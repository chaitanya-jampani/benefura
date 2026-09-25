"""Scenario scoring: facts must appear in the answer and numbers in required tool-call arguments must match exactly."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from evals.harness.browser_tools import ToolError, load_plan, member_by_alias
from evals.harness.datasets import Fact, Scenario

JSON = dict[str, Any]

_GROUP = "[,\u2009\u202f\u00a0 ]"
_MONEY = re.compile(
    rf"(?<![\w.])(?:(?:CA|AU|C|A)\$|\$)\s?(\d{{1,3}}(?:{_GROUP}\d{{3}})+|\d+)(?:\.(\d{{1,2}}))?(?!\d)"
    rf"|(?<![\w.$])(\d{{1,3}}(?:{_GROUP}\d{{3}})+|\d+)(?:\.(\d{{2}}))?\s?(?:dollars|CAD|AUD)\b",
    re.IGNORECASE,
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MONTH_ABBR = r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_MDY = re.compile(rf"\b({_MONTHS}|{_MONTH_ABBR})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS}|{_MONTH_ABBR})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)


def money_cents(text: str) -> set[int]:
    found: set[int] = set()
    for m in _MONEY.finditer(text or ""):
        whole, frac = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
        cents = int(re.sub(r"\D", "", whole)) * 100 + (int((frac or "0").ljust(2, "0")) if frac else 0)
        found.add(cents)
    return found


def _month(name: str) -> int:
    key = name.lower()[:3]
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(key) + 1


def dates_in(text: str) -> set[date]:
    found: set[date] = set()
    for m in _ISO.finditer(text or ""):
        try:
            found.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in _MDY.finditer(text or ""):
        try:
            found.add(date(int(m.group(3)), _month(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    for m in _DMY.finditer(text or ""):
        try:
            found.add(date(int(m.group(3)), _month(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    return found


def check_fact(fact: Fact, text: str) -> bool:
    if fact.kind == "money":
        return fact.cents in money_cents(text)
    if fact.kind == "date":
        assert fact.iso is not None
        target = datetime.strptime(fact.iso, "%Y-%m-%d").date()
        return any(abs((found - target).days) <= fact.toleranceDays for found in dates_in(text))
    if fact.kind == "text":
        lowered = (text or "").lower()
        return any(option.lower() in lowered for option in fact.anyOf or [])
    if fact.kind == "number":
        numbers = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}
        return fact.value in numbers
    return False


def _normalize_member(value: Any, plan: JSON) -> Any:
    if isinstance(value, str):
        try:
            return member_by_alias(plan, value)["id"]
        except ToolError:
            return value
    return value


def arguments_match(expected: Any, actual: Any, plan: JSON, key: str = "") -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(arguments_match(v, actual.get(k), plan, k) for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(arguments_match(e, a, plan, key) for e, a in zip(expected, actual, strict=False))
    if isinstance(expected, bool) or expected is None:
        return actual == expected
    if isinstance(expected, int | float):
        return isinstance(actual, int | float) and not isinstance(actual, bool) and float(actual) == float(expected)
    if key.lower().endswith("memberid") or key == "member_alias":
        return _normalize_member(actual, plan) == _normalize_member(expected, plan)
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.strip() == expected.strip()
    return actual == expected


@dataclass
class ScenarioScore:
    id: str
    numeric_exact: bool
    tool_calls_ok: bool
    facts: list[JSON] = field(default_factory=list)
    missing_calls: list[str] = field(default_factory=list)
    arg_mismatches: list[str] = field(default_factory=list)
    forbidden_used: list[str] = field(default_factory=list)
    over_limit: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _call_names(expected: JSON | Any) -> list[str]:
    if expected.name:
        return [expected.name]
    return [alt["name"] for alt in expected.anyOf or []]


def evaluate_scenario(scenario: Scenario, row: JSON | None) -> ScenarioScore:
    if row is None:
        return ScenarioScore(scenario.id, False, False, errors=["no_harness_row"])
    plan = load_plan(scenario.plan)
    calls: list[JSON] = row.get("tool_calls", [])
    text = row.get("response", "") or ""
    score = ScenarioScore(scenario.id, True, True, errors=list(row.get("meta", {}).get("errors", [])))

    for expected in scenario.expectedToolCalls:
        if not expected.required:
            continue
        names = _call_names(expected)
        candidates = [c for c in calls if c["name"] in names]
        label = "|".join(names)
        if not candidates:
            score.missing_calls.append(label)
            continue
        if expected.arguments and not any(
            arguments_match(expected.arguments, c.get("arguments"), plan) for c in candidates
        ):
            score.arg_mismatches.append(label)

    for name in scenario.forbiddenTools:
        if any(c["name"] == name for c in calls):
            score.forbidden_used.append(name)
    for name, cap in scenario.maxCalls.items():
        if sum(1 for c in calls if c["name"] == name) > cap:
            score.over_limit.append(name)

    for fact in scenario.expectedFacts:
        ok = check_fact(fact, text)
        score.facts.append(
            {
                "kind": fact.kind,
                "ok": ok,
                **({"cents": fact.cents} if fact.cents is not None else {}),
                **({"iso": fact.iso} if fact.iso else {}),
            }
        )

    facts_ok = all(f["ok"] for f in score.facts)
    score.numeric_exact = facts_ok and not score.arg_mismatches and not score.missing_calls and not score.errors
    score.tool_calls_ok = not (score.missing_calls or score.arg_mismatches or score.forbidden_used or score.over_limit)
    return score


def score_scenarios(scenarios: list[Scenario], rows: list[JSON]) -> JSON:
    by_id = {r.get("meta", {}).get("id"): r for r in rows}
    evaluated = [s for s in scenarios if s.id in by_id]
    scores = [evaluate_scenario(s, by_id[s.id]) for s in evaluated]
    total = len(scores)
    return {
        "scenarios": total,
        "numeric_exactness": (sum(s.numeric_exact for s in scores) / total) if total else None,
        "numeric_exact_count": sum(s.numeric_exact for s in scores),
        "tool_call_match": (sum(s.tool_calls_ok for s in scores) / total) if total else None,
        "details": [s.__dict__ for s in scores],
    }
