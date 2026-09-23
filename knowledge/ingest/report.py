"""Ingestion report. It carries counts, statuses and error codes only, never page content."""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from knowledge.ingest.push import IndexStats

logger = logging.getLogger(__name__)

EVENT_NAME = "KnowledgeIngestReport"
SourceStatus = Literal["changed", "unchanged", "failed", "link_only"]


@dataclass
class SourceReport:
    source_id: str
    region: str
    status: SourceStatus
    reproduction: str = "verbatim"
    converter: str | None = None
    error: str | None = None
    cu_pages: int = 0
    chunks: int = 0
    empty_chunks: int = 0
    uploaded: int = 0
    unchanged_chunks: int = 0
    deleted: int = 0
    audiences: dict[str, int] = field(default_factory=dict)


@dataclass
class IngestReport:
    started_at: str
    mode: Literal["live", "dry-run", "offline"]
    finished_at: str | None = None
    sources: list[SourceReport] = field(default_factory=list)
    index_stats: IndexStats | None = None
    index_name: str = "public-knowledge-v1"

    def _count(self, status: SourceStatus) -> int:
        return sum(1 for s in self.sources if s.status == status)

    @property
    def docs_fetched(self) -> int:
        return sum(1 for s in self.sources if s.status in ("changed", "unchanged"))

    @property
    def docs_changed(self) -> int:
        return self._count("changed")

    @property
    def docs_unchanged(self) -> int:
        return self._count("unchanged")

    @property
    def docs_failed(self) -> int:
        return self._count("failed")

    @property
    def link_only(self) -> int:
        return self._count("link_only")

    @property
    def cu_pages(self) -> int:
        return sum(s.cu_pages for s in self.sources)

    @property
    def chunk_count(self) -> int:
        return sum(s.chunks for s in self.sources)

    @property
    def empty_chunk_rate(self) -> float:
        candidates = sum(s.chunks + s.empty_chunks for s in self.sources)
        return (sum(s.empty_chunks for s in self.sources) / candidates) if candidates else 0.0

    def summary(self) -> dict[str, int | float | str]:
        stats = self.index_stats
        return {
            "mode": self.mode,
            "docsFetched": self.docs_fetched,
            "docsChanged": self.docs_changed,
            "docsUnchanged": self.docs_unchanged,
            "docsFailed": self.docs_failed,
            "linkOnly": self.link_only,
            "cuPages": self.cu_pages,
            "chunks": self.chunk_count,
            "emptyChunkRate": round(self.empty_chunk_rate, 4),
            "uploaded": sum(s.uploaded for s in self.sources),
            "deleted": sum(s.deleted for s in self.sources),
            "indexDocumentCount": stats.document_count if stats else -1,
            "indexStorageBytes": stats.storage_size if stats else -1,
            "indexVectorBytes": stats.vector_index_size if stats else -1,
        }

    def to_json(self) -> str:
        return json.dumps({"summary": self.summary(), "report": asdict(self)}, indent=2)


def render_markdown(report: IngestReport) -> str:
    s = report.summary()
    lines = [
        f"## Knowledge ingestion report ({report.mode})",
        "",
        f"Index `{report.index_name}` · started {report.started_at} · finished {report.finished_at or '—'}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Documents fetched | {s['docsFetched']} |",
        f"| Changed | {s['docsChanged']} |",
        f"| Unchanged | {s['docsUnchanged']} |",
        f"| Failed | {s['docsFailed']} |",
        f"| Link-only (not indexed) | {s['linkOnly']} |",
        f"| Content Understanding pages | {s['cuPages']} |",
        f"| Chunks | {s['chunks']} |",
        f"| Empty-chunk rate | {float(s['emptyChunkRate']):.1%} |",
        f"| Uploaded / deleted | {s['uploaded']} / {s['deleted']} |",
    ]
    if report.index_stats:
        st = report.index_stats
        lines += [
            f"| Index documents | {st.document_count} |",
            f"| Index storage | {st.storage_size / 1024:.1f} KiB |",
            f"| Vector index size | {st.vector_index_size / 1024:.1f} KiB |",
        ]
    lines += [
        "",
        "| Source | Region | Status | Reproduction | Chunks | New | Kept | Deleted | CU pages | Note |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for src in report.sources:
        note = src.error or (src.converter or "")
        lines.append(
            f"| `{src.source_id}` | {src.region} | {src.status} | {src.reproduction} | {src.chunks} | "
            f"{src.uploaded} | {src.unchanged_chunks} | {src.deleted} | {src.cu_pages} | {note} |"
        )
    failures = Counter(s.error for s in report.sources if s.status == "failed")
    if failures:
        lines += ["", "Failures: " + ", ".join(f"{code} × {n}" for code, n in sorted(failures.items()))]
    return "\n".join(lines) + "\n"


def write_step_summary(markdown: str, env: dict[str, str] | None = None) -> Path | None:
    target = (env if env is not None else os.environ).get("GITHUB_STEP_SUMMARY")
    if not target:
        return None
    path = Path(target)
    with path.open("a", encoding="utf-8") as f:
        f.write(markdown)
    return path


def emit_app_insights_event(report: IngestReport, connection_string: str | None = None) -> bool:
    """App Insights custom event, sent as a log record with the ``microsoft.custom_event.name`` attribute."""
    conn = connection_string or os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set; skipping custom event")
        return False
    from azure.identity import DefaultAzureCredential
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry._logs import get_logger_provider

    # M0-verify: Entra ingestion from the CI identity (Monitoring Metrics Publisher, local auth disabled).
    configure_azure_monitor(
        connection_string=conn,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        logger_name="benefura.knowledge.events",
        enable_live_metrics=False,
    )
    event_logger = logging.getLogger("benefura.knowledge.events")
    event_logger.setLevel(logging.INFO)
    event_logger.info(EVENT_NAME, extra={"microsoft.custom_event.name": EVENT_NAME, **report.summary()})
    provider = get_logger_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        flush()
    return True
