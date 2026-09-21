"""Parsing works on the REST JSON shape (``AnalysisResult.as_dict()``) so recorded responses test it directly."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.errors import ApiError, ContentFilteredError, is_content_filter_error
from app.fake_hooks import current_hooks
from app.models.plan import Region
from app.models.receipt import FieldConfidence, Receipt, ReceiptLine
from app.telemetry import record_cu_pages

if TYPE_CHECKING:
    from azure.ai.contentunderstanding.aio import ContentUnderstandingClient

logger = logging.getLogger("benefura.cu")

LAYOUT_ANALYZER = "prebuilt-layout"
GOLDEN_DOC_BY_REGION: dict[str, str] = {"CA": "ca-northwind", "AU": "au-wattle"}
_SOURCE_RE = re.compile(r"D\((\d+),([^)]*)\)")


@dataclass(frozen=True)
class LayoutWord:
    content: str
    offset: int  # relative to the page markdown
    length: int
    polygon: tuple[float, ...] | None  # x1,y1,…,x4,y4 in page units (inches for PDF)
    confidence: float | None = None


@dataclass
class LayoutPage:
    page: int  # booklet page number, not the chunk page index
    markdown: str
    words: list[LayoutWord] = field(default_factory=list)
    unit: str | None = None


@dataclass
class LayoutResult:
    pages: list[LayoutPage]
    cu_pages: int


@dataclass
class ReceiptExtraction:
    receipt: Receipt
    field_confidence: dict[str, FieldConfidence]
    pages: list[LayoutPage]
    cu_pages: int
    missing: list[str] = field(default_factory=list)


def parse_source_polygon(source: str | None) -> tuple[int, tuple[float, ...]] | None:
    """``D(page,x1,y1,…,x4,y4)`` → (page, polygon). Axis-aligned ``D(page,x,y,w,h)`` becomes a polygon too."""
    if not source:
        return None
    match = _SOURCE_RE.search(source)
    if not match:
        return None
    try:
        numbers = [float(n) for n in match.group(2).split(",") if n.strip()]
    except ValueError:
        return None
    if len(numbers) == 8:
        return int(match.group(1)), tuple(numbers)
    if len(numbers) == 4:  # M0-verify: CU emits polygons; keep the box form for robustness
        x, y, w, h = numbers
        return int(match.group(1)), (x, y, x + w, y, x + w, y + h, x, y + h)
    return None


def union_polygon(polygons: Sequence[Sequence[float]]) -> list[float] | None:
    xs = [p[i] for p in polygons for i in range(0, len(p), 2)]
    ys = [p[i] for p in polygons for i in range(1, len(p), 2)]
    if not xs or not ys:
        return None
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    return [round(v, 4) for v in (x1, y1, x2, y1, x2, y2, x1, y2)]


def words_polygon(page: LayoutPage, offset: int, length: int) -> list[float] | None:
    end = offset + max(length, 1)
    hits = [w.polygon for w in page.words if w.polygon and w.offset < end and offset < w.offset + w.length]
    return union_polygon([p for p in hits if p])


def _page_markdown(markdown: str, spans: Sequence[Mapping[str, Any]]) -> tuple[str, list[tuple[int, int, int]]]:
    """Returns the text and an index of (abs_offset, length, rel_offset) per span."""
    parts: list[str] = []
    index: list[tuple[int, int, int]] = []
    rel = 0
    for span in spans:
        offset, length = int(span.get("offset", 0)), int(span.get("length", 0))
        parts.append(markdown[offset : offset + length])
        index.append((offset, length, rel))
        rel += length
    return "".join(parts), index


def _relative(offset: int, index: list[tuple[int, int, int]]) -> int | None:
    for abs_offset, length, rel in index:
        if abs_offset <= offset < abs_offset + length:
            return rel + offset - abs_offset
    return None


def parse_layout(result: Mapping[str, Any], booklet_pages: Sequence[int] | None = None) -> list[LayoutPage]:
    pages: list[LayoutPage] = []
    for content in result.get("contents") or []:
        if content.get("kind", "document") != "document":
            continue
        markdown: str = content.get("markdown") or ""
        unit = content.get("unit")
        raw_pages = content.get("pages") or []
        if not raw_pages:
            chunks = markdown.split("<!-- PageBreak -->")
            raw_pages = [{"pageNumber": i + 1, "_markdown": chunk} for i, chunk in enumerate(chunks)]
        for raw in raw_pages:
            number = int(raw.get("pageNumber", len(pages) + 1))
            if "_markdown" in raw:
                text, index = raw["_markdown"], []
            else:
                text, index = _page_markdown(markdown, raw.get("spans") or [])
            words: list[LayoutWord] = []
            for word in raw.get("words") or []:
                span = word.get("span") or {}
                rel = _relative(int(span.get("offset", -1)), index)
                if rel is None:
                    continue
                source = parse_source_polygon(word.get("source"))
                words.append(
                    LayoutWord(
                        content=str(word.get("content", "")),
                        offset=rel,
                        length=int(span.get("length", 0)),
                        polygon=source[1] if source else None,
                        confidence=word.get("confidence"),
                    )
                )
            mapped = booklet_pages[number - 1] if booklet_pages and 0 < number <= len(booklet_pages) else number
            pages.append(LayoutPage(page=mapped, markdown=text.strip("\n"), words=words, unit=unit))
    return pages


_VALUE_KEYS = ("valueString", "valueDate", "valueNumber", "valueInteger", "valueBoolean", "valueTime", "valueJson")


def field_value(raw: Mapping[str, Any] | None) -> Any:
    if not raw:
        return None
    for key in _VALUE_KEYS:
        if key in raw:
            return raw[key]
    if "valueArray" in raw:
        return raw["valueArray"]
    if "valueObject" in raw:
        return raw["valueObject"]
    return None


def to_cents(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).replace("$", "").replace(",", "").strip()
        cents = (Decimal(text) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int(cents) if cents >= 0 else None


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _currency(value: Any, region: Region) -> tuple[str | None, bool]:
    """Returns (currency, assumed)."""
    text = str(value or "").strip().upper()
    if text in ("CAD", "AUD"):
        return text, False
    if text in ("$", "DOLLAR", "DOLLARS", "C$", "CA$", "A$", "AU$", ""):
        return ("CAD" if region == "CA" else "AUD"), True
    return None, False


def _confidence(raw: Mapping[str, Any] | None) -> FieldConfidence | None:
    if not raw or raw.get("confidence") is None:
        return None
    return FieldConfidence(confidence=max(0.0, min(1.0, float(raw["confidence"]))), source=raw.get("source"))


def parse_receipt(result: Mapping[str, Any], region: Region) -> ReceiptExtraction:
    contents = [c for c in result.get("contents") or [] if c.get("kind", "document") == "document"]
    fields: Mapping[str, Any] = (contents[0].get("fields") if contents else None) or {}
    confidence: dict[str, FieldConfidence] = {}
    missing: list[str] = []

    def scalar(name: str, target: str) -> Any:
        raw = fields.get(name)
        value = field_value(raw)
        if (fc := _confidence(raw)) is not None:
            confidence[target] = fc
        if value in (None, ""):
            missing.append(target)
        return value

    lines: list[ReceiptLine] = []
    for i, item in enumerate(field_value(fields.get("serviceLines")) or []):
        obj = field_value(item) or {}
        line = ReceiptLine(
            serviceDate=_to_date(field_value(obj.get("serviceDate"))),
            description=field_value(obj.get("description")),
            itemCode=(str(v) if (v := field_value(obj.get("itemCode"))) is not None else None),
            quantity=_to_float(field_value(obj.get("quantity"))),
            amountCents=to_cents(field_value(obj.get("amount"))),
        )
        for name, target in (
            ("serviceDate", "serviceDate"),
            ("description", "description"),
            ("itemCode", "itemCode"),
            ("quantity", "quantity"),
            ("amount", "amountCents"),
        ):
            if (fc := _confidence(obj.get(name))) is not None:
                confidence[f"serviceLines[{i}].{target}"] = fc
        lines.append(line)
    if not lines:
        missing.append("serviceLines")

    currency_raw = scalar("currency", "currency")
    currency, assumed = _currency(currency_raw, region)
    if assumed and "currency" not in missing:
        missing.append("currency")
    receipt = Receipt(
        providerName=scalar("providerName", "providerName"),
        providerType=scalar("providerType", "providerType"),
        providerRegistrationNo=(str(v) if (v := scalar("providerRegistrationNo", "providerRegistrationNo")) else None),
        serviceLines=lines,
        totalCents=to_cents(scalar("total", "totalCents")),
        insurerPaidCents=to_cents(scalar("insurerPaid", "insurerPaidCents")),
        currency=currency,  # type: ignore[arg-type]
    )
    pages = parse_layout(result)
    cu_pages = sum(
        max(1, int(c.get("endPageNumber") or 1) - int(c.get("startPageNumber") or 1) + 1) for c in contents
    ) or max(1, len(pages))
    # insurerPaid is usually absent on a provider receipt; only EOBs carry it.
    missing = [m for m in dict.fromkeys(missing) if m != "insurerPaidCents"]
    return ReceiptExtraction(receipt, confidence, pages, cu_pages, missing)


@lru_cache
def get_cu_client() -> ContentUnderstandingClient:
    from azure.ai.contentunderstanding.aio import ContentUnderstandingClient

    from app.services.foundry import get_credential

    settings = get_settings()
    if not settings.ai_services_endpoint:
        raise RuntimeError("AI_SERVICES_ENDPOINT is not set")
    # M0-verify: CU accepts the cognitiveservices.azure.com host of the AIServices account (keyless).
    return ContentUnderstandingClient(
        endpoint=settings.ai_services_endpoint.rstrip("/"),
        credential=get_credential(),
        api_version=settings.cu_api_version,
    )


async def close_cu_client() -> None:
    if get_cu_client.cache_info().currsize:
        await get_cu_client().close()
    get_cu_client.cache_clear()


async def _analyze(analyzer_id: str, data: bytes, content_type: str) -> dict[str, Any]:
    from azure.core.exceptions import HttpResponseError

    client = get_cu_client()
    poller = None
    try:
        poller = await client.begin_analyze_binary(analyzer_id, data, content_type=content_type)
        result = await poller.result()
        return result.as_dict()
    except HttpResponseError as exc:
        if is_content_filter_error(exc):
            raise ContentFilteredError("Content Understanding blocked by the content filter") from exc
        logger.error("Content Understanding %s failed (HTTP %s)", analyzer_id, exc.status_code)
        raise ApiError("upstream_error", "Document analysis failed; try again shortly.") from exc
    finally:
        if poller is not None:
            await _delete_result(client, poller)


async def _delete_result(client: ContentUnderstandingClient, poller: Any) -> None:
    """The service otherwise keeps results for up to 24 h."""
    try:
        await client.delete_result(poller.operation_id)
    except Exception as exc:
        logger.warning("Could not delete analyze result (%s)", type(exc).__name__)


def _golden_pages(region: Region) -> dict[int, str]:
    path = get_settings().samples_dir / "golden" / f"{GOLDEN_DOC_BY_REGION[region]}.pages.json"
    if not path.exists():
        raise ApiError("upstream_error", f"Fake mode needs {path.name} in the samples directory.")
    return {int(p["page"]): str(p.get("markdown") or "") for p in json.loads(path.read_text())}


async def analyze_layout(pdf: bytes, *, booklet_pages: Sequence[int], region: Region) -> LayoutResult:
    settings = get_settings()
    if settings.ai_mode != "live":
        golden = _golden_pages(region)
        pages = [LayoutPage(page=p, markdown=golden.get(p, "")) for p in booklet_pages]
        record_cu_pages(LAYOUT_ANALYZER, len(pages))
        return LayoutResult(pages=pages, cu_pages=len(pages))
    # M0-verify: prebuilt-layout returns page words (returnDetails) so hard PII hits can carry a polygon.
    raw = await _analyze(LAYOUT_ANALYZER, pdf, "application/pdf")
    pages = parse_layout(raw, booklet_pages)
    cu_pages = max(len(pages), len(booklet_pages))
    record_cu_pages(LAYOUT_ANALYZER, cu_pages)
    return LayoutResult(pages=pages, cu_pages=cu_pages)


async def analyze_receipt(data: bytes, content_type: str, region: Region) -> ReceiptExtraction:
    settings = get_settings()
    if settings.ai_mode != "live":
        if current_hooks().content_filter:
            raise ContentFilteredError("fake content filter")
        extraction = parse_receipt(fake_receipt_result(region), region)
    else:
        extraction = parse_receipt(await _analyze(settings.cu_receipt_analyzer_id, data, content_type), region)
    record_cu_pages(settings.cu_receipt_analyzer_id, extraction.cu_pages)
    return extraction


FAKE_RECEIPTS: dict[str, dict[str, Any]] = {
    "CA": {
        "providerName": "Harbourfront Massage Therapy",
        "providerType": "Registered massage therapist",
        "providerRegistrationNo": "RMT-00000",
        "serviceLines": [{"description": "Massage therapy, 60 minutes", "itemCode": None, "amountCents": 9500}],
        "totalCents": 9500,
        "insurerPaidCents": None,
        "currency": "CAD",
    },
    "AU": {
        "providerName": "Wattle Street Physiotherapy",
        "providerType": "Physiotherapist",
        "providerRegistrationNo": "PHY0000000000",
        "serviceLines": [{"description": "Initial consultation", "itemCode": "500", "amountCents": 8500}],
        "totalCents": 8500,
        "insurerPaidCents": None,
        "currency": "AUD",
    },
}


def _fake_receipt(region: Region) -> dict[str, Any]:
    """A non-injection sample receipt, else a built-in one dated today so it falls in the current benefit year."""
    path = get_settings().samples_dir / "golden" / "receipts.expected.json"
    if path.exists():
        for entry in json.loads(path.read_text()).get("receipts", []):
            if entry.get("region") == region and not entry.get("injection") and entry.get("receipt"):
                return entry["receipt"]
    receipt = json.loads(json.dumps(FAKE_RECEIPTS[region]))
    for line in receipt["serviceLines"]:
        line.update(serviceDate=date.today().isoformat(), quantity=1)
    return receipt


def fake_receipt_result(region: Region) -> dict[str, Any]:
    receipt = _fake_receipt(region)
    top, bottom = "D(1,0.8,1.0,4.2,1.0,4.2,1.3,0.8,1.3)", "D(1,5.6,6.1,6.6,6.1,6.6,6.4,5.6,6.4)"

    def s(value: Any, conf: float) -> dict[str, Any] | None:
        return (
            None if value is None else {"type": "string", "valueString": str(value), "confidence": conf, "source": top}
        )

    def n(cents: Any, conf: float) -> dict[str, Any] | None:
        return (
            None
            if cents is None
            else {"type": "number", "valueNumber": cents / 100, "confidence": conf, "source": bottom}
        )

    lines: list[dict[str, Any]] = []
    rows: list[str] = []
    for line in receipt.get("serviceLines", []):
        fields = {
            "serviceDate": {"type": "date", "valueDate": line.get("serviceDate"), "confidence": 0.88},
            "description": s(line.get("description"), 0.9),
            "itemCode": s(line.get("itemCode"), 0.87),
            "quantity": {"type": "number", "valueNumber": line.get("quantity") or 1, "confidence": 0.85},
            "amount": n(line.get("amountCents"), 0.91),
        }
        lines.append({"type": "object", "valueObject": {k: v for k, v in fields.items() if v is not None}})
        amount = (line.get("amountCents") or 0) / 100
        rows.append(f"| {line.get('serviceDate')} | {line.get('description')} | ${amount:.2f} |")
    total = (receipt.get("totalCents") or 0) / 100
    markdown = "\n\n".join(
        [
            f"# {receipt.get('providerName')}",
            f"{receipt.get('providerType')} · Registration [ID_1]",
            "Patient: [MEMBER_A]",
            "| Date | Service | Amount |\n|---|---|---|\n" + "\n".join(rows),
            f"Total: ${total:.2f} {receipt.get('currency') or ''}".strip(),
        ]
    )
    top_fields = {
        "providerName": s(receipt.get("providerName"), 0.95),
        "providerType": s(receipt.get("providerType"), 0.8),
        "providerRegistrationNo": s(receipt.get("providerRegistrationNo"), 0.55),
        "serviceLines": {"type": "array", "valueArray": lines},
        "total": n(receipt.get("totalCents"), 0.94),
        "insurerPaid": n(receipt.get("insurerPaidCents"), 0.9),
        "currency": s(receipt.get("currency"), 0.7),
    }
    return {
        "analyzerId": "benefura-receipt",
        "contents": [
            {
                "kind": "document",
                "markdown": markdown,
                "startPageNumber": 1,
                "endPageNumber": 1,
                "unit": "pixel",
                "pages": [{"pageNumber": 1, "spans": [{"offset": 0, "length": len(markdown)}], "words": []}],
                "fields": {k: v for k, v in top_fields.items() if v is not None},
            }
        ],
    }
