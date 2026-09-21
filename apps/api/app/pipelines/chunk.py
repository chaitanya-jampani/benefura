from __future__ import annotations

from dataclasses import dataclass, field

from app.budget import UsageTracker
from app.config import get_settings
from app.errors import ApiError, is_content_filter_error
from app.models.api import ExtractedRows, Issue, PiiDetectedDetail
from app.models.plan import Region
from app.pipelines.booklet_workflow import PageText, run_extraction
from app.services import content_understanding, language_pii, prompt_shields
from app.services.content_understanding import LayoutPage, words_polygon
from app.services.language_pii import PiiCheckResult
from app.telemetry import record_pii_hits, record_safety_event

SURFACE = "booklet"
FAKE_PII_POLYGON = (1.0, 1.0, 3.5, 1.0, 3.5, 1.4, 1.0, 1.4)  # inches, top-left of a Letter/A4 page


@dataclass
class ChunkResult:
    rows: ExtractedRows
    issues: list[Issue] = field(default_factory=list)


def pii_details(result: PiiCheckResult, pages: list[LayoutPage]) -> list[PiiDetectedDetail]:
    details: dict[tuple[int, str, tuple[float, ...] | None], PiiDetectedDetail] = {}
    for hit in result.hard:
        page = pages[hit.doc_index]
        polygon = words_polygon(page, hit.offset, hit.length) if page.words else None
        if polygon is None and get_settings().ai_mode == "fake":
            polygon = list(FAKE_PII_POLYGON)  # fake pages have no word geometry; give the browser a box to draw
        key = (page.page, hit.category, tuple(polygon) if polygon else None)
        details.setdefault(key, PiiDetectedDetail(page=page.page, category=hit.category, polygon=polygon))
    return list(details.values())


def advisory_issues(result: PiiCheckResult, pages: list[int]) -> list[Issue]:
    seen: dict[tuple[int, str], Issue] = {}
    for hit in result.advisory:
        page = pages[hit.doc_index]
        seen.setdefault(
            (page, hit.category),
            Issue(
                code="pii_advisory",
                severity="info",
                message=f"Possible {hit.category} detected. Fine for insurer or provider details; "
                "redact it if it is yours.",
                page=page,
                category=hit.category,
            ),
        )
    return list(seen.values())


async def analyze_chunk(pdf: bytes, *, region: Region, pages: list[int], tracker: UsageTracker) -> ChunkResult:
    issues: list[Issue] = []

    with tracker.step("cu_layout", pages=len(pages)):
        layout = await content_understanding.analyze_layout(pdf, booklet_pages=pages, region=region)
    tracker.cu_pages += layout.cu_pages
    for page in layout.pages:
        if not page.markdown.strip():
            issues.append(
                Issue(code="ocr_empty", severity="warning", message="No text was read from this page.", page=page.page)
            )

    texts = [p.markdown for p in layout.pages]
    page_numbers = [p.page for p in layout.pages]
    with tracker.step("pii"):
        pii = await language_pii.check_pii(texts, page_numbers=page_numbers)
    tracker.language_records += pii.records
    record_pii_hits(pii.categories("hard"), "hard", SURFACE)
    record_pii_hits(pii.categories("advisory"), "advisory", SURFACE)
    if pii.hard:
        raise ApiError(
            "pii_detected",
            "A sensitive identifier is still visible. Add a redaction box on the flagged page and resend.",
            details=pii_details(pii, layout.pages),
        )
    issues.extend(advisory_issues(pii, page_numbers))

    with tracker.step("prompt_shields"):
        shields = await prompt_shields.shield_documents(texts, page_numbers=page_numbers)
    tracker.content_safety_records += shields.records
    usable: list[PageText] = []
    for page, attacked in zip(layout.pages, shields.document_attacks, strict=True):
        if attacked:
            record_safety_event("prompt_injection", SURFACE)
            issues.append(
                Issue(
                    code="prompt_injection",
                    severity="warning",
                    message="This page contains text that tries to instruct the assistant.",
                    page=page.page,
                )
            )
            issues.append(
                Issue(
                    code="page_excluded", severity="warning", message="Page excluded from extraction.", page=page.page
                )
            )
        elif page.markdown.strip():
            usable.append(PageText(page=page.page, markdown=page.markdown))

    if not usable:
        return ChunkResult(rows=ExtractedRows(), issues=issues)

    try:
        extraction = await run_extraction(region, usable, tracker)
    except Exception as exc:
        if not is_content_filter_error(exc):
            raise
        # Not an HTTP error: the rest of the booklet still assembles and review shows these pages.
        record_safety_event("content_filter", SURFACE)
        issues.append(
            Issue(
                code="content_filtered",
                severity="warning",
                message="The AI safety filter blocked extraction for these pages; review them manually.",
                page=usable[0].page if len(usable) == 1 else None,
            )
        )
        return ChunkResult(rows=ExtractedRows(), issues=issues)
    return ChunkResult(rows=extraction.rows, issues=[*issues, *extraction.issues])
