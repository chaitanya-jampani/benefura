from __future__ import annotations

import json

from knowledge.ingest.push import IndexStats
from knowledge.ingest.report import (
    IngestReport,
    SourceReport,
    emit_app_insights_event,
    render_markdown,
    write_step_summary,
)


def sample_report() -> IngestReport:
    return IngestReport(
        started_at="2026-09-16T00:00:00Z",
        finished_at="2026-09-16T00:05:00Z",
        mode="live",
        sources=[
            SourceReport(
                "phio-waiting-periods",
                "AU",
                "changed",
                converter="html",
                chunks=6,
                empty_chunks=1,
                uploaded=4,
                unchanged_chunks=2,
                deleted=1,
            ),
            SourceReport(
                "cra-rc4065-medical-expenses-guide",
                "CA",
                "changed",
                converter="content-understanding",
                cu_pages=40,
                chunks=30,
                uploaded=30,
            ),
            SourceReport("tbs-pshcp-summary", "CA", "unchanged", reproduction="excerpt", chunks=5, unchanged_chunks=5),
            SourceReport("ontario-what-ohip-covers", "CA", "failed", error="http_403"),
            SourceReport("clhia-guide-supplementary-health-insurance", "CA", "link_only", reproduction="excerpt"),
        ],
        index_stats=IndexStats(document_count=41, storage_size=2048 * 1024, vector_index_size=512 * 1024),
    )


def test_counts_and_rates():
    report = sample_report()
    assert report.docs_fetched == 3
    assert report.docs_changed == 2
    assert report.docs_unchanged == 1
    assert report.docs_failed == 1
    assert report.link_only == 1
    assert report.cu_pages == 40
    assert report.chunk_count == 41
    assert report.empty_chunk_rate == 1 / 42


def test_markdown_rendering():
    md = render_markdown(sample_report())
    assert md.startswith("## Knowledge ingestion report (live)")
    assert "| Documents fetched | 3 |" in md
    assert "| Content Understanding pages | 40 |" in md
    assert "| Empty-chunk rate | 2.4% |" in md
    assert "| Index documents | 41 |" in md
    assert "| Index storage | 2048.0 KiB |" in md
    assert "| `ontario-what-ohip-covers` | CA | failed | verbatim | 0 | 0 | 0 | 0 | 0 | http_403 |" in md
    assert "Failures: http_403 × 1" in md


def test_markdown_without_stats_or_candidates():
    report = IngestReport(started_at="t", mode="offline")
    md = render_markdown(report)
    assert "| Empty-chunk rate | 0.0% |" in md
    assert "Index documents" not in md


def test_json_contains_summary_and_no_content():
    data = json.loads(sample_report().to_json())
    assert data["summary"]["docsFailed"] == 1
    assert data["summary"]["indexDocumentCount"] == 41
    assert data["report"]["sources"][0]["source_id"] == "phio-waiting-periods"


def test_step_summary_appends(tmp_path):
    target = tmp_path / "summary.md"
    target.write_text("# Existing\n")
    assert write_step_summary("hello\n", {"GITHUB_STEP_SUMMARY": str(target)}) == target
    assert target.read_text() == "# Existing\nhello\n"
    assert write_step_summary("hello\n", {}) is None


def test_event_is_skipped_without_connection_string(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert emit_app_insights_event(sample_report()) is False
