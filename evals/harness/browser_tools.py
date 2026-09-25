"""Test double for ``apps/web/src/agent/tools.ts``. Search ranking is approximate and draft ids are deterministic."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness import engine

JSON = dict[str, Any]
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_RESULTS = 5
MAX_USAGE_ROWS = 24
MAX_CLAIMS = 25
APPROVAL_TOOLS = frozenset({"draft_claim", "update_claim"})
BROWSER_TOOLS = frozenset(
    {
        "get_plan_overview",
        "find_benefits",
        "search_plan_document",
        "get_usage",
        "estimate_reimbursement",
        "list_claims",
        "draft_claim",
        "update_claim",
    }
)
TRANSITIONS: dict[str, list[str]] = {
    "draft": ["submitted"],
    "submitted": ["paid", "partially_paid", "rejected", "draft"],
    "paid": ["partially_paid", "rejected"],
    "partially_paid": ["paid", "rejected"],
    "rejected": ["paid", "partially_paid"],
}
STOP_WORDS = {
    "item",
    "items",
    "number",
    "no",
    "code",
    "for",
    "the",
    "a",
    "an",
    "my",
    "of",
    "and",
    "is",
    "are",
    "do",
    "i",
    "how",
    "much",
    "what",
    "does",
}
_TERM = re.compile(r"[\w'-]+", re.UNICODE)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ToolError(Exception):
    """Mirrors ``ToolError`` in tools.ts."""


def load_plan(path: str | Path) -> JSON:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return json.loads(p.read_text(encoding="utf-8"))


# Output text matches tools.ts.
def format_money(cents: float) -> str:
    whole = abs(engine.js_round(cents))
    grouped = f"{whole // 100:,}".replace(",", " ")
    return f"{'-' if cents < 0 else ''}${grouped}.{whole % 100:02d}"


_SCOPE_TEXT = {"per_person": "per person", "per_family": "per family", "per_policy": "per policy"}


def _period_text(period: JSON) -> str:
    kind = period["kind"]
    return {
        "benefit_year": "per benefit year",
        "policy_anniversary": "per policy year",
        "rolling_months": f"in any {period.get('months') or 12} months",
        "consecutive_benefit_years": f"every {period.get('years') or 2} benefit years",
        "lifetime": "lifetime",
        "per_visit": "per visit",
        "per_admission": "per admission",
    }[kind]


def _limit_text(limit: JSON) -> str:
    amount = format_money(limit["value"]) if limit["unit"] == "cents" else f"{limit['value']} {limit['unit']}"
    return f"{amount} {_SCOPE_TEXT[limit.get('scope', 'per_person')]} {_period_text(limit['period'])}"


def _frequency_text(frequency: JSON) -> str:
    count = frequency["count"]
    noun = "service" if count == 1 else "services"
    return f"{count} {noun} {_SCOPE_TEXT[frequency.get('scope', 'per_person')]} {_period_text(frequency['period'])}"


def _coverage_text(coverage: JSON) -> str:
    kind = coverage["kind"]
    if kind == "percent":
        return f"{_num(coverage.get('percent') or 0)}%"
    if kind == "percent_capped":
        return f"{_num(coverage.get('percent') or 0)}% up to {format_money(coverage.get('capCents') or 0)} per visit"
    if kind == "fixed_per_service":
        return f"{format_money(coverage.get('amountCents') or 0)} per service"
    if kind == "per_diem":
        return f"{format_money(coverage.get('amountCents') or 0)} per day"
    items = coverage.get("scheduleItems") or []
    if items:
        return f"Set benefit per item ({len(items)} item codes)"
    if coverage.get("scheduleNote"):
        return coverage["scheduleNote"]
    return f"{_num(coverage['percent'])}%" if coverage.get("percent") is not None else "Set benefit per item"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _benefit_by_id(plan: JSON, benefit_id: str) -> JSON:
    benefit = engine.index_plan(plan).benefits.get(benefit_id)
    if benefit is None:
        raise ToolError(f'Unknown benefit id "{benefit_id}". Call find_benefits first.')
    return benefit


def _alias_key(alias: str) -> str:
    return re.sub(r"[\[\]\s]", "", alias).upper()


def member_by_alias(plan: JSON, alias: str) -> JSON:
    key = _alias_key(alias)
    member = next((m for m in plan["members"] if _alias_key(m["alias"]) == key or m["id"] == alias), None)
    if member is None:
        known = ", ".join(m["alias"] for m in plan["members"]) or "none"
        raise ToolError(f'Unknown member alias "{alias}". Members on this plan: {known}.')
    return member


def _member_alias(plan: JSON, member_id: str) -> str:
    return next((m["alias"] for m in plan["members"] if m["id"] == member_id), "[UNKNOWN_MEMBER]")


def _describe_benefit(plan: JSON, benefit: JSON) -> JSON:
    category = next((c for c in plan["categories"] if c["id"] == benefit["categoryId"]), None)
    pool = next((p for p in plan["limitPools"] if benefit.get("poolId") and p["id"] == benefit["poolId"]), None)
    source = benefit.get("source")
    return {
        "benefitId": benefit["id"],
        "name": benefit["name"],
        "category": category["name"] if category else None,
        "coverage": _coverage_text(benefit["coverage"]),
        "limits": [_limit_text(lim) for lim in benefit.get("limits", [])],
        "pool": f"{pool['name']}: {_limit_text(pool['limit'])}" if pool else None,
        "frequency": _frequency_text(benefit["frequency"]) if benefit.get("frequency") else None,
        "waitingPeriodMonths": (benefit.get("waitingPeriod") or {}).get("months"),
        "requirements": benefit.get("requirements", []),
        "source": {"page": source["page"], "quote": source["quote"]} if source else None,
    }


def _iso_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ISO.match(value):
        raise ToolError(f"{field_name} must be an ISO date (YYYY-MM-DD).")
    return value


def _cents(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolError(f"{field_name} must be a whole number of cents.")
    return value


def _optional_cents(value: Any, field: str) -> int:
    return 0 if value is None else _cents(value, field)


def _terms(text: str) -> list[str]:
    return [t for t in (m.lower() for m in _TERM.findall(text)) if t not in STOP_WORDS]


def _score(query_terms: list[str], fields: list[tuple[str, float]]) -> float:
    """Stands in for MiniSearch; affects ranking only, never numbers."""
    score = 0.0
    for term in query_terms:
        best = 0.0
        for text, boost in fields:
            words = set(_terms(text))
            if term in words:
                best = max(best, boost)
            elif len(term) >= 2 and any(w.startswith(term) for w in words):
                best = max(best, boost * 0.5)
        score += best
    return score


def summarize_claim(plan: JSON, claim: JSON) -> JSON:
    outcome = claim.get("outcome")
    return {
        "claimId": claim["id"],
        "status": claim["status"],
        "memberAlias": _member_alias(plan, claim["patientMemberId"]),
        "provider": claim.get("provider"),
        "serviceDates": sorted({ln["serviceDate"] for ln in claim["lines"]}),
        "benefitIds": list(dict.fromkeys(ln["benefitId"] for ln in claim["lines"])),
        "chargedCents": sum(ln["chargedCents"] for ln in claim["lines"]),
        "paidCents": outcome["paidCents"] if outcome else None,
        "deadline": claim.get("deadline"),
        "updatedAt": claim["updatedAt"],
    }


@dataclass
class BrowserToolExecutor:
    plan: JSON
    claims: list[JSON] = field(default_factory=list)
    today: str = "2026-06-15"
    include_submitted: bool = False
    calls: list[JSON] = field(default_factory=list)
    _drafts: int = 0

    @classmethod
    def for_scenario(cls, scenario: JSON) -> BrowserToolExecutor:
        options = scenario.get("executorOptions", {})
        return cls(
            plan=load_plan(scenario["plan"]),
            claims=copy.deepcopy(scenario.get("seededClaims", [])),
            today=scenario["context"]["today"],
            include_submitted=bool(options.get("includeSubmitted", False)),
        )

    @property
    def now(self) -> str:
        return f"{self.today}T12:00:00.000Z"

    def execute(self, name: str, args: JSON | None) -> Any:
        """Raises ``ToolError`` exactly where ``runBrowserTool`` would."""
        self.calls.append({"name": name, "arguments": args})
        if name not in BROWSER_TOOLS:
            raise ToolError(f"{name} is not a browser tool.")
        try:
            return getattr(self, f"_tool_{name}")(args or {})
        except ToolError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(str(exc) or "The tool failed.") from exc

    def _tool_get_plan_overview(self, args: JSON) -> JSON:
        plan = self.plan
        window = engine.current_benefit_period(plan, self.today)
        return {
            "insurer": plan["insurer"],
            "planName": plan["planName"],
            "region": plan["region"],
            "currency": plan["currency"],
            "today": self.today,
            "benefitPeriod": {"start": window[0], "end": window[1]},
            "members": [{"alias": m["alias"], "relationship": m["relationship"]} for m in plan["members"]],
            "categories": [
                {"name": c["name"], "benefits": [{"benefitId": b["id"], "name": b["name"]} for b in c["benefits"]]}
                for c in plan["categories"]
            ],
            "claimRules": {
                "submissionDays": plan["claimRules"].get("submissionDays"),
                "daysAfterPeriodEnd": plan["claimRules"].get("daysAfterPeriodEnd"),
                "receiptsRequired": plan["claimRules"].get("receiptsRequired", True),
            },
        }

    def _tool_find_benefits(self, args: JSON) -> JSON:
        query = str(args["query"])
        terms = _terms(query)
        hits: list[tuple[float, int, JSON]] = []
        order = 0
        for category in self.plan["categories"]:
            for benefit in category["benefits"]:
                items = benefit["coverage"].get("scheduleItems", [])
                fields = [
                    (benefit["name"], 3.0),
                    (" ".join([*benefit.get("itemCodes", []), *(i["itemCode"] for i in items)]), 3.0),
                    (" ".join(benefit.get("keywords", [])), 2.0),
                    (" ".join(i["description"] for i in items), 1.2),
                    (f"{category['name']} {category['kind'].replace('_', ' ')}", 1.0),
                ]
                score = _score(terms, fields)
                if score > 0:
                    hits.append((score, order, benefit))
                order += 1
        hits.sort(key=lambda h: (-h[0], h[1]))
        return {"query": query, "benefits": [_describe_benefit(self.plan, b) for _, _, b in hits[:MAX_RESULTS]]}

    def _passages(self) -> list[JSON]:
        passages: dict[str, JSON] = {}

        def add(source: JSON | None, text: str | None, benefit_id: str | None) -> None:
            if not source or not text or not text.strip():
                return
            key = f"{source['page']}:{text.strip()}"
            passages.setdefault(key, {"page": source["page"], "quote": text.strip(), "benefitId": benefit_id})

        plan = self.plan
        for benefit in engine.index_plan(plan).benefits.values():
            source = benefit.get("source")
            add(source, (source or {}).get("quote"), benefit["id"])
            add(source, benefit.get("notes"), benefit["id"])
            add(source, benefit["coverage"].get("scheduleNote"), benefit["id"])
            add(source, (benefit.get("waitingPeriod") or {}).get("note"), benefit["id"])
            for requirement in benefit.get("requirements", []):
                add(source, requirement, benefit["id"])
        for pool in plan["limitPools"]:
            add(pool.get("source"), (pool.get("source") or {}).get("quote"), None)
        for share in plan["costShares"]:
            add(share.get("source"), (share.get("source") or {}).get("quote"), None)
        rules = plan["claimRules"]
        add(rules.get("source"), (rules.get("source") or {}).get("quote"), None)
        for note in rules.get("notes", []):
            add(rules.get("source"), note, None)
        if plan["profile"]["kind"] == "AU":
            for category in (plan["profile"].get("hospital") or {}).get("categories", []):
                add(category.get("source"), (category.get("source") or {}).get("quote"), None)
        return list(passages.values())

    def _tool_search_plan_document(self, args: JSON) -> JSON:
        query = str(args["query"])
        terms = _terms(query)
        scored = [(_score(terms, [(p["quote"], 1.0)]), i, p) for i, p in enumerate(self._passages())]
        hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        return {"query": query, "results": [p for _, _, p in hits[:MAX_RESULTS]]}

    def _tool_get_usage(self, args: JSON) -> JSON:
        plan = self.plan
        members = [member_by_alias(plan, args["member_alias"])] if args.get("member_alias") else plan["members"]
        index = engine.index_plan(plan)
        if args.get("benefit_id"):
            benefits = [_benefit_by_id(plan, args["benefit_id"])]
        else:
            benefits = [b for b in index.benefits.values() if b.get("limits") or b.get("poolId") or b.get("frequency")]
        rows: list[JSON] = []
        for benefit in benefits:
            for member in members:
                if len(rows) >= MAX_USAGE_ROWS:
                    break
                usage = engine.benefit_usage(
                    plan,
                    self.claims,
                    benefit["id"],
                    member["id"],
                    today=self.today,
                    include_submitted=self.include_submitted,
                )
                pool = usage["pool"]
                freq = usage["frequency"]
                rows.append(
                    {
                        "benefitId": benefit["id"],
                        "name": benefit["name"],
                        "memberAlias": member["alias"],
                        "remainingCents": usage["remainingCents"],
                        "usedCents": usage["usedCents"],
                        "limits": [
                            {
                                "limit": _limit_text(lu["limit"]),
                                "unit": lu["limit"]["unit"],
                                "used": lu["used"],
                                "remaining": lu["remaining"],
                                "window": engine._window_json(lu["window"]),
                            }
                            for lu in usage["limits"]
                        ],
                        "pool": {
                            "name": pool["name"],
                            "used": pool["used"],
                            "remaining": pool["remaining"],
                            "window": engine._window_json(pool["window"]),
                        }
                        if pool
                        else None,
                        "frequency": {
                            "count": freq["count"],
                            "used": freq["used"],
                            "nextEligible": freq["nextEligible"],
                        }
                        if freq
                        else None,
                        "inWaitingPeriod": usage["inWaitingPeriod"],
                        "waitingPeriodEnds": usage["waitingPeriodEnds"],
                    }
                )
        return {
            "asOf": self.today,
            "includesSubmittedClaims": self.include_submitted,
            "usage": rows,
            "truncated": len(rows) >= MAX_USAGE_ROWS,
        }

    def _tool_estimate_reimbursement(self, args: JSON) -> JSON:
        plan = self.plan
        benefit = _benefit_by_id(plan, args["benefit_id"])
        member = member_by_alias(plan, args["member_alias"])
        result = engine.estimate_reimbursement(
            plan,
            self.claims,
            {
                "benefitId": benefit["id"],
                "memberId": member["id"],
                "serviceDate": _iso_date(args["service_date"], "service_date"),
                "chargedCents": _cents(args["charged_cents"], "charged_cents"),
                "quantity": args.get("quantity"),
                "itemCode": args.get("item_code"),
                "otherPlanPaidCents": _optional_cents(args.get("other_plan_paid_cents"), "other_plan_paid_cents"),
            },
            today=self.today,
            include_submitted=self.include_submitted,
        )
        return {
            "benefitId": benefit["id"],
            "name": benefit["name"],
            "memberAlias": member["alias"],
            "serviceDate": args["service_date"],
            "chargedCents": args["charged_cents"],
            "otherPlanPaidCents": args.get("other_plan_paid_cents") or 0,
            "planPaysCents": result["planPaysCents"],
            "memberPaysCents": result["memberPaysCents"],
            "limitedBy": result["limitedBy"],
            "steps": [{"label": s["label"], "planPaysCents": s["planPaysCents"]} for s in result["steps"]],
            "warnings": result["warnings"],
            "remainingAfter": result["remainingAfter"],
            "deadline": result["deadline"],
        }

    def _tool_list_claims(self, args: JSON) -> JSON:
        matching = [
            c
            for c in self.claims
            if (not args.get("status") or c["status"] == args["status"])
            and (not args.get("benefit_id") or any(ln["benefitId"] == args["benefit_id"] for ln in c["lines"]))
        ]
        matching.sort(key=lambda c: c["updatedAt"], reverse=True)
        return {"total": len(matching), "claims": [summarize_claim(self.plan, c) for c in matching[:MAX_CLAIMS]]}

    def _history(self, status: str, note: str | None) -> JSON:
        return {
            "status": status,
            "at": self.now,
            "actor": "agent",
            "agentName": None,
            "agentVersion": None,
            "traceId": None,
            "note": note,
        }

    def _tool_draft_claim(self, args: JSON) -> JSON:
        plan = self.plan
        if not args.get("lines"):
            raise ToolError("A claim needs at least one line.")
        member = member_by_alias(plan, args["member_alias"])
        self._drafts += 1
        claim_id = f"clm-eval{self._drafts:010d}"
        lines = []
        for i, line in enumerate(args["lines"]):
            quantity = line.get("quantity")
            lines.append(
                {
                    "id": f"{claim_id}-line-{i}",
                    "serviceDate": _iso_date(line["service_date"], f"lines[{i}].service_date"),
                    "benefitId": _benefit_by_id(plan, line["benefit_id"])["id"],
                    "itemCode": (line.get("item_code") or "").strip() or None,
                    "description": (line.get("description") or "").strip() or None,
                    "quantity": quantity if quantity and quantity > 0 else 1,
                    "chargedCents": _cents(line["charged_cents"], f"lines[{i}].charged_cents"),
                    "otherPlanPaidCents": _optional_cents(
                        line.get("other_plan_paid_cents"), f"lines[{i}].other_plan_paid_cents"
                    ),
                }
            )
        deadlines = sorted(d for d in (engine.claim_deadline(plan, ln["serviceDate"]) for ln in lines) if d)
        claim = {
            "id": claim_id,
            "planId": plan["id"],
            "status": "draft",
            "patientMemberId": member["id"],
            "provider": (args.get("provider") or "").strip() or None,
            "lines": lines,
            "outcome": None,
            "attachments": [],
            "history": [self._history("draft", "Drafted by the assistant")],
            "deadline": deadlines[0] if deadlines else None,
            "createdAt": self.now,
            "updatedAt": self.now,
        }
        self.claims.append(claim)
        return {
            "claimId": claim_id,
            "status": "draft",
            "memberAlias": member["alias"],
            "provider": claim["provider"],
            "lines": len(lines),
            "totalChargedCents": sum(ln["chargedCents"] for ln in lines),
            "deadline": claim["deadline"],
        }

    def _tool_update_claim(self, args: JSON) -> JSON:
        claim = next((c for c in self.claims if c["id"] == args["claim_id"]), None)
        if claim is None:
            raise ToolError(f'Unknown claim id "{args["claim_id"]}". Call list_claims first.')
        patch: JSON = {}
        if args.get("status"):
            patch["status"] = args["status"]
        if args.get("provider") is not None:
            patch["provider"] = args["provider"]
        if args.get("paid_cents") is not None:
            patch["outcome"] = {
                "paidCents": _cents(args["paid_cents"], "paid_cents"),
                "decidedOn": self.today,
                "note": args.get("note"),
            }
        elif args.get("note") is not None and claim.get("outcome"):
            patch["outcome"] = {**claim["outcome"], "note": args["note"]}
        if not patch:
            raise ToolError("Nothing to update.")
        status = patch.get("status", claim["status"])
        if status != claim["status"] and status not in TRANSITIONS[claim["status"]]:
            raise ToolError(f"A {claim['status'].replace('_', ' ')} claim can't move to {status.replace('_', ' ')}.")
        if "provider" in patch and claim["status"] != "draft" and status != "draft":
            raise ToolError("Move the claim back to draft before changing its details.")
        updated = {**claim, "status": status}
        if "provider" in patch:
            updated["provider"] = (patch["provider"] or "").strip() or None
        if status in ("paid", "partially_paid", "rejected"):
            outcome = patch.get("outcome") or claim.get("outcome")
            if status == "rejected":
                updated["outcome"] = {
                    "paidCents": 0,
                    "decidedOn": (outcome or {}).get("decidedOn"),
                    "note": (outcome or {}).get("note"),
                }
            else:
                if not outcome:
                    raise ToolError("Enter the amount the plan paid.")
                if outcome["paidCents"] > sum(ln["chargedCents"] for ln in updated["lines"]):
                    raise ToolError("The plan can't pay more than was charged.")
                updated["outcome"] = outcome
        else:
            if patch.get("outcome"):
                raise ToolError("Record an outcome only once the claim is paid, partially paid or rejected.")
            updated["outcome"] = None
        changed = status != claim["status"]
        updated["history"] = [*claim["history"], self._history(status, None if changed else "Updated by the assistant")]
        updated["updatedAt"] = self.now
        self.claims[self.claims.index(claim)] = updated
        return summarize_claim(self.plan, updated)
