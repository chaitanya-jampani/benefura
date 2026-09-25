"""Python port of ``apps/web/src/domain`` (the source of truth), kept in step by ``tests/test_engine_double.py``."""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

JSON = dict[str, Any]
LIFETIME = ("0001-01-01", "9999-12-31")
Window = tuple[str, str]


def _is_int(value: float | int) -> bool:
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def round_half_up(numerator: float | int, denominator: float | int = 1) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if _is_int(numerator) and _is_int(denominator):
        n, d = int(numerator), int(denominator)
        return (2 * n + d) // (2 * d)
    return math.floor(numerator / denominator + 0.5 + 1e-9)


def js_round(value: float) -> int:
    """``Math.round``: halves go toward +infinity."""
    return math.floor(value + 0.5)


def percent_of(cents: int, percent: float) -> int:
    return round_half_up(cents * js_round(percent * 100), 10_000)


def allocate(total: int, weights: list[float]) -> list[int]:
    if not weights:
        return []
    safe = [w if math.isfinite(w) and w > 0 else 0 for w in weights]
    total_weight = sum(safe)
    basis = safe if total_weight > 0 else [1 if i == 0 else 0 for i in range(len(safe))]
    basis_sum = total_weight if total_weight > 0 else 1
    exact = [total * w / basis_sum for w in basis]
    parts = [math.floor(v) for v in exact]
    left = total - sum(parts)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - math.floor(exact[i])), i))
    for i in order:
        if left <= 0:
            break
        parts[i] += 1
        left -= 1
    return parts


def clamp_cents(value: float) -> int:
    return max(0, js_round(value))


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def add_days(iso: str, days: int) -> str:
    return (_d(iso) + timedelta(days=int(days))).isoformat()


def add_months(iso: str, months: int) -> str:
    x = _d(iso)
    index = x.year * 12 + (x.month - 1) + int(months)
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(x.day, calendar.monthrange(year, month)[1])).isoformat()


def in_range(iso: str, window: Window) -> bool:
    return window[0] <= iso <= window[1]


def _anchor_on(year: int, anchor: tuple[int, int]) -> str:
    month, day = anchor
    return date(year, month, min(day, calendar.monthrange(year, month)[1])).isoformat()


def _anniversary_anchor(plan: JSON) -> tuple[int, int]:
    if plan.get("effectiveDate"):
        eff = _d(plan["effectiveDate"])
        return eff.month, eff.day
    return 1, 1


def _benefit_year_anchor(period: JSON, plan: JSON) -> tuple[int, int]:
    if period.get("startMonth") is not None:
        return int(period["startMonth"]), int(period.get("startDay") or 1)
    own = plan["benefitPeriod"]
    if own["kind"] == "benefit_year" and own.get("startMonth") is not None:
        return int(own["startMonth"]), int(own.get("startDay") or 1)
    if own["kind"] == "policy_anniversary":
        return _anniversary_anchor(plan)
    return 1, 1


def _year_window(anchor: tuple[int, int], iso: str) -> Window:
    year = _d(iso).year
    start_year = year if _anchor_on(year, anchor) <= iso else year - 1
    return _anchor_on(start_year, anchor), add_days(_anchor_on(start_year + 1, anchor), -1)


def period_window(period: JSON, service_date: str, plan: JSON) -> Window | None:
    kind = period["kind"]
    if kind == "benefit_year":
        return _year_window(_benefit_year_anchor(period, plan), service_date)
    if kind == "policy_anniversary":
        return _year_window(_anniversary_anchor(plan), service_date)
    if kind == "rolling_months":
        months = int(period.get("months") or 12)
        return add_days(add_months(service_date, -months), 1), service_date
    if kind == "consecutive_benefit_years":
        years = max(1, int(period.get("years") or 1))
        anchor = _benefit_year_anchor(period, plan)
        first = _year_window(anchor, plan.get("effectiveDate") or _anchor_on(1, anchor))[0]
        current = _year_window(anchor, service_date)[0]
        start_year = _d(first).year + ((_d(current).year - _d(first).year) // years) * years
        return _anchor_on(start_year, anchor), add_days(_anchor_on(start_year + years, anchor), -1)
    if kind == "lifetime":
        return LIFETIME
    return None  # per_visit / per_admission


def current_benefit_period(plan: JSON, today: str) -> Window:
    window = period_window(plan["benefitPeriod"], today, plan)
    if window is None or window == LIFETIME:
        return _year_window((1, 1), today)
    return window


def claim_deadline(plan: JSON, service_date: str) -> str | None:
    rules = plan.get("claimRules") or {}
    candidates: list[str] = []
    if rules.get("submissionDays") is not None:
        candidates.append(add_days(service_date, int(rules["submissionDays"])))
    if rules.get("daysAfterPeriodEnd") is not None:
        window = period_window(plan["benefitPeriod"], service_date, plan)
        if window is not None and window != LIFETIME:
            candidates.append(add_days(window[1], int(rules["daysAfterPeriodEnd"])))
    return max(candidates) if candidates else None


def waiting_period_ends(benefit: JSON, plan: JSON, coverage_start: str | None = None) -> str | None:
    months = int((benefit.get("waitingPeriod") or {}).get("months") or 0)
    start = coverage_start or plan.get("effectiveDate")
    if months <= 0 or not start:
        return None
    return add_months(start, months)


@dataclass
class LedgerEntry:
    claim_id: str
    line_id: str
    member_id: str
    benefit_id: str
    service_date: str
    quantity: float
    plan_paid_cents: int
    cost_share_cents: dict[str, int] = field(default_factory=dict)


@dataclass
class PlanIndex:
    benefits: dict[str, JSON]
    categories: dict[str, JSON]
    pool_by_benefit: dict[str, JSON]
    cost_shares_by_benefit: dict[str, list[JSON]]


def index_plan(plan: JSON) -> PlanIndex:
    benefits: dict[str, JSON] = {}
    categories: dict[str, JSON] = {}
    for category in plan["categories"]:
        categories[category["id"]] = category
        for benefit in category["benefits"]:
            benefits[benefit["id"]] = benefit
    pool_by_benefit: dict[str, JSON] = {}
    for pool in plan["limitPools"]:
        for benefit_id in pool["benefitIds"]:
            pool_by_benefit.setdefault(benefit_id, pool)
    for benefit in benefits.values():
        pool = next((p for p in plan["limitPools"] if benefit.get("poolId") and p["id"] == benefit["poolId"]), None)
        if pool is not None:
            pool_by_benefit[benefit["id"]] = pool
    shares = {
        b["id"]: [
            cs
            for cs in plan["costShares"]
            if cs["id"] in b.get("costShareIds", [])
            or b["id"] in cs.get("appliesToBenefitIds", [])
            or b["categoryId"] in cs.get("appliesToCategoryIds", [])
        ]
        for b in benefits.values()
    }
    return PlanIndex(benefits, categories, pool_by_benefit, shares)


def counted_claims(claims: list[JSON], include_submitted: bool) -> list[JSON]:
    return [
        c
        for c in claims
        if c["status"] in ("paid", "partially_paid") or (include_submitted and c["status"] == "submitted")
    ]


def _in_scope(entry: LedgerEntry, member_id: str | None, scope: str) -> bool:
    return scope != "per_person" or member_id is None or entry.member_id == member_id


def _measure(entry: LedgerEntry, unit: str) -> float:
    if unit == "cents":
        return entry.plan_paid_cents
    return entry.quantity if entry.plan_paid_cents > 0 else 0


def _sum_entries(
    ledger: list[LedgerEntry], benefit_ids: set[str], member_id: str | None, scope: str, window: Window, unit: str
) -> float:
    total: float = 0
    for entry in ledger:
        if (
            entry.benefit_id in benefit_ids
            and _in_scope(entry, member_id, scope)
            and in_range(entry.service_date, window)
        ):
            total += _measure(entry, unit)
    return int(total) if _is_int(total) else total


def _window_json(window: Window | None) -> JSON | None:
    return {"start": window[0], "end": window[1]} if window else None


def limit_usage_at(
    plan: JSON, ledger: list[LedgerEntry], benefit_id: str, member_id: str, limit: JSON, as_of: str
) -> JSON:
    window = period_window(limit["period"], as_of, plan)
    if window is None:
        return {"limit": limit, "window": None, "used": 0, "remaining": limit["value"]}
    used = _sum_entries(ledger, {benefit_id}, member_id, limit.get("scope", "per_person"), window, limit["unit"])
    return {"limit": limit, "window": window, "used": used, "remaining": max(0, limit["value"] - used)}


def pool_usage_at(plan: JSON, ledger: list[LedgerEntry], pool: JSON, member_id: str, as_of: str) -> JSON:
    base = {"poolId": pool["id"], "name": pool["name"], "limit": pool["limit"], "benefitIds": pool["benefitIds"]}
    window = period_window(pool["limit"]["period"], as_of, plan)
    if window is None:
        return {**base, "window": None, "used": 0, "remaining": pool["limit"]["value"]}
    members = dict.fromkeys(pool["benefitIds"])
    for benefit in index_plan(plan).benefits.values():
        if benefit.get("poolId") == pool["id"]:
            members[benefit["id"]] = None
    limit = pool["limit"]
    used = _sum_entries(ledger, set(members), member_id, limit.get("scope", "per_person"), window, limit["unit"])
    return {
        **base,
        "benefitIds": list(members),
        "window": window,
        "used": used,
        "remaining": max(0, limit["value"] - used),
    }


def frequency_usage_at(
    plan: JSON, ledger: list[LedgerEntry], benefit_id: str, member_id: str, frequency: JSON, as_of: str
) -> JSON:
    period = frequency["period"]
    window = period_window(period, as_of, plan)
    count = int(frequency["count"])
    if window is None:
        return {"count": count, "used": 0, "window": None, "nextEligible": None}
    dates: list[str] = []
    for entry in ledger:
        if entry.benefit_id != benefit_id or entry.plan_paid_cents <= 0:
            continue
        if not _in_scope(entry, member_id, frequency.get("scope", "per_person")) or not in_range(
            entry.service_date, window
        ):
            continue
        dates.extend([entry.service_date] * max(1, math.ceil(entry.quantity)))
    used = len(dates)
    if used < count:
        return {"count": count, "used": used, "window": window, "nextEligible": None}
    if period["kind"] == "rolling_months":
        dates.sort()
        blocking = dates[used - count]
        next_eligible = add_months(blocking, int(period.get("months") or 12))
        while in_range(blocking, period_window(period, next_eligible, plan) or LIFETIME):
            next_eligible = add_days(next_eligible, 1)
    elif window == LIFETIME:
        next_eligible = LIFETIME[1]
    else:
        next_eligible = add_days(window[1], 1)
    return {"count": count, "used": used, "window": window, "nextEligible": next_eligible}


def cost_share_window(plan: JSON, cost_share: JSON, as_of: str) -> Window | None:
    if not cost_share.get("period"):
        return current_benefit_period(plan, as_of)
    return period_window(cost_share["period"], as_of, plan)


def cost_share_usage_at(
    plan: JSON, ledger: list[LedgerEntry], cost_share: JSON, member_id: str | None, as_of: str
) -> JSON:
    amount = int(cost_share.get("amountCents") or 0)
    scope = cost_share.get("scope", "per_person")
    owner = member_id if scope == "per_person" else None
    window = cost_share_window(plan, cost_share, as_of)
    if window is None:
        return {
            "costShareId": cost_share["id"],
            "memberId": owner,
            "window": None,
            "metCents": 0,
            "remainingCents": amount,
        }
    met = sum(
        entry.cost_share_cents.get(cost_share["id"], 0)
        for entry in ledger
        if entry.cost_share_cents.get(cost_share["id"])
        and _in_scope(entry, owner, scope)
        and in_range(entry.service_date, window)
    )
    return {
        "costShareId": cost_share["id"],
        "memberId": owner,
        "window": window,
        "metCents": min(met, amount),
        "remainingCents": max(0, amount - met),
    }


def paid_in_window(ledger: list[LedgerEntry], benefit_id: str, member_id: str, window: Window) -> int:
    return int(_sum_entries(ledger, {benefit_id}, member_id, "per_person", window, "cents"))


def _normalize_code(code: str | None) -> str:
    text = (code or "").strip()
    lowered = text.lower()
    if lowered.startswith("item"):
        text = text[4:].lstrip()
    return text.lower()


def coverage_amount(coverage: JSON, charged_cents: int, quantity: float, item_code: str | None) -> JSON:
    charged = max(0, charged_cents)
    kind = coverage["kind"]
    if kind == "percent":
        return {"cents": percent_of(charged, coverage.get("percent") or 0), "capped": False, "item": None}
    if kind == "percent_capped":
        raw = percent_of(charged, coverage.get("percent") or 0)
        cap = math.inf if coverage.get("capCents") is None else round_half_up(coverage["capCents"] * quantity)
        return {"cents": min(raw, cap), "capped": raw > cap, "item": None}
    if kind in ("fixed_per_service", "per_diem"):
        return {
            "cents": min(charged, round_half_up((coverage.get("amountCents") or 0) * quantity)),
            "capped": False,
            "item": None,
        }
    code = _normalize_code(item_code)
    item = next((i for i in coverage.get("scheduleItems", []) if code and _normalize_code(i["itemCode"]) == code), None)
    if item is not None:
        return {"cents": min(charged, round_half_up(item["benefitCents"] * quantity)), "capped": False, "item": item}
    if coverage.get("percent") is not None:
        return {"cents": percent_of(charged, coverage["percent"]), "capped": False, "item": None}
    return {"cents": 0, "capped": False, "item": None}


def _dollars(cents: float) -> str:
    whole = abs(js_round(cents))
    text = f"${whole // 100:,}".replace(",", " ")
    return text if whole % 100 == 0 else f"{text}.{whole % 100:02d}"


@dataclass
class LedgerEstimate:
    result: JSON
    quantity: float
    cost_share_cents: dict[str, int]


def _empty_result(inp: JSON, deadline: str | None, label: str, warning: str) -> JSON:
    charged = clamp_cents(inp["chargedCents"])
    other = min(charged, clamp_cents(inp.get("otherPlanPaidCents") or 0))
    return {
        "benefitId": inp["benefitId"],
        "eligibleCents": charged,
        "planPaysCents": 0,
        "memberPaysCents": charged - other,
        "limitedBy": "not_covered",
        "steps": [{"label": label, "planPaysCents": 0}],
        "warnings": [warning],
        "remainingAfter": {"limitCents": None, "poolCents": None},
        "deadline": deadline,
    }


def estimate_on_ledger(plan: JSON, ledger: list[LedgerEntry], inp: JSON, today: str) -> LedgerEstimate:
    index = index_plan(plan)
    benefit = index.benefits.get(inp["benefitId"])
    raw_quantity = inp.get("quantity")
    quantity: float = raw_quantity if raw_quantity is not None and raw_quantity > 0 else 1
    service = inp["serviceDate"]
    deadline = claim_deadline(plan, service)
    if benefit is None:
        return LedgerEstimate(
            _empty_result(inp, deadline, "Not a benefit in this plan", "Pick a benefit from your plan to estimate."),
            quantity,
            {},
        )
    if plan.get("effectiveDate") and service < plan["effectiveDate"]:
        return LedgerEstimate(
            _empty_result(
                inp, deadline, "Before your coverage started", f"Coverage starts on {plan['effectiveDate']}."
            ),
            quantity,
            {},
        )

    member_id = inp["memberId"]
    charged = clamp_cents(inp["chargedCents"])
    other = min(charged, clamp_cents(inp.get("otherPlanPaidCents") or 0))
    balance = charged - other
    coverage = benefit["coverage"]
    item_code = inp.get("itemCode")
    steps: list[JSON] = []
    warnings: list[str] = []
    cost_share_cents: dict[str, int] = {}
    state: JSON = {"pays": 0, "limitedBy": None}

    def lower(value: float, reason: str) -> None:
        if value < state["pays"]:
            state["pays"] = max(0, int(value))
            state["limitedBy"] = reason

    def step(label: str) -> None:
        steps.append({"label": label, "planPaysCents": state["pays"]})

    covered = coverage_amount(coverage, charged, quantity, item_code)
    kind = coverage["kind"]
    if kind in ("percent", "percent_capped"):
        state["pays"] = percent_of(charged, coverage.get("percent") or 0)
        step(f"{coverage.get('percent') or 0}% of {_dollars(charged)}")
    elif kind in ("fixed_per_service", "per_diem"):
        state["pays"] = round_half_up((coverage.get("amountCents") or 0) * quantity)
        step(f"{_dollars(coverage.get('amountCents') or 0)} {'a day' if kind == 'per_diem' else 'a service'}")
    elif covered["item"] is not None:
        state["pays"] = round_half_up(covered["item"]["benefitCents"] * quantity)
        step(f"Item {covered['item']['itemCode']} pays {_dollars(covered['item']['benefitCents'])}")
    elif coverage.get("percent") is not None:
        state["pays"] = percent_of(charged, coverage["percent"])
        step(f"{coverage['percent']}% of {_dollars(charged)}")
    else:
        state["pays"] = 0
        state["limitedBy"] = "schedule"
        codes = ", ".join(i["itemCode"] for i in coverage.get("scheduleItems", []))
        step(f"Item {item_code} isn't on the schedule" if item_code else "No item number")
        warnings.append(
            f"Item {item_code} isn't covered under {benefit['name']}. Covered items: {codes}."
            if item_code
            else f"{benefit['name']} pays a set amount per item number. Add one of: {codes}."
        )
    if state["pays"] < charged and state["limitedBy"] is None:
        state["limitedBy"] = "schedule" if kind == "schedule" else "coverage"

    if kind == "percent_capped" and covered["capped"]:
        lower(covered["cents"], "per_service_cap")
        step(f"Capped at {_dollars(coverage.get('capCents') or 0)} a visit")
    elif state["pays"] > charged:
        state["pays"] = charged
        state["limitedBy"] = None
        step(f"Up to the {_dollars(charged)} charged")

    if other > 0:
        lower(balance, "other_plan")
        step(f"Other plan paid {_dollars(other)}")

    # Deductibles come off the charge, so caps and schedules still apply to what's left.
    for share in index.cost_shares_by_benefit.get(benefit["id"], []):
        if share["kind"] == "coinsurance":
            continue
        amount = int(share.get("amountCents") or 0)
        if amount <= 0:
            continue
        if share["kind"] == "copay":
            lower(state["pays"] - round_half_up(amount * quantity), "deductible")
            step(f"{share['name']}: {_dollars(amount)} a service")
            continue
        usage = cost_share_usage_at(plan, ledger, share, member_id, service)
        applied = min(usage["remainingCents"], balance)
        cost_share_cents[share["id"]] = applied
        if usage["remainingCents"] == 0:
            step(f"{share['name']} already met")
        elif applied > 0:
            after = min(coverage_amount(coverage, charged - applied, quantity, item_code)["cents"], balance - applied)
            lower(after, "deductible")
            step(f"{share['name']}: {_dollars(applied)} paid by you first")

    frequency = benefit.get("frequency")
    if frequency:
        usage = frequency_usage_at(plan, ledger, benefit["id"], member_id, frequency, service)
        allowed = int(frequency["count"]) - usage["used"]
        if allowed <= 0:
            lower(0, "frequency")
            steps.append({"label": "Frequency already used", "planPaysCents": 0})
            next_eligible = usage["nextEligible"]
            if next_eligible and next_eligible != LIFETIME[1]:
                warnings.append(f"Not eligible again until {next_eligible}.")
            else:
                warnings.append(f"{benefit['name']} has already been used.")
        else:
            if quantity > allowed:
                lower(round_half_up(state["pays"] * allowed, quantity), "frequency")
            step(f"{allowed} available")

    money_limits: list[int] = []
    for limit in benefit.get("limits", []):
        usage = limit_usage_at(plan, ledger, benefit["id"], member_id, limit, service)
        remaining = usage["remaining"]
        if limit["unit"] == "cents":
            lower(remaining, "limit")
            if usage["window"]:
                money_limits.append(int(remaining))
            step(f"{_dollars(remaining)} left of {_dollars(limit['value'])}")
        else:
            if remaining <= 0:
                lower(0, "limit")
            elif quantity > remaining:
                lower(round_half_up(state["pays"] * remaining, quantity), "limit")
            step(f"{remaining} left of {limit['value']} {limit['unit']}")
        if remaining <= 0:
            warnings.append("The maximum is used up.")

    pool = index.pool_by_benefit.get(benefit["id"])
    pool_remaining: int | None = None
    if pool is not None:
        usage = pool_usage_at(plan, ledger, pool, member_id, service)
        remaining = usage["remaining"]
        if pool["limit"]["unit"] == "cents":
            lower(remaining, "pool")
            if usage["window"]:
                pool_remaining = int(remaining)
            step(f"{pool['name']}: {_dollars(remaining)} left of {_dollars(pool['limit']['value'])}")
        else:
            if remaining <= 0:
                lower(0, "pool")
            elif quantity > remaining:
                lower(round_half_up(state["pays"] * remaining, quantity), "pool")
            step(f"{pool['name']}: {remaining} left")
        if remaining <= 0:
            warnings.append(f"The {pool['name'].lower()} is used up.")

    wait_ends = waiting_period_ends(benefit, plan)
    if wait_ends and service < wait_ends:
        state["pays"] = 0
        state["limitedBy"] = "waiting_period"
        steps.append({"label": f"Waiting period until {wait_ends}", "planPaysCents": 0})
        warnings.append(f"{benefit['name']} is covered for services from {wait_ends}.")

    if deadline and deadline < today:
        warnings.append(f"The deadline to claim this was {deadline}.")
    if index.categories.get(benefit["categoryId"], {}).get("kind") == "hospital":
        warnings.append(
            "Hospital estimates are a guide only: agreements, gap fees and clinical categories change what's paid."
        )

    pays = state["pays"]
    limited_by = None if pays == charged and state["limitedBy"] != "waiting_period" else state["limitedBy"]
    result = {
        "benefitId": benefit["id"],
        "eligibleCents": charged,
        "planPaysCents": pays,
        "memberPaysCents": balance - pays,
        "limitedBy": limited_by,
        "steps": steps,
        "warnings": warnings,
        "remainingAfter": {
            "limitCents": max(0, min(money_limits) - pays) if money_limits else None,
            "poolCents": None if pool_remaining is None else max(0, pool_remaining - pays),
        },
        "deadline": deadline,
    }
    return LedgerEstimate(result, quantity, cost_share_cents)


def line_input(line: JSON, member_id: str) -> JSON:
    return {
        "benefitId": line["benefitId"],
        "memberId": member_id,
        "serviceDate": line["serviceDate"],
        "chargedCents": line["chargedCents"],
        "quantity": line.get("quantity"),
        "itemCode": line.get("itemCode"),
        "otherPlanPaidCents": line.get("otherPlanPaidCents") or 0,
    }


def _first_service_date(claim: JSON) -> str:
    return min((ln["serviceDate"] for ln in claim["lines"]), default="9999-12-31")


def build_ledger(
    plan: JSON, claims: list[JSON], *, today: str, include_submitted: bool = False, exclude_claim_id: str | None = None
) -> list[LedgerEntry]:
    index = index_plan(plan)
    ordered = sorted(
        counted_claims(
            [c for c in claims if c["planId"] == plan["id"] and c["id"] != exclude_claim_id], include_submitted
        ),
        key=lambda c: (_first_service_date(c), c.get("createdAt", ""), c["id"]),
    )
    ledger: list[LedgerEntry] = []
    for claim in ordered:
        lines = sorted(
            (ln for ln in claim["lines"] if ln["benefitId"] in index.benefits), key=lambda ln: ln["serviceDate"]
        )
        entries: list[LedgerEntry] = []
        for line in lines:
            est = estimate_on_ledger(plan, ledger, line_input(line, claim["patientMemberId"]), today)
            entry = LedgerEntry(
                claim["id"],
                line["id"],
                claim["patientMemberId"],
                line["benefitId"],
                line["serviceDate"],
                est.quantity,
                est.result["planPaysCents"],
                est.cost_share_cents,
            )
            ledger.append(entry)
            entries.append(entry)
        if claim["status"] in ("paid", "partially_paid") and claim.get("outcome") and entries:
            estimates = [e.plan_paid_cents for e in entries]
            weights = (
                estimates
                if any(c > 0 for c in estimates)
                else [max(0, ln["chargedCents"] - (ln.get("otherPlanPaidCents") or 0)) for ln in lines]
            )
            for entry, cents in zip(
                entries, allocate(clamp_cents(claim["outcome"]["paidCents"]), weights), strict=True
            ):
                entry.plan_paid_cents = cents
    return ledger


def estimate_reimbursement(
    plan: JSON,
    claims: list[JSON],
    inp: JSON,
    *,
    today: str,
    include_submitted: bool = False,
    exclude_claim_id: str | None = None,
) -> JSON:
    ledger = build_ledger(
        plan, claims, today=today, include_submitted=include_submitted, exclude_claim_id=exclude_claim_id
    )
    return estimate_on_ledger(plan, ledger, inp, today).result


def benefit_usage(
    plan: JSON, claims: list[JSON], benefit_id: str, member_id: str, *, today: str, include_submitted: bool = False
) -> JSON:
    index = index_plan(plan)
    benefit = index.benefits.get(benefit_id)
    if benefit is None:
        raise KeyError(f"Unknown benefit: {benefit_id}")
    ledger = build_ledger(plan, claims, today=today, include_submitted=include_submitted)
    limits = [limit_usage_at(plan, ledger, benefit_id, member_id, lim, today) for lim in benefit.get("limits", [])]
    pool_def = index.pool_by_benefit.get(benefit_id)
    pool = pool_usage_at(plan, ledger, pool_def, member_id, today) if pool_def else None
    frequency = benefit.get("frequency")
    freq = frequency_usage_at(plan, ledger, benefit_id, member_id, frequency, today) if frequency else None
    money_remaining = [lu["remaining"] for lu in limits if lu["limit"]["unit"] == "cents" and lu["window"]]
    if pool and pool["limit"]["unit"] == "cents" and pool["window"]:
        money_remaining.append(pool["remaining"])
    ends = waiting_period_ends(benefit, plan)
    return {
        "benefitId": benefit_id,
        "memberId": member_id,
        "limits": limits,
        "pool": pool,
        "frequency": freq,
        "remainingCents": min(money_remaining) if money_remaining else None,
        "usedCents": paid_in_window(ledger, benefit_id, member_id, current_benefit_period(plan, today)),
        "waitingPeriodEnds": ends,
        "inWaitingPeriod": bool(ends and today < ends),
    }
