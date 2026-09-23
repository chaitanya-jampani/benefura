from __future__ import annotations

import json
from pathlib import Path

from knowledge.ingest.__main__ import FIXTURES_DIR, OFFLINE_MANIFEST, main
from knowledge.ingest.convert import LocalPdfConverter
from knowledge.ingest.embed import EMBEDDING_DIMENSIONS, FakeEmbedder, batches
from knowledge.ingest.enrich import FakeEnricher
from knowledge.ingest.fetch import FixtureFetcher
from knowledge.ingest.pipeline import Components, ingest_source, run_ingest
from knowledge.ingest.push import EXPECTED_FIELDS, MemoryIndexStore
from knowledge.ingest.sources import EXCERPT_MAX_TOKENS, load_manifest, select_sources
from knowledge.ingest.tokens import count_tokens


class SpyFetcher(FixtureFetcher):
    def __init__(self, fixtures_dir: Path) -> None:
        super().__init__(fixtures_dir)
        self.fetched: list[str] = []

    async def fetch(self, source):
        self.fetched.append(source.id)
        return await super().fetch(source)


def components(store: MemoryIndexStore | None = None, fetcher=None) -> Components:
    return Components(
        fetcher or SpyFetcher(FIXTURES_DIR),
        LocalPdfConverter(),
        FakeEnricher(),
        FakeEmbedder(),
        store or MemoryIndexStore(),
    )


def test_cli_offline_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report_path = tmp_path / "report.json"
    assert main(["--dry-run", "--offline", "--report-json", str(report_path)]) == 0
    data = json.loads(report_path.read_text())
    assert data["summary"]["mode"] == "offline"
    assert data["summary"]["docsFetched"] == 3
    assert data["summary"]["docsFailed"] == 0
    assert data["summary"]["linkOnly"] == 1
    assert data["summary"]["chunks"] >= 3
    assert data["summary"]["indexDocumentCount"] == data["summary"]["chunks"]
    assert "Knowledge ingestion report (offline)" in summary.read_text()
    assert "Knowledge ingestion report" in capsys.readouterr().out


def test_cli_rejects_offline_without_dry_run():
    import pytest

    with pytest.raises(SystemExit):
        main(["--offline"])


async def test_link_only_sources_are_never_fetched_or_indexed():
    indexable, link_only = select_sources(load_manifest(OFFLINE_MANIFEST))
    parts = components()
    report = await run_ingest(indexable, link_only, parts, mode="offline")
    assert "offline-link-only" not in parts.fetcher.fetched  # type: ignore[attr-defined]
    store: MemoryIndexStore = parts.store  # type: ignore[assignment]
    assert all(not doc_id.startswith("offline-link-only") for doc_id in store.docs)
    assert {s.source_id: s.status for s in report.sources}["offline-link-only"] == "link_only"


async def test_documents_match_the_index_schema_and_carry_licence_fields():
    indexable, link_only = select_sources(load_manifest(OFFLINE_MANIFEST))
    parts = components()
    await run_ingest(indexable, link_only, parts, mode="offline")
    store: MemoryIndexStore = parts.store  # type: ignore[assignment]
    assert store.docs
    for doc in store.docs.values():
        assert set(doc) == set(EXPECTED_FIELDS)
        assert len(doc["contentVector"]) == EMBEDDING_DIMENSIONS
        assert doc["license"] == "Synthetic test content"
        assert doc["attribution"] and doc["region"] in ("CA", "AU")
        assert doc["lastFetched"].endswith("Z")
    pdf_docs = [d for d in store.docs.values() if d["url"].endswith(".pdf")]
    assert pdf_docs and all(count_tokens(d["content"]) <= EXCERPT_MAX_TOKENS for d in pdf_docs)


async def test_rerun_keeps_unchanged_chunks_and_deletes_stale_ones():
    manifest = load_manifest(OFFLINE_MANIFEST)
    source = next(s for s in manifest.sources if s.id == "offline-au-waiting-periods")
    store = MemoryIndexStore()
    await store.ensure_index(__import__("knowledge.ingest.push", fromlist=["x"]).load_index_schema())
    first = await ingest_source(source, components(store))
    assert first.uploaded == first.chunks > 0

    stale = dict(next(iter(store.docs.values())))
    stale["id"] = "offline-au-waiting-periods-00000000000000000000"
    await store.upload([stale])

    second = await ingest_source(source, components(store))
    assert second.uploaded == 0
    assert second.unchanged_chunks == first.chunks
    assert second.deleted == 1
    assert "offline-au-waiting-periods-00000000000000000000" not in store.docs


async def test_fetch_failures_are_reported_not_raised(tmp_path):
    manifest = load_manifest(OFFLINE_MANIFEST)
    indexable, link_only = select_sources(manifest)
    parts = components(fetcher=FixtureFetcher(tmp_path))  # empty dir: every fixture missing
    report = await run_ingest(indexable, link_only, parts, mode="offline")
    assert report.docs_failed == len(indexable)
    assert all(s.error == "fixture_missing" for s in report.sources if s.status == "failed")


async def test_fake_enricher_and_embedder_are_deterministic():
    tags = await FakeEnricher().enrich("Waiting periods", "Hospital", "A waiting period applies to hospital cover.")
    assert "waiting periods" in tags.topics and "hospital" in tags.benefitCategories
    v1, v2 = await FakeEmbedder().embed(["same", "same"])
    assert v1 == v2 and abs(sum(x * x for x in v1) - 1) < 1e-9


def test_embedding_batches_respect_caps():
    texts = ["word " * 1000] * 10
    groups = batches(texts, max_items=4, max_tokens=2500)
    assert [i for g in groups for i in g] == list(range(10))
    assert all(len(g) <= 4 for g in groups)
    assert all(sum(count_tokens(texts[i]) for i in g) <= 2500 or len(g) == 1 for g in groups)
