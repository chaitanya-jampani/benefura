"""Every safety rejection is a 422: a receipt has no partial result worth keeping, and manual entry exists."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from pypdf import PdfReader

from app.budget import UsageTracker
from app.config import get_settings
from app.errors import ApiError, is_content_filter_error
from app.models.api import Issue
from app.models.plan import Region
from app.models.receipt import FieldConfidence, Receipt
from app.pipelines.chunk import advisory_issues, pii_details
from app.services import content_safety, content_understanding, language_pii, prompt_shields
from app.telemetry import record_pii_hits, record_safety_event

logger = logging.getLogger("benefura.receipt")

SURFACE = "receipt"
LOW_FIELD_CONFIDENCE = 0.6


@dataclass
class ReceiptResult:
    receipt: Receipt
    field_confidence: dict[str, FieldConfidence]
    issues: list[Issue] = field(default_factory=list)


def pdf_page_images(data: bytes, max_pages: int) -> list[bytes]:
    """The browser rasterizes each receipt page into one embedded image."""
    reader = PdfReader(io.BytesIO(data))
    images: list[bytes] = []
    for page in reader.pages[:max_pages]:
        for image in page.images:
            images.append(image.data)
    return images


async def analyze_receipt(data: bytes, content_type: str, *, region: Region, tracker: UsageTracker) -> ReceiptResult:
    settings = get_settings()
    images = pdf_page_images(data, settings.max_receipt_pages) if content_type == "application/pdf" else [data]
    if not images:
        raise ApiError("invalid_request", "The PDF has no page images. Upload the redacted image version.")

    with tracker.step("content_safety", images=len(images)):
        for image in images:
            safety = await content_safety.analyze_image(image)
            tracker.content_safety_records += safety.records
            if safety.unsafe:
                record_safety_event("unsafe_image", SURFACE)
                raise ApiError("unsafe_image", "This image was flagged by content moderation and cannot be analyzed.")

    try:
        with tracker.step("cu_receipt"):
            extraction = await content_understanding.analyze_receipt(data, content_type, region)
    except Exception as exc:
        if not is_content_filter_error(exc):
            raise
        record_safety_event("content_filter", SURFACE)
        raise ApiError(
            "content_filtered", "The AI safety filter blocked this receipt. Enter the details manually."
        ) from exc
    tracker.cu_pages += extraction.cu_pages
    tracker.cu_field_pages += extraction.cu_pages

    texts = [p.markdown for p in extraction.pages]
    page_numbers = [p.page for p in extraction.pages]
    with tracker.step("prompt_shields"):
        shields = await prompt_shields.shield_documents(texts, page_numbers=page_numbers)
    tracker.content_safety_records += shields.records
    if any(shields.document_attacks):
        record_safety_event("prompt_injection", SURFACE)
        raise ApiError(
            "prompt_injection", "This image contains hidden instructions and was rejected. Enter the details manually."
        )

    with tracker.step("pii"):
        pii = await language_pii.check_pii(texts, page_numbers=page_numbers)
    tracker.language_records += pii.records
    record_pii_hits(pii.categories("hard"), "hard", SURFACE)
    record_pii_hits(pii.categories("advisory"), "advisory", SURFACE)
    if pii.hard:
        raise ApiError(
            "pii_detected",
            "A sensitive identifier is still visible on the receipt. Redact it and resend.",
            details=pii_details(pii, extraction.pages),
        )

    issues = advisory_issues(pii, page_numbers)
    for name in extraction.missing:
        if name == "currency" and extraction.receipt.currency:
            message = f"Currency not printed; assumed {extraction.receipt.currency} from the plan region."
            issues.append(Issue(code="assumed_default", severity="info", message=message))
        else:
            issues.append(
                Issue(code="field_missing", severity="info", message=f"{name} was not found; enter it manually.")
            )
    for name, fc in sorted(extraction.field_confidence.items()):
        if fc.confidence < LOW_FIELD_CONFIDENCE:
            issues.append(Issue(code="low_confidence", severity="info", message=f"Check {name} (low confidence)."))
    return ReceiptResult(extraction.receipt, extraction.field_confidence, issues)
