from __future__ import annotations

from typing import Any

import pytest

from app.models.api import AssembleChunk, AssembleRequest, ExtractedRows
from app.models.extraction import (
    ExBenefit,
    ExCostShare,
    ExHeader,
    ExHospitalCategory,
    ExPool,
    ExRule,
    ExtractionChunk,
    RowVerdict,
)
from app.models.plan import Period
from app.pipelines.assemble import assemble_plan, dollars_to_cents, map_period, split_notes
from app.pipelines.grounding import apply_correction, merge_extraction, quote_score, row_confidence

YEAR = Period(kind="benefit_year", startMonth=7, startDay=1)


def rows(model: type, **values: Any) -> Any:
    data: dict[str, Any] = {k: None for k in model.model_fields}
    data.update(values)
    return model.model_validate(data)


def grounded(chunk: ExtractionChunk, pages: dict[int, str]) -> ExtractedRows:
    verdicts = [
        RowVerdict(row_id=r.row_id, verdict="supported", correction=None, reason=None)
        for name in ("header", "benefits", "pools", "cost_shares", "rules", "hospital_categories")
        for r in getattr(chunk, name)
    ]
    merged, _ = merge_extraction(chunk, verdicts, pages)
    return merged


def extraction(**lists: list[Any]) -> ExtractionChunk:
    base: dict[str, list[Any]] = {
        k: [] for k in ("header", "benefits", "pools", "cost_shares", "rules", "hospital_categories")
    }
    base.update(lists)
    return ExtractionChunk.model_validate(base)


def test_quote_score_tolerates_tables_html_and_ocr_noise() -> None:
    markdown = "<table><tr><td>Massage therapy</td><td>80%</td><td>$500 per person</td></tr></table>"
    assert quote_score("Massage therapy | 80% | $500 per person", markdown) == 100.0
    assert quote_score("Massage therapv 80% $500 per persom", markdown) >= 85
    assert quote_score("Acupuncture 100% unlimited", markdown) < 60


def test_merge_relocates_wrong_page_and_scores_confidence() -> None:
    pages = {4: "Vision care: eye exam every 24 months.", 5: "Massage therapy: 80% to $500."}
    chunk = extraction(
        benefits=[
            rows(
                ExBenefit,
                row_id="b1",
                page=4,
                quote="Massage therapy: 80% to $500.",
                category_name="P",
                benefit_name="Massage",
            ),
            rows(ExBenefit, row_id="b1", page=9, quote="Chiropractic 50%", category_name="P", benefit_name="Chiro"),
            rows(ExBenefit, row_id="", page=4, quote="eye exam every 24 months", category_name="V", benefit_name="Eye"),
        ]
    )
    merged, issues = merge_extraction(
        chunk, [RowVerdict(row_id="b1", verdict="corrected", correction="{not json", reason=None)], pages
    )
    assert [(r.row_id, r.page, r.meta.grounded) for r in merged.benefits] == [
        ("b1", 5, True),
        ("benefit-p4-3", 4, True),
    ]
    assert merged.benefits[0].meta.confidence == 0.85
    assert merged.benefits[1].meta.verifierVerdict is None and merged.benefits[1].meta.confidence == 0.75
    assert {i.code for i in issues} == {"low_confidence", "unsupported_row"}


def test_confidence_and_corrections() -> None:
    assert row_confidence(100, "supported") == 1.0
    assert row_confidence(100, "unsupported") == 0.35
    assert row_confidence(70, "supported") == 0.5
    assert row_confidence(100, "supported", rounds_exhausted=True) == 0.9
    row = rows(ExBenefit, row_id="b", page=1, quote="q", category_name="c", benefit_name="n", coverage_percent=70)
    fixed, ok = apply_correction(row, '{"coverage_percent": 80, "row_id": "hijack", "unknown": 1}')
    assert ok and fixed.coverage_percent == 80 and fixed.row_id == "b"
    same, ok = apply_correction(row, '{"coverage_kind": "nonsense"}')
    assert not ok and same is row


@pytest.mark.parametrize(
    ("dollars", "cents"), [(1500, 150000), (80.5, 8050), (0.1 + 0.2, 30), (19.999, 2000), (None, None), (-1, None)]
)
def test_dollars_to_cents(dollars: float | None, cents: int | None) -> None:
    assert dollars_to_cents(dollars) == cents


@pytest.mark.parametrize(
    ("kind", "months", "expected", "guessed"),
    [
        ("benefit_year", None, YEAR, False),
        ("per benefit year", None, YEAR, False),
        ("calendar_year", None, Period(kind="benefit_year", startMonth=1, startDay=1), False),
        (
            "consecutive_benefit_years",
            24,
            Period(kind="consecutive_benefit_years", startMonth=7, startDay=1, years=2),
            False,
        ),
        ("rolling_months", 9, Period(kind="rolling_months", months=9), False),
        ("every 24 months", None, Period(kind="rolling_months", months=24), False),
        ("per_visit", None, Period(kind="per_visit"), False),
        ("per trip", None, Period(kind="per_visit"), False),
        ("per_admission", None, Period(kind="per_admission"), False),
        ("lifetime", None, Period(kind="lifetime"), False),
        ("policy anniversary", None, Period(kind="policy_anniversary"), False),
        (None, None, YEAR, True),
        ("fortnightly", None, YEAR, True),
    ],
)
def test_map_period(kind: str | None, months: int | None, expected: Period, guessed: bool) -> None:
    assert map_period(kind, months, YEAR) == (expected, guessed)


def test_split_notes_conventions() -> None:
    parts = split_notes(
        "Accommodation and theatre fees at agreement hospitals, after the excess. Waiting period: 12 months for "
        "pre-existing conditions. Schedule: 500 Initial consultation $55; 505 Subsequent consultation $45.50. "
        "Remedies are not covered."
    )
    assert [(i.itemCode, i.description, i.benefitCents) for i in parts.schedule] == [
        ("500", "Initial consultation", 5500),
        ("505", "Subsequent consultation", 4550),
    ]
    assert parts.waiting_note == "12 months for pre-existing conditions"
    assert parts.coverage_note == "Accommodation and theatre fees at agreement hospitals, after the excess"
    assert parts.notes == "Remedies are not covered."


CA_PAGES = {
    1: "Northwind Life & Health. Plan member: [MEMBER_A]. Spouse: IMEMBER_B]. Group policy [POLICY_1].",
    5: "Massage therapy: 80% up to $80.50 per visit, to $500 per person per benefit year. Chiropractor: 80%, "
    "to $400 per person per benefit year.",
    6: "Massage therapy and chiropractic share a combined maximum of $1,500 per person per benefit year. "
    "Physiotherapy: 80%.",
    8: "Annual deductible: $25 per family, applied to prescription drugs. Prescription drugs: 80%.",
    13: "Claims must be received within 90 days after the end of the benefit year. If your spouse has their own "
    "plan, claim under their plan first.",
}


def ca_chunks() -> list[AssembleChunk]:
    header = [
        rows(
            ExHeader,
            row_id="h1",
            page=1,
            quote="Northwind Life & Health",
            insurer="Northwind Life & Health",
            plan_name="Class A",
            benefit_period_kind="benefit_year",
            province="Ontario",
        ),
        rows(
            ExHeader, row_id="h2", page=1, quote="Plan member: [MEMBER_A]. Spouse: IMEMBER_B]. Group policy [POLICY_1]."
        ),
    ]
    massage = rows(
        ExBenefit,
        row_id="b1",
        page=5,
        quote="Massage therapy: 80% up to $80.50 per visit, to $500 per person",
        category_name="Paramedical practitioners",
        benefit_name="Massage therapy",
        coverage_percent=80,
        coverage_cap_amount=80.5,
        limit_unit="dollars",
        limit_value=500,
        limit_period_kind="benefit_year",
        pool_name="Paramedical combined maximum",
    )
    chiro = rows(
        ExBenefit,
        row_id="b2",
        page=5,
        quote="Chiropractor: 80%, to $400 per person per benefit year",
        category_name="Paramedical practitioners",
        benefit_name="Chiropractic",
        coverage_kind="percent",
        coverage_percent=80,
        limit_unit="dollars",
        limit_value=400,
        limit_period_kind="benefit_year",
    )
    physio = rows(
        ExBenefit,
        row_id="b3",
        page=6,
        quote="Physiotherapy: 80%.",
        category_name="Paramedical practitioners",
        benefit_name="Physiotherapy",
        coverage_kind="percent",
        coverage_percent=80,
        pool_name="Dental maximum",
    )
    pool = rows(
        ExPool,
        row_id="p1",
        page=6,
        quote="Massage therapy and chiropractic share a combined maximum of $1,500",
        pool_name="Paramedical combined maximum",
        limit_unit="dollars",
        limit_value=1500,
        period_kind="benefit_year",
        benefit_names="Massage, Chiropractor, Reflexology",
    )
    drugs = rows(
        ExBenefit,
        row_id="b4",
        page=8,
        quote="Prescription drugs: 80%.",
        category_name="Prescription drugs",
        benefit_name="Prescription drugs",
        coverage_kind="percent",
        coverage_percent=80,
    )
    deductible = rows(
        ExCostShare,
        row_id="c1",
        page=8,
        quote="Annual deductible: $25 per family, applied to prescription drugs",
        name="Drug deductible",
        kind="deductible",
        amount=25,
        scope="per_family",
        applies_to="Prescription drugs, Ice cream",
    )
    rules = [
        rows(
            ExRule,
            row_id="r1",
            page=13,
            quote="Claims must be received within 90 days after the end of the benefit year",
            rule_kind="submission_deadline",
            deadline_days=90,
        ),
        rows(
            ExRule,
            row_id="r2",
            page=13,
            quote="If your spouse has their own plan, claim under their plan first.",
            rule_kind="coordination_of_benefits",
            text="If your spouse has their own plan, claim under their plan first.",
        ),
    ]
    first = extraction(header=header, benefits=[massage, chiro], pools=[])
    # The second chunk overlaps on page 5 and repeats the massage row with a slightly different OCR quote.
    massage_again = massage.model_copy(
        update={"quote": "Massage therapy: 80% up to $80.50 per visit, to $500 per persom"}
    )
    second = extraction(benefits=[massage_again, physio, drugs], pools=[pool], cost_shares=[deductible], rules=rules)
    return [
        AssembleChunk(pages=[1, 2, 3, 4, 5], rows=grounded(first, CA_PAGES)),
        AssembleChunk(pages=[5, 6, 7, 8, 9], rows=grounded(second, CA_PAGES)),
        AssembleChunk(pages=[13], rows=ExtractedRows()),
    ]


def issue_codes(issues: list[Any]) -> list[tuple[str, int | None]]:
    return [(i.code, i.page) for i in issues]


def test_assemble_ca_plan() -> None:
    result = assemble_plan(AssembleRequest(region="CA", documentName="booklet.pdf", pageCount=13, chunks=ca_chunks()))
    plan, codes = result.plan, issue_codes(result.issues)

    assert plan.insurer == "Northwind Life & Health" and plan.currency == "CAD"
    assert plan.benefitPeriod == Period(kind="benefit_year", startMonth=1, startDay=1)
    assert plan.profile.kind == "CA" and plan.profile.province == "ON" and plan.profile.spousePlan is True

    (category,) = [c for c in plan.categories if c.name == "Paramedical practitioners"]
    assert category.id == "cat-paramedical-practitioners" and category.kind == "paramedical"
    massage = next(b for b in category.benefits if b.name == "Massage therapy")
    assert massage.id == "ben-massage-therapy"
    assert massage.coverage.kind == "percent_capped" and massage.coverage.capCents == 8050
    assert massage.limits[0].value == 50000 and massage.limits[0].unit == "cents"
    assert ("duplicate_removed", 5) in codes
    assert len(category.benefits) == 3

    (pool,) = plan.limitPools
    assert pool.limit.value == 150000
    assert pool.benefitIds == ["ben-massage-therapy", "ben-chiropractic"]
    assert massage.poolId == pool.id
    assert codes.count(("unlinked_pool", 6)) == 2  # "Reflexology" and physio's unknown "Dental maximum"

    (deductible,) = plan.costShares
    assert deductible.amountCents == 2500 and deductible.scope == "per_family"
    drugs = next(b for c in plan.categories for b in c.benefits if b.name == "Prescription drugs")
    assert deductible.appliesToCategoryIds == [drugs.categoryId] and drugs.costShareIds == [deductible.id]
    assert ("unlinked_cost_share", 8) in codes

    assert plan.claimRules.daysAfterPeriodEnd == 90 and plan.claimRules.submissionDays is None
    assert plan.claimRules.coordinationOfBenefits is True
    assert ("assumed_default", 13) in codes  # deadline basis inferred from "after the end of the benefit year"

    assert [(m.alias, m.relationship, m.id) for m in plan.members] == [
        ("[MEMBER_A]", "self", "m-a"),
        ("[MEMBER_B]", "spouse", "m-b"),
    ]
    assert plan.identifiers == ["[POLICY_1]"]
    assert ("page_excluded", None) in codes  # pages 10–12 were never analyzed


def test_assemble_au_profile_schedule_and_excess() -> None:
    pages = {
        3: "Wattle Health Fund. Silver Plus. Couple policy.",
        4: "Excess: $500 per person per calendar year, payable once per admission and no more than once each year.",
        5: "Joint replacements: Covered. Insulin pumps: Restricted.",
        8: "Physiotherapy: initial consultation (item 500) $55, subsequent consultation (item 505) $45.",
    }
    chunk = extraction(
        header=[
            rows(
                ExHeader,
                row_id="h",
                page=3,
                quote="Wattle Health Fund. Silver Plus.",
                insurer="Wattle Health Fund",
                plan_name="Silver Plus",
                hospital_tier="silver",
                hospital_plus=True,
                benefit_period_kind="benefit_year",
            )
        ],
        benefits=[
            rows(
                ExBenefit,
                row_id="b",
                page=8,
                quote="Physiotherapy: initial consultation (item 500) $55",
                category_name="Therapies",
                benefit_name="Physiotherapy",
                item_codes="500, 505",
                coverage_kind="schedule",
                waiting_period_months=2,
                notes="Schedule: 500 Initial consultation $55; 505 Subsequent consultation $45.",
            ),
            rows(
                ExBenefit,
                row_id="h1",
                page=4,
                quote="Excess: $500 per person per calendar year",
                category_name="Hospital",
                benefit_name="Private hospital admission",
                coverage_kind="percent",
                coverage_percent=100,
                waiting_period_months=2,
                notes="Accommodation and theatre fees at agreement hospitals, after the excess. "
                "Waiting period: 12 months for pre-existing conditions.",
            ),
        ],
        cost_shares=[
            rows(
                ExCostShare,
                row_id="c",
                page=4,
                quote="Excess: $500 per person per calendar year, payable once per admission and no more than "
                "once each year",
                name="Hospital excess",
                amount=500,
                period_kind="calendar_year",
                applies_to="Hospital",
            )
        ],
        hospital_categories=[
            rows(
                ExHospitalCategory,
                row_id="j",
                page=5,
                quote="Joint replacements: Covered",
                name="Joint replacements",
                status="covered",
            ),
            rows(
                ExHospitalCategory,
                row_id="i",
                page=5,
                quote="Insulin pumps: Restricted",
                name="Insulin pumps",
                status=None,
            ),
        ],
    )
    result = assemble_plan(
        AssembleRequest(
            region="AU", pageCount=8, chunks=[AssembleChunk(pages=list(range(1, 9)), rows=grounded(chunk, pages))]
        )
    )
    plan, codes = result.plan, issue_codes(result.issues)
    assert plan.currency == "AUD" and ("assumed_default", None) in codes
    assert plan.profile.kind == "AU"
    assert plan.profile.coverType == "combined"
    hospital = plan.profile.hospital
    assert hospital is not None and hospital.tier == "silver" and hospital.plus is True
    assert hospital.excess is not None and hospital.excess.model_dump() == {
        "amountCents": 50000,
        "per": "year",
        "maxPerYearCents": 50000,
    }
    assert [(c.name, c.status) for c in hospital.categories] == [
        ("Joint replacements", "covered"),
        ("Insulin pumps", "covered"),
    ]
    assert ("assumed_default", 5) in codes

    physio = next(b for c in plan.categories for b in c.benefits if b.name == "Physiotherapy")
    assert physio.coverage.kind == "schedule" and physio.coverage.scheduleNote == "Fixed benefit per item number"
    assert [(i.itemCode, i.benefitCents) for i in physio.coverage.scheduleItems] == [("500", 5500), ("505", 4500)]
    assert physio.itemCodes == ["500", "505"] and physio.notes is None
    assert next(c for c in plan.categories if c.name == "Therapies").kind == "extras"  # AU therapies are extras

    admission = next(b for c in plan.categories for b in c.benefits if b.name == "Private hospital admission")
    assert (
        admission.waitingPeriod is not None and admission.waitingPeriod.note == "12 months for pre-existing conditions"
    )
    assert admission.coverage.scheduleNote == "Accommodation and theatre fees at agreement hospitals, after the excess"
    assert admission.costShareIds == ["cs-hospital-excess"]
    excess = plan.costShares[0]
    assert excess.kind == "excess" and excess.period == Period(kind="benefit_year", startMonth=1, startDay=1)
