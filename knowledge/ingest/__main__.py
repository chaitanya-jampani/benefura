"""CLI: ``uv run python -m knowledge.ingest [--dry-run] [--offline] [--source ID ...]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from knowledge.ingest.convert import ContentUnderstandingPdfConverter, LocalPdfConverter
from knowledge.ingest.embed import FakeEmbedder, FoundryEmbedder
from knowledge.ingest.enrich import FakeEnricher, FoundryEnricher
from knowledge.ingest.fetch import DEFAULT_CACHE_DIR, FixtureFetcher, HttpFetcher
from knowledge.ingest.pipeline import Components, run_ingest
from knowledge.ingest.push import AzureSearchStore, MemoryIndexStore
from knowledge.ingest.report import emit_app_insights_event, render_markdown, write_step_summary
from knowledge.ingest.sources import DEFAULT_MANIFEST, KNOWLEDGE_DIR, load_manifest, select_sources

FIXTURES_DIR = KNOWLEDGE_DIR / "tests" / "fixtures"
OFFLINE_MANIFEST = FIXTURES_DIR / "offline_sources.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m knowledge.ingest", description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="fake enrich/embed/push; nothing is written to Azure")
    p.add_argument("--offline", action="store_true", help="with --dry-run: local fixtures only, no network")
    p.add_argument("--source", action="append", default=[], metavar="ID", help="limit to these source ids")
    p.add_argument("--manifest", type=Path, default=None, help="sources manifest (default: knowledge/sources.yaml)")
    p.add_argument("--force", action="store_true", help="reprocess sources even when unchanged")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    p.add_argument("--report-json", type=Path, default=None, help="also write the report as JSON")
    p.add_argument("--max-failures", type=int, default=0, help="exit non-zero when more sources fail")
    p.add_argument("--no-event", action="store_true", help="do not send the App Insights custom event")
    args = p.parse_args(argv)
    if args.offline and not args.dry_run:
        p.error("--offline requires --dry-run")
    return args


async def main_async(args: argparse.Namespace) -> int:
    manifest_path = args.manifest or (OFFLINE_MANIFEST if args.offline else DEFAULT_MANIFEST)
    indexable, link_only = select_sources(load_manifest(manifest_path), args.source)

    if args.dry_run:
        mode = "offline" if args.offline else "dry-run"
        client = None if args.offline else HttpFetcher.new_client()
        fetcher = FixtureFetcher(FIXTURES_DIR) if args.offline else HttpFetcher(client, args.cache_dir)  # type: ignore[arg-type]
        parts = Components(fetcher, LocalPdfConverter(), FakeEnricher(), FakeEmbedder(), MemoryIndexStore())
    else:
        mode = "live"
        client = HttpFetcher.new_client()
        parts = Components(
            HttpFetcher(client, args.cache_dir),
            ContentUnderstandingPdfConverter(),
            FoundryEnricher(),
            FoundryEmbedder(),
            AzureSearchStore(),
        )
    try:
        report = await run_ingest(indexable, link_only, parts, mode=mode, force=args.force)
    finally:
        await parts.store.close()
        if client is not None:
            await client.aclose()
        if mode == "live":
            from app.services.foundry import close_clients

            await close_clients()

    markdown = render_markdown(report)
    print(markdown)
    write_step_summary(markdown)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(report.to_json(), encoding="utf-8")
    if mode == "live" and not args.no_event:
        emit_app_insights_event(report)
    return 1 if report.docs_failed > args.max_failures else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
