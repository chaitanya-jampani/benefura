"""Orchestrates fetch → convert → chunk → licence filter → enrich → embed → push → report."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from knowledge.ingest.chunk import chunk_markdown
from knowledge.ingest.convert import ConvertedDocument, PdfConverter, html_to_markdown
from knowledge.ingest.embed import Embedder, embedding_text
from knowledge.ingest.enrich import Enricher
from knowledge.ingest.fetch import Fetcher, FetchResult, utc_now
from knowledge.ingest.push import IndexStore, chunk_id, content_hash, load_index_schema
from knowledge.ingest.report import IngestReport, SourceReport
from knowledge.ingest.sources import Source, apply_licence

logger = logging.getLogger(__name__)


@dataclass
class Components:
    fetcher: Fetcher
    pdf_converter: PdfConverter
    enricher: Enricher
    embedder: Embedder
    store: IndexStore


async def convert(fetched: FetchResult, source: Source, pdf_converter: PdfConverter) -> ConvertedDocument:
    assert fetched.body is not None
    is_pdf = source.format == "pdf" or fetched.content_type == "application/pdf" or fetched.body[:5] == b"%PDF-"
    if is_pdf:
        return await pdf_converter.convert(fetched.body)
    return html_to_markdown(fetched.body)


async def ingest_source(source: Source, parts: Components, *, force: bool = False) -> SourceReport:
    report = SourceReport(source.id, source.region, "link_only", reproduction=source.reproduction)
    if not source.index:
        return report

    fetched = await parts.fetcher.fetch(source)
    if not fetched.ok:
        report.status, report.error = "failed", fetched.error or "fetch_failed"
        return report
    url = str(source.url)
    existing = await parts.store.existing_ids(url)
    if fetched.status == "unchanged" and existing and not force:
        report.status, report.chunks, report.unchanged_chunks = "unchanged", len(existing), len(existing)
        await parts.store.touch(sorted(existing), fetched.fetched_at)
        return report

    try:
        doc = await convert(fetched, source, parts.pdf_converter)
    except Exception as exc:  # one bad source must not stop the run
        logger.exception("convert failed for %s", source.id)
        report.status, report.error = "failed", f"convert_{type(exc).__name__}"
        return report
    report.converter, report.cu_pages = doc.converter, doc.cu_pages

    chunked = chunk_markdown(doc.markdown)
    report.empty_chunks = chunked.empty
    chunks = apply_licence(source, chunked.chunks)

    by_id: dict[str, Any] = {}
    for chunk in chunks:
        by_id.setdefault(chunk_id(source.id, chunk.section, chunk.content), chunk)
    ids = set(by_id)
    new_ids = sorted(ids - existing, key=lambda i: by_id[i].index)
    kept_ids = sorted(ids & existing)
    stale_ids = sorted(existing - ids)

    documents: list[dict[str, Any]] = []
    if new_ids:
        title = source.title  # the manifest title citations show, not the page <title>
        enrichments = await asyncio.gather(
            *(parts.enricher.enrich(title, by_id[i].section, by_id[i].content) for i in new_ids)
        )
        vectors = await parts.embedder.embed(
            [embedding_text(title, by_id[i].section, by_id[i].content) for i in new_ids]
        )
        audiences: Counter[str] = Counter()
        for cid, enrichment, vector in zip(new_ids, enrichments, vectors, strict=True):
            chunk = by_id[cid]
            tags = enrichment.normalized(source.topics)
            audiences[tags.audience] += 1
            documents.append(
                {
                    "id": cid,
                    "content": chunk.content,
                    "title": title,
                    "url": url,
                    "region": source.region,
                    "publisher": source.publisher,
                    "license": source.licence,
                    "attribution": source.attribution,
                    "section": chunk.section,
                    "topics": tags.topics,
                    "categories": list(tags.benefitCategories),
                    "contentVector": vector,
                    "contentHash": content_hash(chunk.content),
                    "lastFetched": fetched.fetched_at,
                }
            )
        report.audiences = dict(audiences)

    report.uploaded = await parts.store.upload(documents) if documents else 0
    if kept_ids:
        await parts.store.touch(kept_ids, fetched.fetched_at)
    report.deleted = await parts.store.delete(stale_ids) if stale_ids else 0
    report.unchanged_chunks = len(kept_ids)
    report.chunks = len(ids)
    report.status = "changed" if (new_ids or stale_ids or fetched.status == "changed") else "unchanged"
    return report


async def run_ingest(
    sources: list[Source],
    link_only: list[Source],
    parts: Components,
    *,
    mode: str,
    force: bool = False,
    create_index: bool = True,
) -> IngestReport:
    report = IngestReport(started_at=utc_now(), mode=mode)  # type: ignore[arg-type]
    if create_index:
        await parts.store.ensure_index(load_index_schema())
    for source in sources:  # sequential to stay polite to publishers
        try:
            report.sources.append(await ingest_source(source, parts, force=force))
        except Exception as exc:
            logger.exception("ingest failed for %s", source.id)
            report.sources.append(
                SourceReport(source.id, source.region, "failed", source.reproduction, error=type(exc).__name__)
            )
    report.sources.extend(SourceReport(s.id, s.region, "link_only", s.reproduction) for s in link_only)
    try:
        report.index_stats = await parts.store.statistics()
    except Exception as exc:
        logger.warning("index statistics unavailable: %s", type(exc).__name__)
    report.finished_at = utc_now()
    return report
