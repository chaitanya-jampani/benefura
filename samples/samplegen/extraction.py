"""Golden extraction rows derived from the fixture plans so they can't drift; conventions are in README.md."""

from __future__ import annotations

import re
from typing import Any

from samplegen.fixtures import plan
from samplegen.pii import MARKER

# Notes in the fixture that describe app behaviour rather than booklet content.
APP_ONLY_NOTES = {("au-wattle", "ben-hospital-admission")}

# Employer, address, phone and email aliases on these pages are not plan facts.
ALIAS_PAGES = {"ca-northwind": [2], "au-wattle": [2]}
ALIAS_STEMS = {"MEMBER", "POLICY", "CERT"}

HEADER_ROWS: dict[str, list[dict[str, Any]]] = {
    "ca-northwind": [
        {
            "row_id": "hdr-plan",
            "page": 3,
            "quote": (
                "Northwind Life & Health provides the Group Extended Health and Dental, Class A plan described in this "
                "booklet. Coverage is effective January 1, 2025 under a group policy issued in Ontario. The benefit "
                "year runs from January 1 to December 31, and all amounts are in Canadian dollars"
            ),
            "insurer": "Northwind Life & Health",
            "plan_name": "Group Extended Health and Dental, Class A",
            "currency": "CAD",
            "effective_date": "2025-01-01",
            "benefit_period_kind": "benefit_year",
            "benefit_period_start_month": 1,
            "province": "ON",
            "cover_type": None,
            "hospital_tier": None,
            "hospital_plus": None,
            "hsa_annual_credit": None,
            "hsa_carry_forward_years": None,
        },
        {
            "row_id": "hdr-hsa",
            "page": 12,
            "quote": (
                "Your employer credits $500 to your health spending account at the start of each benefit year. Unused "
                "credits carry forward for 1 benefit year"
            ),
            "insurer": None,
            "plan_name": None,
            "currency": None,
            "effective_date": None,
            "benefit_period_kind": None,
            "benefit_period_start_month": None,
            "province": None,
            "cover_type": None,
            "hospital_tier": None,
            "hospital_plus": None,
            "hsa_annual_credit": 500.0,
            "hsa_carry_forward_years": 1,
        },
    ],
    "au-wattle": [
        {
            "row_id": "hdr-plan",
            "page": 3,
            "quote": (
                "Silver Plus Hospital and Mid Extras is a combined hospital and extras policy from Wattle Health Fund. "
                "Your hospital cover is classified as Silver Plus. Your cover started on 1 July 2024. Extras limits "
                "reset on 1 January each calendar year, and all amounts are in Australian dollars"
            ),
            "insurer": "Wattle Health Fund",
            "plan_name": "Silver Plus Hospital and Mid Extras",
            "currency": "AUD",
            "effective_date": "2024-07-01",
            "benefit_period_kind": "benefit_year",
            "benefit_period_start_month": 1,
            "province": None,
            "cover_type": "combined",
            "hospital_tier": "silver",
            "hospital_plus": True,
            "hsa_annual_credit": None,
            "hsa_carry_forward_years": None,
        }
    ],
}


def _rule(
    row_id: str, page: int, quote: str, kind: str, days: int | None = None, basis: str | None = None
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "page": page,
        "quote": quote,
        "rule_kind": kind,
        "deadline_days": days,
        "deadline_basis": basis,
        "text": quote + ".",
    }


RULE_ROWS: dict[str, list[dict[str, Any]]] = {
    "ca-northwind": [
        _rule(
            "rule-deadline",
            13,
            "Claims must be received no later than 90 days after the end of the benefit year in which the expense was incurred",
            "submission_deadline",
            90,
            "period_end",
        ),
        _rule("rule-receipts", 13, "Every claim must be supported by an itemized receipt", "receipts_required"),
        _rule("rule-keep-receipts", 13, "Keep original receipts for 12 months in case of audit", "other"),
        _rule(
            "rule-cob-spouse",
            13,
            "If your spouse has their own plan, claim for your spouse under their plan first",
            "coordination_of_benefits",
        ),
    ],
    "au-wattle": [
        _rule(
            "rule-deadline",
            11,
            "Extras claims must be lodged within 2 years of the date of service",
            "submission_deadline",
            730,
            "service_date",
        ),
        _rule("rule-receipts", 11, "You need an itemised receipt for every extras claim", "receipts_required"),
        _rule(
            "rule-hicaps",
            11,
            "Claim on the spot with your member card where the provider has a HICAPS terminal",
            "other",
        ),
        _rule(
            "rule-no-double-claim",
            11,
            "Benefits cannot be paid for services claimed from Medicare or another insurer",
            "other",
        ),
    ],
}


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


def _money(cents: int) -> str:
    return f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"


def _period_months(period: dict[str, Any] | None) -> int | None:
    if not period:
        return None
    if period.get("months"):
        return period["months"]
    if period.get("years"):
        return period["years"] * 12
    return None


def _limit(limit: dict[str, Any]) -> tuple[str, float]:
    if limit["unit"] == "cents":
        return "dollars", limit["value"] / 100
    return limit["unit"], float(limit["value"])


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith(".") else text + "."


def _notes(doc: str, b: dict[str, Any]) -> str | None:
    cov = b["coverage"]
    parts: list[str] = []
    if cov["kind"] == "schedule":
        items = "; ".join(
            f"{i['itemCode']} {i['description']} {_money(i['benefitCents'])}" for i in cov["scheduleItems"]
        )
        parts.append(f"Schedule: {items}.")
    elif cov.get("scheduleNote"):
        parts.append(_sentence(cov["scheduleNote"]))
    wp = b.get("waitingPeriod")
    if wp and wp.get("note"):
        parts.append(_sentence(f"Waiting period: {wp['note'][0].lower()}{wp['note'][1:]}"))
    if b.get("notes") and (doc, b["id"]) not in APP_ONLY_NOTES:
        parts.append(_sentence(b["notes"]))
    return " ".join(parts) or None


def benefit_rows(doc: str) -> list[dict[str, Any]]:
    p = plan(doc)
    pools = {pool["id"]: pool["name"] for pool in p["limitPools"]}
    rows = []
    for cat in p["categories"]:
        for b in cat["benefits"]:
            cov = b["coverage"]
            assert len(b["limits"]) <= 1, b["id"]
            limit = b["limits"][0] if b["limits"] else None
            unit, value = _limit(limit) if limit else (None, None)
            freq = b.get("frequency")
            wp = b.get("waitingPeriod")
            rows.append(
                {
                    "row_id": b["id"],
                    "page": b["source"]["page"],
                    "quote": b["source"]["quote"],
                    "category_name": cat["name"],
                    "category_kind": cat["kind"],
                    "benefit_name": b["name"],
                    "keywords": ", ".join(b["keywords"]) or None,
                    "item_codes": ", ".join(b["itemCodes"]) or None,
                    "coverage_kind": cov["kind"],
                    "coverage_percent": None if cov["percent"] is None else float(cov["percent"]),
                    "coverage_cap_amount": _dollars(cov["capCents"]),
                    "coverage_amount": _dollars(cov["amountCents"]),
                    "limit_unit": unit,
                    "limit_value": value,
                    "limit_period_kind": limit["period"]["kind"] if limit else None,
                    "limit_period_months": _period_months(limit["period"]) if limit else None,
                    "limit_scope": limit["scope"] if limit else None,
                    "frequency_count": freq["count"] if freq else None,
                    "frequency_period_kind": freq["period"]["kind"] if freq else None,
                    "frequency_period_months": _period_months(freq["period"]) if freq else None,
                    "waiting_period_months": wp["months"] if wp else None,
                    "requirements": "; ".join(b["requirements"]) or None,
                    "pool_name": pools.get(b["poolId"]) if b["poolId"] else None,
                    "notes": _notes(doc, b),
                }
            )
    return rows


def pool_rows(doc: str) -> list[dict[str, Any]]:
    p = plan(doc)
    names = {b["id"]: b["name"] for cat in p["categories"] for b in cat["benefits"]}
    rows = []
    for pool in p["limitPools"]:
        unit, value = _limit(pool["limit"])
        rows.append(
            {
                "row_id": pool["id"],
                "page": pool["source"]["page"],
                "quote": pool["source"]["quote"],
                "pool_name": pool["name"],
                "limit_unit": unit,
                "limit_value": value,
                "period_kind": pool["limit"]["period"]["kind"],
                "period_months": _period_months(pool["limit"]["period"]),
                "scope": pool["limit"]["scope"],
                "benefit_names": ", ".join(names[i] for i in pool["benefitIds"]),
            }
        )
    return rows


def cost_share_rows(doc: str) -> list[dict[str, Any]]:
    p = plan(doc)
    cats = {c["id"]: c["name"] for c in p["categories"]}
    names = {b["id"]: b["name"] for cat in p["categories"] for b in cat["benefits"]}
    rows = []
    for cs in p["costShares"]:
        applies = [cats[i] for i in cs["appliesToCategoryIds"]] + [names[i] for i in cs["appliesToBenefitIds"]]
        rows.append(
            {
                "row_id": cs["id"],
                "page": cs["source"]["page"],
                "quote": cs["source"]["quote"],
                "name": cs["name"],
                "kind": cs["kind"],
                "amount": _dollars(cs["amountCents"]),
                "percent": cs["percent"],
                "period_kind": cs["period"]["kind"] if cs["period"] else None,
                "scope": cs["scope"],
                "applies_to": ", ".join(applies) or None,
            }
        )
    return rows


def hospital_rows(doc: str) -> list[dict[str, Any]]:
    profile = plan(doc)["profile"]
    if profile["kind"] != "AU" or not profile.get("hospital"):
        return []
    return [
        {
            "row_id": "hosp-" + re.sub(r"[^a-z0-9]+", "-", hc["name"].lower()).strip("-"),
            "page": hc["source"]["page"],
            "quote": hc["source"]["quote"],
            "name": hc["name"],
            "status": hc["status"],
        }
        for hc in profile["hospital"]["categories"]
    ]


def alias_rows(doc: str) -> list[dict[str, Any]]:
    """All-null header rows quoting ``| label | [TOKEN] |`` cells, which ``assemble.py`` reads for members."""

    from samplegen import au_policy, ca_booklet
    from samplegen.doc import Grid, md_text

    spec = {"ca-northwind": ca_booklet.spec, "au-wattle": au_policy.spec}[doc]()
    empty = {k: None for k in HEADER_ROWS[doc][0] if k not in ("row_id", "page", "quote")}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in ALIAS_PAGES[doc]:
        for block in spec.pages[page - 1].blocks:
            if not isinstance(block, Grid):
                continue
            for cells in block.rows:
                for label, value in zip(cells, cells[1:], strict=False):
                    marker = MARKER.fullmatch(value.strip())
                    if marker is None or MARKER.search(label):
                        continue
                    token = spec.registry[marker.group(1)].token
                    if token is None or token[1:-1].rsplit("_", 1)[0] not in ALIAS_STEMS or token in seen:
                        continue
                    seen.add(token)
                    rows.append(
                        {
                            "row_id": "hdr-alias-" + token[1:-1].lower().replace("_", "-"),
                            "page": page,
                            "quote": f"{md_text(label, spec.registry)} | {token}",
                            **empty,
                        }
                    )
    return rows


def chunk(doc: str) -> dict[str, Any]:
    return {
        "header": alias_rows(doc) + HEADER_ROWS[doc],
        "benefits": benefit_rows(doc),
        "pools": pool_rows(doc),
        "cost_shares": cost_share_rows(doc),
        "rules": RULE_ROWS[doc],
        "hospital_categories": hospital_rows(doc),
    }
