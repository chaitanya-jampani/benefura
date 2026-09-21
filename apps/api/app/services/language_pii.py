"""Two-tier PII backstop (browser redaction is the control); hits carry categories and offsets, never values."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import get_settings
from app.errors import ApiError
from app.fake_hooks import current_hooks

HARD_TIER_CATEGORIES: frozenset[str] = frozenset(
    {
        "CASocialInsuranceNumber",
        "CAHealthServiceNumber",
        "CAPersonalHealthIdentification",
        "CABankAccountNumber",
        "AUTaxFileNumber",
        "AUMedicalAccountNumber",
        "AUBankAccountNumber",
        "AUDriversLicenseNumber",
        "DateOfBirth",
    }
)
ADVISORY_TIER_CATEGORIES: frozenset[str] = frozenset({"Person", "Organization", "Address", "PhoneNumber", "Email"})

# M0-verify: synchronous analyze-text limits for PII (documents per request, characters per document).
MAX_DOCS_PER_REQUEST = 5
MAX_DOC_CHARS = 5120
TEXT_RECORD_CHARS = 1000


@dataclass(frozen=True)
class PiiHit:
    category: str
    tier: Literal["hard", "advisory"]
    confidence: float
    doc_index: int
    offset: int
    length: int


@dataclass
class PiiCheckResult:
    hard: list[PiiHit] = field(default_factory=list)
    advisory: list[PiiHit] = field(default_factory=list)
    records: int = 0

    def categories(self, tier: Literal["hard", "advisory"]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for hit in self.hard if tier == "hard" else self.advisory:
            counts[hit.category] = counts.get(hit.category, 0) + 1
        return counts


ALIAS_STEMS = (
    "CERTIFICATE",
    "MEMBERSHIP",
    "DEPENDENT",
    "PROVIDER",
    "EMPLOYER",
    "EMPLOYEE",
    "CONTRACT",
    "ACCOUNT",
    "ADDRESS",
    "COMPANY",
    "PATIENT",
    "MEMBER",
    "POLICY",
    "PERSON",
    "SPOUSE",
    "CLINIC",
    "DOCTOR",
    "GROUP",
    "CHILD",
    "PHONE",
    "EMAIL",
    "CARD",
    "CERT",
    "NAME",
    "PLAN",
    "DOB",
    "ORG",
    "ID",
)
ALIAS_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\]")
_STRONG_OPEN, _STRONG_CLOSE = "[({|", "])}|"
_ALIAS_SCAN_RE = re.compile(
    r"(?P<wf>\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\])"
    r"|(?<![A-Za-z0-9_])(?:(?P<open>[\[\(\{\|Il1!])[ ]?)?"
    rf"(?P<stem>{'|'.join(ALIAS_STEMS)})"
    r"(?P<sep>[ ]?[_\-\. ][ ]?|)"
    r"(?P<suffix>[A-Z0-9]{1,2}?)"
    r"(?:[ ]?(?P<close>[\]\)\}\|Il1J!]))?(?![A-Za-z0-9_])"
)


@dataclass(frozen=True)
class NormalizedText:
    """Text with alias tokens repaired, plus a map back to offsets in the original text."""

    text: str
    alias_spans: tuple[tuple[int, int], ...]
    edits: tuple[tuple[int, int, int, int], ...]  # (norm_start, norm_end, orig_start, orig_end)

    def to_original(self, offset: int) -> int:
        delta = 0
        for norm_start, norm_end, orig_start, orig_end in self.edits:
            if offset < norm_start:
                break
            if offset < norm_end:
                return orig_start + min(offset - norm_start, orig_end - orig_start)
            delta = orig_end - norm_end
        return offset + delta

    def alias_overlap(self, start: int, end: int) -> float:
        """Share of ``[start, end)`` (normalized offsets) covered by alias tokens."""
        if end <= start:
            return 0.0
        covered = sum(max(0, min(end, e) - max(start, s)) for s, e in self.alias_spans)
        return covered / (end - start)


def normalize_aliases_mapped(text: str) -> NormalizedText:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    edits: list[tuple[int, int, int, int]] = []
    pos = 0
    out_len = 0
    for match in _ALIAS_SCAN_RE.finditer(text):
        if match.group("wf"):
            token = match.group("wf")
        else:
            open_, close, sep = match.group("open") or "", match.group("close") or "", match.group("sep")
            strong = (open_ and open_ in _STRONG_OPEN) or (close and close in _STRONG_CLOSE)
            if not (strong or "_" in sep or (open_ and close)):
                continue
            token = f"[{match.group('stem')}_{match.group('suffix')}]"
        start, end = match.span()
        parts.append(text[pos:start])
        out_len += start - pos
        spans.append((out_len, out_len + len(token)))
        if token != match.group(0):
            edits.append((out_len, out_len + len(token), start, end))
        parts.append(token)
        out_len += len(token)
        pos = end
    parts.append(text[pos:])
    return NormalizedText("".join(parts), tuple(spans), tuple(edits))


def normalize_aliases(text: str) -> str:
    """Repair OCR-damaged alias tokens (``IMEMBER_A]``, ``(MEMBER_A)``) to ``[MEMBER_A]``."""
    return normalize_aliases_mapped(text).text


def find_alias_tokens(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for token in ALIAS_TOKEN_RE.findall(normalize_aliases(text)):
        seen.setdefault(token, None)
    return list(seen)


@dataclass(frozen=True)
class _Piece:
    id: str
    doc_index: int
    start: int  # offset in the normalized document text
    text: str


def text_records(text: str) -> int:
    return math.ceil(len(text) / TEXT_RECORD_CHARS) if text else 0


def split_text(text: str, max_chars: int = MAX_DOC_CHARS) -> list[tuple[int, str]]:
    pieces: list[tuple[int, str]] = []
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", pos, end), text.rfind(" ", pos, end))
            if cut > pos + max_chars // 2:
                end = cut + 1
        pieces.append((pos, text[pos:end]))
        pos = end
    return pieces


def classify(category: str, confidence: float) -> Literal["hard", "advisory"] | None:
    settings = get_settings()
    if category in HARD_TIER_CATEGORIES and confidence >= settings.pii_hard_min_confidence:
        return "hard"
    if (
        category in HARD_TIER_CATEGORIES | ADVISORY_TIER_CATEGORIES
        and confidence >= settings.pii_advisory_min_confidence
    ):
        # A low-confidence identifier is reported, never blocking.
        return "advisory"
    return None


def build_request(pieces: list[_Piece], language: str) -> dict[str, Any]:
    return {
        "kind": "PiiEntityRecognition",
        "parameters": {
            "modelVersion": "latest",
            "loggingOptOut": True,  # M0-verify: honoured by the PII task
            "stringIndexType": "UnicodeCodePoint",
            # M0-verify: category names (CA*/AU*, DateOfBirth) as accepted by piiCategories.
            "piiCategories": sorted(HARD_TIER_CATEGORIES | ADVISORY_TIER_CATEGORIES),
        },
        "analysisInput": {"documents": [{"id": p.id, "language": language, "text": p.text} for p in pieces]},
    }


def parse_response(
    body: dict[str, Any], pieces: list[_Piece], docs: list[NormalizedText], result: PiiCheckResult
) -> None:
    results = body.get("results") or {}
    if results.get("errors"):
        raise ApiError("upstream_error", "The PII check could not analyze this content.")
    by_id = {p.id: p for p in pieces}
    for doc in results.get("documents", []):
        piece = by_id.get(str(doc.get("id")))
        if piece is None:
            continue
        normalized = docs[piece.doc_index]
        for entity in doc.get("entities", []):
            category = str(entity.get("category", ""))
            subcategory = entity.get("subcategory")
            # M0-verify: whether DateOfBirth arrives as a category or as DateTime/DateOfBirth.
            if isinstance(subcategory, str) and subcategory in HARD_TIER_CATEGORIES:
                category = subcategory
            confidence = float(entity.get("confidenceScore", 0.0))
            tier = classify(category, confidence)
            if tier is None:
                continue
            start = piece.start + int(entity.get("offset", 0))
            end = start + int(entity.get("length", 0))
            if normalized.alias_overlap(start, end) >= 0.5:
                continue
            orig_start = normalized.to_original(start)
            hit = PiiHit(
                category=category,
                tier=tier,
                confidence=round(confidence, 3),
                doc_index=piece.doc_index,
                offset=orig_start,
                length=max(0, normalized.to_original(end) - orig_start),
            )
            (result.hard if tier == "hard" else result.advisory).append(hit)


async def check_pii(texts: list[str], *, language: str = "en", page_numbers: list[int] | None = None) -> PiiCheckResult:
    """Hit offsets refer to the original texts; ``page_numbers`` is only read by the fake-mode PII hook."""
    settings = get_settings()
    docs = [normalize_aliases_mapped(t) for t in texts]
    if settings.ai_mode != "live":
        return _fake_check(texts, docs, page_numbers)

    from app.services.rest import post_json

    pieces: list[_Piece] = []
    for doc_index, normalized in enumerate(docs):
        for offset, piece in split_text(normalized.text):
            if piece.strip():
                pieces.append(_Piece(str(len(pieces)), doc_index, offset, piece))
    result = PiiCheckResult(records=sum(text_records(p.text) for p in pieces))
    path = f"language/:analyze-text?api-version={settings.language_api_version}"  # M0-verify: 2026-05-01 GA
    for batch_start in range(0, len(pieces), MAX_DOCS_PER_REQUEST):
        batch = pieces[batch_start : batch_start + MAX_DOCS_PER_REQUEST]
        body = await post_json(path, build_request(batch, language), service="Language PII")
        parse_response(body, batch, docs, result)
    return result


def _fake_check(texts: list[str], docs: list[NormalizedText], page_numbers: list[int] | None) -> PiiCheckResult:
    result = PiiCheckResult(records=sum(text_records(d.text) for d in docs))
    hook = current_hooks().pii
    if hook is None:
        return result
    page, category = hook
    pages = page_numbers or list(range(1, len(texts) + 1))
    if page not in pages:
        return result
    index = pages.index(page)
    tier: Literal["hard", "advisory"] = "advisory" if category in ADVISORY_TIER_CATEGORIES else "hard"
    hit = PiiHit(category, tier, 0.99, index, 0, min(len(texts[index]), 12))
    (result.hard if tier == "hard" else result.advisory).append(hit)
    return result
