"""Deterministic chunk rows → ``Plan`` (no LLM); every guess or default becomes an issue for the review screen."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, TypeVar

from pydantic import ValidationError
from rapidfuzz import fuzz

from app.models.api import (
    AssembleRequest,
    AssembleResponse,
    ExBenefitRow,
    ExCostShareRow,
    ExHeaderRow,
    ExHospitalCategoryRow,
    ExPoolRow,
    ExRuleRow,
    Issue,
    IssueCode,
)
from app.models.plan import (
    CA_PROVINCES,
    AUProfile,
    Benefit,
    CAProfile,
    Category,
    CategoryKind,
    ClaimRules,
    CostShare,
    Coverage,
    Frequency,
    HealthSpendingAccount,
    HospitalCategory,
    HospitalCover,
    HospitalExcess,
    Limit,
    LimitPool,
    Member,
    Period,
    Plan,
    PlanDocument,
    ScheduleItem,
    SourceRef,
    WaitingPeriod,
)
from app.pipelines.grounding import normalize_text
from app.services.language_pii import find_alias_tokens, normalize_aliases

DUPLICATE_QUOTE_SCORE = 90
LINK_SCORE = 88
RowT = TypeVar("RowT", ExHeaderRow, ExBenefitRow, ExPoolRow, ExCostShareRow, ExRuleRow, ExHospitalCategoryRow)
Severity = Literal["info", "warning", "error"]

CATEGORY_KINDS: tuple[str, ...] = (
    "paramedical",
    "vision",
    "dental",
    "drugs",
    "hospital",
    "extras",
    "medical_equipment",
    "ambulance",
    "travel",
    "hsa",
    "other",
)
KIND_KEYWORDS: tuple[tuple[CategoryKind, tuple[str, ...]], ...] = (
    ("hsa", ("health spending", "spending account", "hsa", "wellness account")),
    ("dental", ("dental", "dentist", "orthodont")),
    ("vision", ("vision", "optical", "eye", "glasses", "optometr")),
    ("drugs", ("drug", "prescription", "pharmac", "medication")),
    ("ambulance", ("ambulance",)),
    ("hospital", ("hospital",)),
    ("travel", ("travel", "out-of-province", "out of province", "out-of-country", "out of country")),
    ("medical_equipment", ("equipment", "supplies", "orthotic", "appliance", "prosthe", "hearing aid", "mobility")),
    (
        "paramedical",
        (
            "paramedical",
            "practitioner",
            "massage",
            "physio",
            "chiropract",
            "naturopath",
            "psycholog",
            "therap",
            "counsel",
            "osteopath",
            "acupunct",
            "podiatr",
            "speech",
        ),
    ),
    ("extras", ("extras", "general treatment")),
)
PROVINCE_NAMES = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
    "newfoundland and labrador": "NL", "newfoundland": "NL", "nova scotia": "NS", "northwest territories": "NT",
    "nunavut": "NU", "ontario": "ON", "prince edward island": "PE", "quebec": "QC", "québec": "QC",
    "saskatchewan": "SK", "yukon": "YT",
}  # fmt: skip
PERSON_STEMS = {"MEMBER", "PERSON", "NAME", "DEPENDENT", "SPOUSE", "CHILD", "PATIENT", "EMPLOYEE"}
IDENTIFIER_STEMS = {"POLICY", "CERT", "CERTIFICATE", "GROUP", "ID", "CARD", "ACCOUNT", "MEMBERSHIP", "CONTRACT", "PLAN"}
DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%b %d %Y", "%d %b %Y", "%Y/%m/%d")


def slugify(text: str, fallback: str = "item") -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:48].strip("-") or fallback


def dollars_to_cents(value: float | None) -> int | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_list(text: str | None, separators: str = r"[,;\n]") -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(part.strip() for part in re.split(separators, text) if part.strip()))


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    cleaned = text.strip()
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


_SUFFIXES = ("ations", "ation", "ists", "ist", "ics", "ic", "ors", "or", "ies", "y", "es", "s")


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def names_match(a: str, b: str) -> float:
    """0–100 similarity of two benefit/pool/category names (word order and simple suffixes ignored)."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    sa, sb = " ".join(_stem(w) for w in na.split()), " ".join(_stem(w) for w in nb.split())
    return max(
        float(fuzz.token_sort_ratio(na, nb)),
        float(fuzz.token_set_ratio(na, nb)) - 5,
        float(fuzz.token_sort_ratio(sa, sb)),
    )


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _confidence(row: Any) -> float:
    return float(row.meta.confidence)


def row_identity(row: Any) -> str:
    """What a row is about; two rows are duplicates only when this matches (one quote can back several rows)."""
    if isinstance(row, ExBenefitRow):
        return f"{slugify(row.category_name)}/{slugify(row.benefit_name)}/{row.item_codes or ''}"
    if isinstance(row, ExPoolRow):
        return slugify(row.pool_name)
    if isinstance(row, ExCostShareRow | ExHospitalCategoryRow):
        return slugify(row.name)
    if isinstance(row, ExRuleRow):
        return row.rule_kind
    return "header"


def source_of(row: Any) -> SourceRef | None:
    if row.page < 1:
        return None
    return SourceRef(
        page=row.page, quote=row.quote[:600], confidence=row.meta.confidence, verifierVerdict=row.meta.verifierVerdict
    )


class Assembler:
    def __init__(self, request: AssembleRequest) -> None:
        self.request = request
        self.issues: list[Issue] = []

    def issue(
        self, code: IssueCode, severity: Severity, message: str, *, page: int | None = None, row_id: str | None = None
    ) -> None:
        self.issues.append(Issue(code=code, severity=severity, message=message, page=page, rowId=row_id))

    def collect(self, attr: str) -> list[Any]:
        return [row for chunk in self.request.chunks for row in getattr(chunk.rows, attr)]

    def dedupe(self, rows: Sequence[RowT]) -> list[RowT]:
        order = {id(row): i for i, row in enumerate(rows)}
        kept: list[RowT] = []
        removed: dict[int, int] = {}
        for row in sorted(rows, key=lambda r: (-_confidence(r), order[id(r)])):
            quote, identity = normalize_text(row.quote), row_identity(row)
            duplicate = any(
                k.page == row.page
                and row_identity(k) == identity
                and fuzz.ratio(normalize_text(k.quote), quote) >= DUPLICATE_QUOTE_SCORE
                for k in kept
            )
            if duplicate:
                removed[row.page] = removed.get(row.page, 0) + 1
            else:
                kept.append(row)
        for page, count in sorted(removed.items()):
            self.issue(
                "duplicate_removed", "info", f"Removed {count} duplicate row(s) from the chunk overlap.", page=page
            )
        return sorted(kept, key=lambda r: (r.page, order[id(r)]))

    def period(self, kind: str | None, months: int | None, default: Period, *, row: Any, what: str) -> Period:
        period, guessed = map_period(kind, months, default)
        if guessed:
            self.issue(
                "assumed_default",
                "info",
                f"{what}: period not recognised; assumed {period.kind.replace('_', ' ')}.",
                page=row.page,
                row_id=row.meta.rowId,
            )
        return period

    def build(self) -> AssembleResponse:
        req = self.request
        headers = self.dedupe(self.collect("header"))
        benefits = self.dedupe(self.collect("benefits"))
        pools = self.dedupe(self.collect("pools"))
        cost_shares = self.dedupe(self.collect("cost_shares"))
        rules = self.dedupe(self.collect("rules"))
        hospital_rows = self.dedupe(self.collect("hospital_categories"))
        self.report_uncovered_pages()

        header = HeaderView(headers)
        currency = header.value("currency")
        if currency not in ("CAD", "AUD"):
            currency = "CAD" if req.region == "CA" else "AUD"
            self.issue("assumed_default", "info", f"Currency not stated; assumed {currency}.")
        insurer = header.value("insurer")
        if not insurer:
            insurer = "Unknown insurer"
            self.issue("field_missing", "warning", "Insurer name not found.")
        plan_name = header.value("plan_name")
        if not plan_name:
            plan_name = req.documentName
            self.issue("field_missing", "info", "Plan name not found; using the document name.")
        effective = parse_date(header.value("effective_date"))
        if header.value("effective_date") and effective is None:
            self.issue("assumed_default", "info", "Effective date could not be read.")
        plan_period = self.plan_period(header, effective)

        categories, benefit_index = self.build_categories(benefits, plan_period)
        limit_pools = self.build_pools(pools, benefits, benefit_index, plan_period)
        costs = self.build_cost_shares(cost_shares, categories, benefit_index, plan_period)
        claim_rules = self.build_rules(rules)
        members, identifiers = self.members_and_identifiers()
        profile = self.build_profile(header, categories, costs, hospital_rows, rules)

        digest = hashlib.sha256(
            f"{req.region}|{insurer}|{plan_name}|{req.documentName}|{req.pageCount}".encode()
        ).hexdigest()[:10]
        plan = Plan(
            id=f"plan-{req.region.lower()}-{digest}",
            region=req.region,
            insurer=insurer,
            planName=plan_name,
            currency=currency,
            identifiers=identifiers,
            members=members,
            effectiveDate=effective,
            benefitPeriod=plan_period,
            categories=categories,
            limitPools=limit_pools,
            costShares=costs,
            claimRules=claim_rules,
            profile=profile,
            document=PlanDocument(
                name=req.documentName,
                pageCount=req.pageCount,
                extractedAt=datetime.now(UTC).isoformat(timespec="seconds"),
                isDemo=False,
            ),
        )
        return AssembleResponse(plan=plan, issues=self.issues)

    def report_uncovered_pages(self) -> None:
        covered = {p for chunk in self.request.chunks for p in chunk.pages}
        missing = [p for p in range(1, self.request.pageCount + 1) if p not in covered]
        if missing:
            listed = ", ".join(str(p) for p in missing[:20]) + ("…" if len(missing) > 20 else "")
            self.issue("page_excluded", "warning", f"Pages not analyzed: {listed}.")

    def plan_period(self, header: HeaderView, effective: date | None) -> Period:
        kind = header.value("benefit_period_kind")
        month = header.value("benefit_period_start_month")
        start_month = month if isinstance(month, int) and 1 <= month <= 12 else None
        if kind == "policy_anniversary":
            if effective is not None:
                return Period(kind="policy_anniversary", startMonth=effective.month, startDay=effective.day)
            self.issue(
                "assumed_default", "warning", "Policy-year plan without an effective date; assumed calendar year."
            )
            return Period(kind="benefit_year", startMonth=1, startDay=1)
        if kind is None:
            self.issue("assumed_default", "info", "Benefit period not stated; assumed a calendar year.")
        return Period(kind="benefit_year", startMonth=start_month or 1, startDay=1)

    def category_kind(self, row: ExBenefitRow) -> CategoryKind:
        region = self.request.region
        stated = (row.category_kind or "").strip().lower()
        if stated in CATEGORY_KINDS:
            return stated  # type: ignore[return-value]
        haystack = f"{row.category_name} {row.benefit_name}".lower()
        for kind, words in KIND_KEYWORDS:
            if any(w in haystack for w in words):
                inferred: CategoryKind = "extras" if (kind == "paramedical" and region == "AU") else kind
                return inferred
        self.issue(
            "assumed_default",
            "info",
            "Category type not recognised; filed under other.",
            page=row.page,
            row_id=row.meta.rowId,
        )
        return "other"

    def build_categories(
        self, rows: list[ExBenefitRow], plan_period: Period
    ) -> tuple[list[Category], dict[str, Benefit]]:
        groups: dict[tuple[str, str], list[ExBenefitRow]] = {}
        category_names: dict[str, str] = {}
        category_kinds: dict[str, CategoryKind] = {}
        for row in rows:
            cat_id = f"cat-{slugify(row.category_name, 'other')}"
            category_names.setdefault(cat_id, row.category_name.strip() or "Other")
            if cat_id not in category_kinds:
                category_kinds[cat_id] = self.category_kind(row)
            groups.setdefault((cat_id, slugify(row.benefit_name, "benefit")), []).append(row)

        categories: dict[str, Category] = {}
        benefit_index: dict[str, Benefit] = {}
        used_ids: set[str] = set()
        for (cat_id, name_slug), group in groups.items():
            benefit_id = f"ben-{name_slug}"
            if benefit_id in used_ids:
                benefit_id = f"ben-{cat_id.removeprefix('cat-')}-{name_slug}"
            suffix = 2
            while benefit_id in used_ids:
                benefit_id, suffix = f"ben-{cat_id.removeprefix('cat-')}-{name_slug}-{suffix}", suffix + 1
            try:
                benefit = self.build_benefit(benefit_id, cat_id, group, plan_period)
            except ValidationError:
                primary = group[0]
                self.issue(
                    "field_missing", "warning", "A benefit row could not be converted and was skipped.",
                    page=primary.page, row_id=primary.meta.rowId,
                )  # fmt: skip
                continue
            used_ids.add(benefit_id)
            benefit_index[benefit_id] = benefit
            category = categories.setdefault(
                cat_id, Category(id=cat_id, name=category_names[cat_id], kind=category_kinds[cat_id])
            )
            category.benefits.append(benefit)
        return list(categories.values()), benefit_index

    def build_benefit(self, benefit_id: str, cat_id: str, group: list[ExBenefitRow], plan_period: Period) -> Benefit:
        ranked = sorted(group, key=lambda r: -_confidence(r))
        schedule_rows = [
            r
            for r in ranked
            if r.coverage_kind == "schedule" and len(split_list(r.item_codes)) == 1 and r.coverage_amount
        ]
        primary = next((r for r in ranked if r not in schedule_rows), ranked[0])
        if len(group) > 1 and len(schedule_rows) < len(group):
            self.issue(
                "duplicate_removed", "info", f"Merged {len(group)} rows describing the same benefit.",
                page=primary.page, row_id=primary.meta.rowId,
            )  # fmt: skip

        def first(attr: str) -> Any:
            for row in [primary, *ranked]:
                value = getattr(row, attr)
                if value not in (None, ""):
                    return value
            return None

        note_parts = split_notes(first("notes"))
        schedule_items: list[ScheduleItem] = list(note_parts.schedule)
        seen_codes: set[str] = {item.itemCode for item in schedule_items}
        # Also accept one row per item number (item_codes = the code, coverage_amount = its benefit).
        for row in sorted(schedule_rows, key=lambda r: (r.page, split_list(r.item_codes)[0])):
            code = split_list(row.item_codes)[0]
            cents = dollars_to_cents(row.coverage_amount)
            if code in seen_codes or cents is None:
                continue
            seen_codes.add(code)
            description = (row.notes or row.benefit_name)[:200]
            schedule_items.append(ScheduleItem(itemCode=code, description=description, benefitCents=cents))

        coverage = self.build_coverage(primary, first, schedule_items, note_parts.coverage_note)
        limits: list[Limit] = []
        unit, value = first("limit_unit"), first("limit_value")
        if value is not None and unit is None:
            self.issue(
                "field_missing",
                "warning",
                "Limit amount without a unit was ignored.",
                page=primary.page,
                row_id=primary.meta.rowId,
            )
        elif value is not None and unit is not None:
            limit_value = dollars_to_cents(value) if unit == "dollars" else round(value)
            if limit_value is not None and limit_value >= 0:
                limits.append(
                    Limit(
                        unit="cents" if unit == "dollars" else unit,
                        value=limit_value,
                        period=self.period(
                            first("limit_period_kind"),
                            first("limit_period_months"),
                            plan_period,
                            row=primary,
                            what="Limit",
                        ),
                        scope=first("limit_scope") or "per_person",
                    )
                )

        frequency = None
        count = first("frequency_count")
        if isinstance(count, int) and count >= 1:
            frequency = Frequency(
                count=count,
                period=self.period(
                    first("frequency_period_kind"),
                    first("frequency_period_months"),
                    plan_period,
                    row=primary,
                    what="Frequency",
                ),
                scope=first("limit_scope") or "per_person",
            )

        waiting = first("waiting_period_months")
        waiting_period = (
            WaitingPeriod(months=_clamp(int(waiting), 0, 60), note=note_parts.waiting_note)
            if waiting is not None
            else None
        )
        remaining_notes = note_parts.notes
        if waiting is None and note_parts.waiting_note:
            remaining_notes = " ".join(filter(None, [remaining_notes, f"Waiting period: {note_parts.waiting_note}."]))

        keywords = list(dict.fromkeys(k for r in ranked for k in split_list(r.keywords)))
        item_codes = list(
            dict.fromkeys(
                [*(c for r in ranked for c in split_list(r.item_codes)), *(i.itemCode for i in schedule_items)]
            )
        )
        requirements = list(dict.fromkeys(q for r in ranked for q in split_list(r.requirements, r"[;\n]")))
        return Benefit(
            id=benefit_id,
            categoryId=cat_id,
            name=primary.benefit_name.strip() or "Benefit",
            keywords=keywords,
            itemCodes=item_codes,
            coverage=coverage,
            limits=limits,
            frequency=frequency,
            waitingPeriod=waiting_period,
            requirements=requirements,
            notes=remaining_notes,
            source=source_of(primary),
        )

    def build_coverage(
        self, row: ExBenefitRow, first: Any, schedule_items: list[ScheduleItem], coverage_note: str | None
    ) -> Coverage:
        percent = first("coverage_percent")
        if percent is not None and 0 < percent < 1:
            percent = percent * 100
            self.issue(
                "assumed_default",
                "info",
                "Coverage looked like a fraction; read it as a percentage.",
                page=row.page,
                row_id=row.meta.rowId,
            )
        if percent is not None:
            percent = max(0.0, min(100.0, float(percent)))
        cap = dollars_to_cents(first("coverage_cap_amount"))
        amount = dollars_to_cents(first("coverage_amount"))
        kind = row.coverage_kind or next((r for r in [first("coverage_kind")] if r), None)
        if schedule_items:
            kind = "schedule"
        if kind is None:
            if percent is not None and cap is not None:
                kind = "percent_capped"
            elif percent is not None:
                kind = "percent"
            elif amount is not None:
                kind = "fixed_per_service"
            else:
                kind = "percent"
                self.issue(
                    "field_missing",
                    "warning",
                    "Coverage not stated; please enter it.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
            if percent is not None or amount is not None:
                self.issue(
                    "assumed_default",
                    "info",
                    f"Coverage type inferred as {kind.replace('_', ' ')}.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
        if kind == "percent_capped" and cap is None:
            kind = "percent"
            self.issue(
                "field_missing",
                "info",
                "Per-visit cap not stated; treated as a plain percentage.",
                page=row.page,
                row_id=row.meta.rowId,
            )
        if kind in ("fixed_per_service", "per_diem") and amount is None:
            self.issue(
                "field_missing", "warning", "Fixed benefit amount not stated.", page=row.page, row_id=row.meta.rowId
            )
        return Coverage(
            kind=kind,
            percent=percent,
            capCents=cap if kind == "percent_capped" else None,
            amountCents=amount if kind in ("fixed_per_service", "per_diem") else None,
            scheduleNote=coverage_note or ("Fixed benefit per item number" if schedule_items else None),
            scheduleItems=schedule_items,
        )

    def match_benefit(self, name: str, benefit_index: dict[str, Benefit]) -> str | None:
        best_id, best = None, 0.0
        for benefit_id, benefit in benefit_index.items():
            score = max(names_match(name, benefit.name), *(names_match(name, k) - 3 for k in benefit.keywords[:8]), 0.0)
            if score > best:
                best_id, best = benefit_id, score
        return best_id if best >= LINK_SCORE else None

    def build_pools(
        self,
        rows: list[ExPoolRow],
        benefit_rows: list[ExBenefitRow],
        benefit_index: dict[str, Benefit],
        plan_period: Period,
    ) -> list[LimitPool]:
        pools: dict[str, LimitPool] = {}
        pool_names: dict[str, str] = {}
        for row in sorted(rows, key=lambda r: -_confidence(r)):
            pool_id = f"pool-{slugify(row.pool_name, 'pool')}"
            if pool_id in pools:
                continue
            if row.limit_value is None or row.limit_unit is None:
                self.issue(
                    "field_missing",
                    "warning",
                    "Combined maximum without an amount was skipped.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
                continue
            value = dollars_to_cents(row.limit_value) if row.limit_unit == "dollars" else round(row.limit_value)
            if value is None or value < 0:
                continue
            pools[pool_id] = LimitPool(
                id=pool_id,
                name=row.pool_name.strip(),
                limit=Limit(
                    unit="cents" if row.limit_unit == "dollars" else row.limit_unit,
                    value=value,
                    period=self.period(
                        row.period_kind, row.period_months, plan_period, row=row, what="Combined maximum"
                    ),
                    scope=row.scope or "per_person",
                ),
                source=source_of(row),
            )
            pool_names[pool_id] = row.pool_name
            for name in split_list(row.benefit_names):
                benefit_id = self.match_benefit(name, benefit_index)
                if benefit_id is None:
                    self.issue(
                        "unlinked_pool",
                        "warning",
                        "A benefit named in a combined maximum was not found.",
                        page=row.page,
                        row_id=row.meta.rowId,
                    )
                elif benefit_id not in pools[pool_id].benefitIds:
                    pools[pool_id].benefitIds.append(benefit_id)

        for brow in benefit_rows:
            if not brow.pool_name:
                continue
            match = max(
                ((pid, names_match(brow.pool_name, name)) for pid, name in pool_names.items()),
                key=lambda x: x[1],
                default=None,
            )
            benefit_id = self.match_benefit(brow.benefit_name, benefit_index)
            if match is None or match[1] < LINK_SCORE or benefit_id is None:
                self.issue(
                    "unlinked_pool",
                    "warning",
                    "Benefit refers to a combined maximum that was not found.",
                    page=brow.page,
                    row_id=brow.meta.rowId,
                )
                continue
            if benefit_id not in pools[match[0]].benefitIds:
                pools[match[0]].benefitIds.append(benefit_id)

        for pool in pools.values():
            for benefit_id in pool.benefitIds:
                benefit = benefit_index[benefit_id]
                if benefit.poolId is None:
                    benefit.poolId = pool.id
                elif benefit.poolId != pool.id:
                    self.issue(
                        "unlinked_pool", "info", "Benefit appears in more than one combined maximum; kept the first."
                    )
        return list(pools.values())

    def build_cost_shares(
        self,
        rows: list[ExCostShareRow],
        categories: list[Category],
        benefit_index: dict[str, Benefit],
        plan_period: Period,
    ) -> list[CostShare]:
        shares: dict[str, CostShare] = {}
        for row in sorted(rows, key=lambda r: -_confidence(r)):
            share_id = f"cs-{slugify(row.name, 'cost-share')}"
            if share_id in shares:
                continue
            kind = row.kind or infer_cost_share_kind(row.name, self.request.region)
            if row.kind is None:
                self.issue(
                    "assumed_default",
                    "info",
                    f"Cost share type inferred as {kind}.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
            period = (
                self.period(row.period_kind, None, plan_period, row=row, what="Cost share")
                if row.period_kind
                else (plan_period if kind in ("deductible", "excess") else None)
            )
            percent = max(0.0, min(100.0, row.percent)) if row.percent is not None else None
            share = CostShare(
                id=share_id,
                name=row.name.strip(),
                kind=kind,
                amountCents=dollars_to_cents(row.amount),
                percent=percent,
                period=period,
                scope=row.scope or "per_person",
                source=source_of(row),
            )
            for target in split_list(row.applies_to):
                category = max(
                    (
                        (c, max(names_match(target, c.name), names_match(target, c.kind.replace("_", " "))))
                        for c in categories
                    ),
                    key=lambda x: x[1],
                    default=None,
                )
                if category is not None and category[1] >= LINK_SCORE:
                    if category[0].id not in share.appliesToCategoryIds:
                        share.appliesToCategoryIds.append(category[0].id)
                    continue
                benefit_id = self.match_benefit(target, benefit_index)
                if benefit_id is not None:
                    if benefit_id not in share.appliesToBenefitIds:
                        share.appliesToBenefitIds.append(benefit_id)
                    continue
                self.issue(
                    "unlinked_cost_share",
                    "warning",
                    "A cost share target was not found.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
            if not row.applies_to:
                self.issue(
                    "unlinked_cost_share",
                    "info",
                    "Cost share does not say what it applies to.",
                    page=row.page,
                    row_id=row.meta.rowId,
                )
            shares[share_id] = share
            for category in categories:
                for benefit in category.benefits:
                    applies = category.id in share.appliesToCategoryIds or benefit.id in share.appliesToBenefitIds
                    if applies and share.id not in benefit.costShareIds:
                        benefit.costShareIds.append(share.id)
        return list(shares.values())

    def build_rules(self, rows: list[ExRuleRow]) -> ClaimRules:
        rules = ClaimRules()
        for row in sorted(rows, key=lambda r: (-_confidence(r), r.page)):
            if row.rule_kind == "submission_deadline":
                days = row.deadline_days
                basis = row.deadline_basis
                if days is None:
                    self.issue(
                        "field_missing",
                        "warning",
                        "Claim deadline without a number of days.",
                        page=row.page,
                        row_id=row.meta.rowId,
                    )
                    continue
                if basis is None:
                    basis = (
                        "period_end"
                        if re.search(r"end of (the )?(benefit|calendar|policy|plan) year", row.quote, re.I)
                        else "service_date"
                    )
                    self.issue(
                        "assumed_default",
                        "info",
                        "Deadline basis inferred from the wording.",
                        page=row.page,
                        row_id=row.meta.rowId,
                    )
                if basis == "service_date" and rules.submissionDays is None and 1 <= days <= 1095:
                    rules.submissionDays = days
                elif basis == "period_end" and rules.daysAfterPeriodEnd is None and 0 <= days <= 730:
                    rules.daysAfterPeriodEnd = days
                else:
                    continue
                if rules.source is None:
                    rules.source = source_of(row)
            elif row.rule_kind == "receipts_required":
                rules.receiptsRequired = True
            elif row.rule_kind == "coordination_of_benefits":
                rules.coordinationOfBenefits = True
                if row.text:
                    rules.notes.append(row.text.strip()[:300])
            elif row.text and len(rules.notes) < 10:
                rules.notes.append(row.text.strip()[:300])
        rules.notes = list(dict.fromkeys(rules.notes))
        if rules.submissionDays is None and rules.daysAfterPeriodEnd is None:
            self.issue("field_missing", "info", "No claim submission deadline found.")
        return rules

    def members_and_identifiers(self) -> tuple[list[Member], list[str]]:
        texts: list[str] = []
        for chunk in self.request.chunks:
            for attr in ("header", "benefits", "pools", "cost_shares", "rules", "hospital_categories"):
                for row in getattr(chunk.rows, attr):
                    texts.extend(v for v in row.model_dump(exclude={"meta"}).values() if isinstance(v, str))
        joined = "\n".join(texts)
        tokens = sorted(set(find_alias_tokens(joined)))
        members: list[Member] = []
        identifiers: list[str] = []
        guessed = False
        for token in tokens:
            stem, _, suffix = token[1:-1].rpartition("_")
            if stem in IDENTIFIER_STEMS:
                identifiers.append(token)
                continue
            if stem not in PERSON_STEMS:
                continue
            relationship = relationship_hint(token, stem, texts)
            if relationship is None:
                relationship = "self" if not members else "dependent"
                guessed = True
            member_id = f"m-{suffix.lower()}" if stem == "MEMBER" else f"m-{stem.lower()}-{suffix.lower()}"
            members.append(Member(id=member_id, alias=token, relationship=relationship))
        if guessed:
            self.issue("assumed_default", "info", "Member relationships were guessed from alias order; please check.")
        return members, identifiers

    def build_profile(
        self,
        header: HeaderView,
        categories: list[Category],
        costs: list[CostShare],
        hospital_rows: list[ExHospitalCategoryRow],
        rules: list[ExRuleRow],
    ) -> CAProfile | AUProfile:
        if self.request.region == "CA":
            raw = str(header.value("province") or "").strip()
            province = raw.upper() if raw.upper() in CA_PROVINCES else PROVINCE_NAMES.get(raw.lower())
            if province is None:
                province = "ON"
                self.issue("assumed_default", "warning", "Province not stated; assumed Ontario. Please check.")
            credit = dollars_to_cents(header.value("hsa_annual_credit"))
            carry = header.value("hsa_carry_forward_years")
            hsa = (
                HealthSpendingAccount(annualCreditCents=credit, carryForwardYears=_clamp(int(carry or 0), 0, 2))
                if credit is not None
                else None
            )
            spouse_plan = any(
                r.rule_kind == "coordination_of_benefits"
                and re.search(r"\b(spouse|partner)\b", f"{r.quote} {r.text or ''}", re.I)
                for r in rules
            )
            return CAProfile(province=province, hsa=hsa, spousePlan=spouse_plan)  # type: ignore[arg-type]

        excess_share = next((c for c in costs if c.kind == "excess"), None)
        has_hospital = bool(hospital_rows) or header.value("hospital_tier") is not None or excess_share is not None
        has_extras = any(c.kind not in ("hospital", "ambulance") for c in categories)
        cover_type = header.value("cover_type")
        if cover_type is None:
            cover_type = "combined" if has_hospital and has_extras else ("hospital" if has_hospital else "extras")
            self.issue("assumed_default", "info", f"Cover type inferred as {cover_type}.")
        hospital = None
        if cover_type in ("hospital", "combined"):
            tier = header.value("hospital_tier")
            if tier is None:
                tier = "basic"
                self.issue("assumed_default", "warning", "Hospital tier not stated; assumed basic. Please check.")
            hospital_categories: list[HospitalCategory] = []
            seen: set[str] = set()
            for row in hospital_rows:
                key = slugify(row.name)
                if key in seen:
                    continue
                seen.add(key)
                if row.status is None:
                    self.issue(
                        "assumed_default",
                        "info",
                        "Clinical category status not stated; assumed covered.",
                        page=row.page,
                        row_id=row.meta.rowId,
                    )
                hospital_categories.append(
                    HospitalCategory(name=row.name.strip(), status=row.status or "covered", source=source_of(row))
                )
            excess = None
            if excess_share is not None and excess_share.amountCents is not None:
                excess = hospital_excess(excess_share)
            hospital = HospitalCover(
                tier=tier, plus=bool(header.value("hospital_plus")), categories=hospital_categories, excess=excess
            )
        return AUProfile(coverType=cover_type, hospital=hospital)


class HeaderView:
    def __init__(self, rows: Iterable[ExHeaderRow]) -> None:
        self.rows = sorted(rows, key=lambda r: -_confidence(r))

    def value(self, attr: str) -> Any:
        for row in self.rows:
            value = getattr(row, attr)
            if value not in (None, ""):
                return value
        return None


def map_period(kind: str | None, months: int | None, default: Period) -> tuple[Period, bool]:
    """Free-text period → ``Period``; the bool is True when the result is a guess."""
    text = re.sub(r"[\s\-]+", "_", (kind or "").strip().lower())
    months = _clamp(months, 1, 120) if isinstance(months, int) and months > 0 else None
    digits = re.search(r"\d+", text)
    number = int(digits.group()) if digits else None
    if not text:
        return default, True
    if "calendar" in text:
        return Period(kind="benefit_year", startMonth=1, startDay=1), False
    if "consecutive" in text:
        years = round(months / 12) if months else number
        start = {"startMonth": default.startMonth, "startDay": default.startDay}
        if years:
            return Period(kind="consecutive_benefit_years", years=_clamp(years, 1, 10), **start), False
        return Period(kind="consecutive_benefit_years", years=2, **start), True
    if "anniversary" in text or "policy_year" in text or "membership_year" in text:
        return Period(kind="policy_anniversary"), False
    if "lifetime" in text:
        return Period(kind="lifetime"), False
    if "admission" in text or "hospital_stay" in text:
        return Period(kind="per_admission"), False
    if any(w in text for w in ("visit", "treatment", "service", "trip", "occurrence", "session")):
        return Period(kind="per_visit"), False
    if "rolling" in text or "month" in text:
        length = months or number
        if length:
            return Period(kind="rolling_months", months=_clamp(length, 1, 120)), False
        return default, True
    if text in (
        "benefit_year",
        "per_benefit_year",
        "plan_year",
        "year",
        "yearly",
        "annual",
        "annually",
        "per_year",
        "benefit_period",
    ):
        return default, False
    if "year" in text and number:
        return Period(kind="rolling_months", months=_clamp(number * 12, 1, 120)), True
    if "year" in text:
        return default, False
    return default, True


SCHEDULE_ITEM_RE = re.compile(
    r"^\s*(?P<code>[A-Za-z0-9][A-Za-z0-9-]*)\s+(?P<description>.+?)\s+\$\s?(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*$"
)
COVERAGE_NOTE_RE = re.compile(r"fee guide|reimbursed up to|\bfees\b|after the excess", re.I)


@dataclass(frozen=True)
class NoteParts:
    schedule: tuple[ScheduleItem, ...] = ()
    waiting_note: str | None = None
    coverage_note: str | None = None
    notes: str | None = None


def split_notes(notes: str | None) -> NoteParts:
    """``Schedule: 500 Consultation $55; …`` → items, ``Waiting period: …`` → waiting note, fee wording →
    ``Coverage.scheduleNote``, the rest → ``notes``."""
    if not notes or not notes.strip():
        return NoteParts()
    schedule: list[ScheduleItem] = []
    waiting: str | None = None
    coverage: list[str] = []
    rest: list[str] = []
    for sentence in re.split(r"(?<=\.)\s+(?=[A-Z])", notes.strip()):
        body = sentence.strip()
        if match := re.match(r"schedule\s*:\s*(.+)$", body, re.I | re.S):
            for part in match.group(1).rstrip(".").split(";"):
                item = SCHEDULE_ITEM_RE.match(part)
                cents = dollars_to_cents(float(item.group("amount").replace(",", ""))) if item else None
                if item and cents is not None:
                    schedule.append(
                        ScheduleItem(
                            itemCode=item.group("code"), description=item.group("description"), benefitCents=cents
                        )
                    )
        elif match := re.match(r"waiting period\s*:\s*(.+?)\.?$", body, re.I | re.S):
            text = match.group(1).strip()
            waiting = text[:1].upper() + text[1:]
        elif COVERAGE_NOTE_RE.search(body):
            coverage.append(body.rstrip("."))
        else:
            rest.append(body)
    return NoteParts(
        schedule=tuple(schedule),
        waiting_note=waiting,
        coverage_note="; ".join(coverage) or None,
        notes=" ".join(rest) or None,
    )


def hospital_excess(share: CostShare) -> HospitalExcess:
    per: Literal["admission", "year"] = "admission" if share.period and share.period.kind == "per_admission" else "year"
    quote = share.source.quote if share.source else ""
    capped_yearly = re.search(r"\b(once|no more than)\b[^.]*\b(each|per|a|every)\s+(calendar\s+)?year", quote, re.I)
    amount = share.amountCents or 0
    return HospitalExcess(
        amountCents=amount, per=per, maxPerYearCents=amount if per == "year" or capped_yearly else None
    )


def infer_cost_share_kind(name: str, region: str) -> Literal["deductible", "excess", "copay", "coinsurance"]:
    lowered = name.lower()
    if "excess" in lowered:
        return "excess"
    if re.search(r"co-?pay", lowered):
        return "copay"
    if re.search(r"co-?insurance", lowered):
        return "coinsurance"
    if "deductible" in lowered:
        return "deductible"
    return "excess" if region == "AU" else "deductible"


RELATIONSHIP_LABELS: tuple[tuple[str, Literal["self", "spouse", "child", "dependent"]], ...] = (
    (r"spouse|partner|husband|wife", "spouse"),
    (r"child|son|daughter", "child"),
    (r"dependa?e?nts?", "dependent"),
    (r"plan member|policy ?holder|employee|primary( member)?|main member|principal|adult 1|member 1|insured", "self"),
)


def relationship_hint(
    token: str, stem: str, texts: Sequence[str]
) -> Literal["self", "spouse", "child", "dependent"] | None:
    """Relationship from a label next to the alias: ``Spouse: [MEMBER_B]`` or ``[MEMBER_C] (child)``."""
    by_stem: dict[str, Literal["self", "spouse", "child", "dependent"]] = {
        "SPOUSE": "spouse",
        "CHILD": "child",
        "DEPENDENT": "dependent",
        "EMPLOYEE": "self",
    }
    if stem in by_stem:
        return by_stem[stem]
    for text in texts:
        normalized = normalize_aliases(text)
        position = normalized.find(token)
        while position >= 0:
            before = normalized[max(0, position - 32) : position].lower()
            after = normalized[position + len(token) : position + len(token) + 20].lower()
            best: Literal["self", "spouse", "child", "dependent"] | None = None
            best_end = -1
            for pattern, relationship in RELATIONSHIP_LABELS:
                for match in re.finditer(rf"\b({pattern})\b", before):
                    if match.end() > best_end:
                        best, best_end = relationship, match.end()
            if best is not None and "[" not in before[best_end:]:
                return best
            for pattern, relationship in RELATIONSHIP_LABELS:
                if re.match(rf"^\s*[(\-–:,]\s*({pattern})\b", after):
                    return relationship
            position = normalized.find(token, position + len(token))
    return None


def assemble_plan(request: AssembleRequest) -> AssembleResponse:
    return Assembler(request).build()
