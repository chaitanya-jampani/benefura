"""Field accuracy against the golden extraction. Unmatched rows on either side lower the score."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

JSON = dict[str, Any]
COLLECTIONS = ("header", "benefits", "pools", "cost_shares", "rules", "hospital_categories")
SCORED: dict[str, tuple[str, ...]] = {
    "header": (
        "insurer",
        "plan_name",
        "currency",
        "effective_date",
        "benefit_period_kind",
        "benefit_period_start_month",
        "province",
        "cover_type",
        "hospital_tier",
        "hospital_plus",
        "hsa_annual_credit",
        "hsa_carry_forward_years",
    ),
    "benefits": (
        "category_name",
        "category_kind",
        "benefit_name",
        "item_codes",
        "coverage_kind",
        "coverage_percent",
        "coverage_cap_amount",
        "coverage_amount",
        "limit_unit",
        "limit_value",
        "limit_period_kind",
        "limit_period_months",
        "limit_scope",
        "frequency_count",
        "frequency_period_kind",
        "frequency_period_months",
        "waiting_period_months",
        "pool_name",
    ),
    "pools": ("pool_name", "limit_unit", "limit_value", "period_kind", "period_months", "scope", "benefit_names"),
    "cost_shares": ("name", "kind", "amount", "percent", "period_kind", "scope", "applies_to"),
    "rules": ("rule_kind", "deadline_days", "deadline_basis"),
    "hospital_categories": ("name", "status"),
}
FUZZY_FIELDS = {"insurer", "plan_name", "category_name", "benefit_name", "pool_name", "name"}
LIST_FIELDS = {"item_codes", "benefit_names", "applies_to"}
MATCH_THRESHOLD = 80
FIELD_THRESHOLD = 90


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("&", "and").split())


def _key(collection: str, row: JSON) -> str:
    if collection == "benefits":
        return f"{row.get('category_name') or ''} {row.get('benefit_name') or ''}"
    if collection == "pools":
        return row.get("pool_name") or ""
    if collection == "rules":
        return f"{row.get('rule_kind') or ''} {row.get('text') or row.get('quote') or ''}"
    return row.get("name") or ""


def field_equal(name: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(expected) - float(actual)) <= 0.005
    e, a = _norm(str(expected)), _norm(str(actual))
    if name in LIST_FIELDS:
        es = sorted(x.strip() for x in e.split(",") if x.strip())
        as_ = sorted(x.strip() for x in a.split(",") if x.strip())
        if len(es) != len(as_):
            return False
        return all(any(fuzz.ratio(x, y) >= FIELD_THRESHOLD for y in as_) for x in es)
    if name in FUZZY_FIELDS:
        return fuzz.token_sort_ratio(e, a) >= FIELD_THRESHOLD
    return e == a


def normalize_prediction(prediction: JSON | list[JSON]) -> JSON:
    """Overlap duplicates keep the highest ``meta.confidence`` row, as ``/api/plan/assemble`` does."""
    chunks = prediction if isinstance(prediction, list) else [prediction]
    merged: JSON = {c: [] for c in COLLECTIONS}
    best: dict[tuple[str, str], tuple[float, int]] = {}
    for chunk in chunks:
        rows = chunk.get("rows", chunk)
        for collection in COLLECTIONS:
            for row in rows.get(collection, []) or []:
                confidence = float((row.get("meta") or {}).get("confidence", 1.0))
                clean = {k: v for k, v in row.items() if k != "meta"}
                if collection == "header":
                    merged[collection].append(clean)
                    continue
                key = (collection, _norm(_key(collection, clean)))
                if key in best:
                    previous_confidence, index = best[key]
                    if confidence > previous_confidence:
                        merged[collection][index] = clean
                        best[key] = (confidence, index)
                    continue
                best[key] = (confidence, len(merged[collection]))
                merged[collection].append(clean)
    return merged


def _merge_header(rows: list[JSON]) -> list[JSON]:
    if not rows:
        return []
    merged: JSON = {}
    for row in rows:
        for f in SCORED["header"]:
            if merged.get(f) is None and row.get(f) is not None:
                merged[f] = row[f]
    return [merged]


@dataclass
class CollectionScore:
    correct: int = 0
    total: int = 0
    unmatched_golden: int = 0
    unmatched_predicted: int = 0
    wrong_fields: list[str] = field(default_factory=list)


def _count_fields(collection: str, row: JSON) -> int:
    return sum(1 for f in SCORED[collection] if row.get(f) is not None)


def score_collection(collection: str, golden: list[JSON], predicted: list[JSON]) -> CollectionScore:
    score = CollectionScore()
    if collection == "header":
        golden, predicted = _merge_header(golden), _merge_header(predicted)
    remaining = list(range(len(predicted)))
    for g in golden:
        best, best_score = None, -1.0
        for i in remaining:
            p = predicted[i]
            if collection == "rules" and p.get("rule_kind") != g.get("rule_kind"):
                continue
            s = (
                100.0
                if collection == "header"
                else fuzz.token_set_ratio(_norm(_key(collection, g)), _norm(_key(collection, p)))
            )
            if s > best_score:
                best, best_score = i, s
        if best is None or best_score < MATCH_THRESHOLD:
            score.unmatched_golden += 1
            n = _count_fields(collection, g)
            score.total += n
            score.wrong_fields.append(f"{collection}:{_key(collection, g).strip()}:*")
            continue
        remaining.remove(best)
        p = predicted[best]
        for f in SCORED[collection]:
            if g.get(f) is None and p.get(f) is None:
                continue
            score.total += 1
            if field_equal(f, g.get(f), p.get(f)):
                score.correct += 1
            else:
                score.wrong_fields.append(f"{collection}:{_key(collection, g).strip()}:{f}")
    for i in remaining:
        score.unmatched_predicted += 1
        score.total += _count_fields(collection, predicted[i])
    return score


def score_extraction(golden: JSON, prediction: JSON | list[JSON]) -> JSON:
    predicted = normalize_prediction(prediction)
    per = {c: score_collection(c, golden.get(c, []) or [], predicted.get(c, [])) for c in COLLECTIONS}
    correct = sum(s.correct for s in per.values())
    total = sum(s.total for s in per.values())
    return {
        "field_accuracy": (correct / total) if total else 0.0,
        "correct": correct,
        "total": total,
        "collections": {c: s.__dict__ for c, s in per.items()},
    }
