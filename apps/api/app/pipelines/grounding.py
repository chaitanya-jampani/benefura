"""Deterministic merge after the verifier: quote grounding with rapidfuzz, corrections, confidence."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from rapidfuzz import fuzz

from app.models.api import (
    ExBenefitRow,
    ExCostShareRow,
    ExHeaderRow,
    ExHospitalCategoryRow,
    ExPoolRow,
    ExRuleRow,
    ExtractedRows,
    Issue,
    IssueCode,
)
from app.models.extraction import ExtractionChunk, RowKind, RowMeta, RowVerdict
from app.models.plan import VerifierVerdict

GROUNDED_MIN_SCORE = 85.0
LOW_CONFIDENCE = 0.6

# ExtractionChunk field → (row kind, response row model)
ROW_FIELDS: dict[str, tuple[RowKind, type[BaseModel]]] = {
    "header": ("header", ExHeaderRow),
    "benefits": ("benefit", ExBenefitRow),
    "pools": ("pool", ExPoolRow),
    "cost_shares": ("cost_share", ExCostShareRow),
    "rules": ("rule", ExRuleRow),
    "hospital_categories": ("hospital_category", ExHospitalCategoryRow),
}

_TAG_RE = re.compile(r"<[^>]+>")
_PUNCT_RE = re.compile(r"[|*#`>~]+")
_SPACE_RE = re.compile(r"\s+")
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = _PUNCT_RE.sub(" ", _TAG_RE.sub(" ", text))
    return _SPACE_RE.sub(" ", text).strip().lower()


def quote_score(quote: str, markdown: str) -> float:
    q, m = normalize_text(quote), normalize_text(markdown)
    if not q or not m:
        return 0.0
    if q in m:
        return 100.0
    return float(fuzz.partial_ratio(q, m))


def locate_quote(quote: str, pages: Mapping[int, str]) -> tuple[int | None, float]:
    best_page, best = None, 0.0
    for page, markdown in pages.items():
        score = quote_score(quote, markdown)
        if score > best:
            best_page, best = page, score
    return best_page, best


def row_confidence(score: float, verdict: VerifierVerdict | None, *, rounds_exhausted: bool = False) -> float:
    factor = {"supported": 1.0, "corrected": 0.85, "unsupported": 0.35, None: 0.75}[verdict]
    confidence = score / 100 * factor
    if score < GROUNDED_MIN_SCORE:
        confidence = min(confidence, 0.5)
    if rounds_exhausted:
        confidence *= 0.9
    return round(max(0.0, min(1.0, confidence)), 3)


def apply_correction[RowT: BaseModel](row: RowT, correction: str | None) -> tuple[RowT, bool]:
    if not correction:
        return row, False
    try:
        changes = json.loads(correction)
    except ValueError:
        return row, False
    if not isinstance(changes, dict):
        return row, False
    fields = type(row).model_fields
    allowed: dict[str, Any] = {k: v for k, v in changes.items() if k in fields and k not in ("row_id", "meta")}
    if not allowed:
        return row, False
    try:
        return type(row).model_validate({**row.model_dump(), **allowed}), True
    except ValidationError:
        return row, False


def merge_extraction(
    chunk: ExtractionChunk,
    verdicts: Sequence[RowVerdict],
    pages: Mapping[int, str],
    *,
    rounds_exhausted: bool = False,
) -> tuple[ExtractedRows, list[Issue]]:
    by_id = {v.row_id: v for v in verdicts}
    issues: list[Issue] = []
    out: dict[str, list[Any]] = {name: [] for name in ROW_FIELDS}
    used_ids: set[str] = set()
    for field_name, (kind, row_model) in ROW_FIELDS.items():
        for index, row in enumerate(getattr(chunk, field_name)):
            verdict = by_id.get(row.row_id)
            verdict_value: VerifierVerdict | None = verdict.verdict if verdict else None
            if verdict and verdict.verdict == "corrected":
                row, applied = apply_correction(row, verdict.correction)
                if not applied:
                    issues.append(_issue("low_confidence", "info", "A verifier correction could not be applied.", row))

            score = quote_score(row.quote, pages.get(row.page, "")) if row.page in pages else 0.0
            if score < GROUNDED_MIN_SCORE:
                best_page, best = locate_quote(row.quote, pages)
                if best_page is not None and best >= GROUNDED_MIN_SCORE and best > score:
                    row, score = row.model_copy(update={"page": best_page}), best
            if row.page not in pages:
                issues.append(
                    _issue("unsupported_row", "warning", "Row cites a page outside this chunk; dropped.", row)
                )
                continue

            row_id = row.row_id.strip()
            if not row_id or row_id in used_ids:
                row_id = f"{kind}-p{row.page}-{index + 1}"
            used_ids.add(row_id)

            confidence = row_confidence(score, verdict_value, rounds_exhausted=rounds_exhausted)
            grounded = score >= GROUNDED_MIN_SCORE
            meta = RowMeta(
                rowId=row_id,
                kind=kind,
                page=row.page,
                confidence=confidence,
                verifierVerdict=verdict_value,
                grounded=grounded,
            )
            data = {**row.model_dump(), "row_id": row_id, "meta": meta}
            out[field_name].append(row_model.model_validate(data))

            if verdict_value == "unsupported":
                issues.append(
                    _issue("unsupported_row", "warning", "The verifier could not confirm this row.", row, row_id)
                )
            elif not grounded:
                issues.append(_issue("ungrounded_quote", "warning", "Quote not found on the cited page.", row, row_id))
            elif confidence < LOW_CONFIDENCE:
                issues.append(_issue("low_confidence", "info", "Low confidence; please review.", row, row_id))
    return ExtractedRows.model_validate(out), issues


def _issue(
    code: IssueCode, severity: Literal["info", "warning"], message: str, row: Any, row_id: str | None = None
) -> Issue:
    return Issue(
        code=code, severity=severity, message=message, page=getattr(row, "page", None), rowId=row_id or row.row_id
    )
