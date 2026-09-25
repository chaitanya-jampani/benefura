from __future__ import annotations

import copy

import pytest

from evals.harness import engine
from evals.harness.browser_tools import BrowserToolExecutor, ToolError, load_plan
from evals.harness.datasets import load_scenarios, path_matches, path_values

SCENARIOS = load_scenarios()
CA = load_plan("samples/fixtures/ca-northwind.plan.json")
AU = load_plan("samples/fixtures/au-wattle.plan.json")
TODAY = "2026-06-15"


def est(plan, claims, **inp):
    return engine.estimate_reimbursement(plan, claims, {"memberId": "m-a", "serviceDate": TODAY, **inp}, today=TODAY)


def claim(cid, plan, status, lines, paid=None, member="m-a", created="2026-01-01T00:00:00Z"):
    return {
        "id": cid,
        "planId": plan["id"],
        "status": status,
        "patientMemberId": member,
        "provider": None,
        "lines": [
            {
                "id": f"{cid}-{i}",
                "serviceDate": d,
                "benefitId": b,
                "itemCode": code,
                "description": None,
                "quantity": q,
                "chargedCents": c,
                "otherPlanPaidCents": 0,
            }
            for i, (b, d, c, q, code) in enumerate(lines)
        ],
        "outcome": None if paid is None else {"paidCents": paid, "decidedOn": None, "note": None},
        "attachments": [],
        "history": [],
        "deadline": None,
        "createdAt": created,
        "updatedAt": created,
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_executor_reproduces_expected_numbers(scenario):
    for expected in scenario.expectedToolOutputs:
        executor = BrowserToolExecutor.for_scenario(scenario.model_dump())
        output = executor.execute(expected.tool, expected.args)
        for path, value in expected.expect.items():
            assert path_matches(output, path, value), (scenario.id, expected.tool, path, path_values(output, path))


def test_money_matches_the_typescript_helpers():
    assert engine.percent_of(12345, 50) == 6173
    assert engine.percent_of(63, 80) == 50
    assert engine.percent_of(25, 50) == 13
    assert engine.percent_of(10001, 62.5) == 6251
    assert engine.round_half_up(5, 2) == 3
    assert engine.allocate(1000, [1, 1, 1]) == [334, 333, 333]
    assert engine.allocate(500, [0, 0]) == [500, 0]
    assert engine.allocate(10001, [8000, 8000]) == [5001, 5000]


def test_deductible_comes_off_the_charge_then_coverage_and_caps_apply():
    drugs = est(CA, [], benefitId="ben-drugs", chargedCents=4250)
    assert drugs["planPaysCents"] == 1400  # 80% x ($42.50 - $25)
    assert drugs["limitedBy"] == "deductible"
    plan = copy.deepcopy(CA)
    plan["costShares"].append(
        {
            "id": "cs-massage",
            "name": "Massage deductible",
            "kind": "deductible",
            "amountCents": 2500,
            "percent": None,
            "period": {"kind": "benefit_year", "startMonth": 1, "startDay": 1},
            "scope": "per_person",
            "appliesToCategoryIds": [],
            "appliesToBenefitIds": ["ben-massage"],
        }
    )
    massage = est(plan, [], benefitId="ben-massage", chargedCents=15000)
    assert massage["planPaysCents"] == 8000  # 80% x ($150 - $25) = $100, still capped at $80
    assert massage["limitedBy"] == "per_service_cap"


def test_family_deductible_is_met_across_members():
    spouse = claim("c1", CA, "paid", [("ben-drugs", "2026-02-01", 1500, 1, None)], paid=0, member="m-b")
    assert est(CA, [spouse], benefitId="ben-drugs", chargedCents=6000)["planPaysCents"] == 4000


def test_claims_replay_in_service_date_order():
    # Created physio-first, but by service date chiro and massage take 100000 of the 150000 pool first.
    claims = [
        claim("c-physio", CA, "submitted", [("ben-physio", "2026-03-01", 100000, 1, None)], created="2026-01-01"),
        claim("c-naturo", CA, "submitted", [("ben-naturopath", "2026-04-01", 100000, 1, None)], created="2026-01-02"),
        claim("c-massage", CA, "submitted", [("ben-massage", "2026-02-01", 200000, 10, None)], created="2026-05-01"),
        claim("c-chiro", CA, "submitted", [("ben-chiro", "2026-01-15", 200000, 10, None)], created="2026-05-02"),
    ]
    used = {
        b: engine.benefit_usage(CA, claims, b, "m-a", today=TODAY, include_submitted=True)["usedCents"]
        for b in ("ben-chiro", "ben-massage", "ben-physio", "ben-naturopath")
    }
    assert used == {"ben-chiro": 50000, "ben-massage": 50000, "ben-physio": 50000, "ben-naturopath": 0}


def test_paid_claims_split_by_estimate_with_largest_remainder():
    two_lines = claim(
        "c2",
        CA,
        "paid",
        [("ben-massage", "2026-02-01", 12000, 1, None), ("ben-physio", "2026-02-01", 10000, 1, None)],
        paid=10001,
    )
    massage = engine.benefit_usage(CA, [two_lines], "ben-massage", "m-a", today=TODAY)
    physio = engine.benefit_usage(CA, [two_lines], "ben-physio", "m-a", today=TODAY)
    assert (massage["usedCents"], physio["usedCents"]) == (5001, 5000)


def test_submitted_claims_count_only_when_toggled_and_drafts_never():
    submitted = claim("c3", CA, "submitted", [("ben-massage", "2026-02-01", 12000, 1, None)])
    draft = claim("c4", CA, "draft", [("ben-massage", "2026-02-02", 12000, 1, None)])
    off = engine.benefit_usage(CA, [submitted, draft], "ben-massage", "m-a", today=TODAY)
    on = engine.benefit_usage(CA, [submitted, draft], "ben-massage", "m-a", today=TODAY, include_submitted=True)
    assert (off["usedCents"], on["usedCents"]) == (0, 8000)


def test_counts_and_frequencies_only_include_lines_the_plan_paid():
    unpaid_exam = claim("c5", CA, "paid", [("ben-eye-exam", "2026-01-10", 9000, 1, None)], paid=0)
    assert est(CA, [unpaid_exam], benefitId="ben-eye-exam", chargedCents=9000)["planPaysCents"] == 9000
    unpaid_stockings = claim("c6", CA, "paid", [("ben-compression", "2026-01-10", 5000, 2, None)], paid=0)
    paid_stockings = claim("c7", CA, "paid", [("ben-compression", "2026-02-10", 5000, 2, None)], paid=4000)
    assert est(CA, [unpaid_stockings], benefitId="ben-compression", chargedCents=5000)["planPaysCents"] == 4000
    blocked = est(CA, [paid_stockings], benefitId="ben-compression", chargedCents=5000)
    assert blocked["planPaysCents"] == 0 and blocked["limitedBy"] == "limit"


def test_windows_waiting_periods_and_deadlines():
    assert engine.add_months("2025-01-31", 1) == "2025-02-28"
    assert engine.add_months("2024-01-31", 1) == "2024-02-29"
    assert engine.period_window({"kind": "rolling_months", "months": 24}, TODAY, CA) == ("2024-06-16", TODAY)
    assert engine.period_window({"kind": "benefit_year"}, TODAY, CA) == ("2026-01-01", "2026-12-31")
    assert engine.period_window(
        {"kind": "policy_anniversary"}, "2026-03-01", {**CA, "effectiveDate": "2025-07-01"}
    ) == ("2025-07-01", "2026-06-30")
    eyewear = {"kind": "consecutive_benefit_years", "years": 2}
    assert engine.period_window(eyewear, TODAY, CA) == ("2025-01-01", "2026-12-31")
    assert engine.period_window(eyewear, "2027-02-01", CA) == ("2027-01-01", "2028-12-31")
    assert engine.period_window(eyewear, "2026-02-01", {**CA, "effectiveDate": "2025-07-01"}) == (
        "2025-01-01",
        "2026-12-31",
    )
    assert engine.period_window({"kind": "per_visit"}, TODAY, CA) is None

    crown = {"benefitId": "ben-dental-major", "chargedCents": 10000}
    assert est(CA, [], serviceDate="2025-12-31", **crown)["planPaysCents"] == 0
    assert est(CA, [], serviceDate="2026-01-01", **crown)["planPaysCents"] == 5000  # first covered day

    both = {**CA, "claimRules": {**CA["claimRules"], "submissionDays": 30, "daysAfterPeriodEnd": 90}}
    assert engine.claim_deadline(both, "2026-12-20") == "2027-03-31"
    both["claimRules"]["submissionDays"] = 200
    assert engine.claim_deadline(both, "2026-12-20") == "2027-07-08"


def test_no_cover_before_the_effective_date_and_unknown_items():
    before = est(CA, [], benefitId="ben-physio", serviceDate="2024-12-31", chargedCents=10000)
    assert before["planPaysCents"] == 0 and before["limitedBy"] == "not_covered"
    unknown = est(AU, [], benefitId="ben-general-dental", chargedCents=9500, itemCode="999")
    assert unknown["planPaysCents"] == 0 and unknown["limitedBy"] == "schedule"
    item = est(AU, [], benefitId="ben-physio", chargedCents=3000, itemCode="item 500")
    assert item["planPaysCents"] == 3000 and item["limitedBy"] is None  # schedule 5500 capped at the charge


def test_executor_tool_errors_and_shapes():
    scenario = next(s for s in SCENARIOS if s.id.startswith("ca-07")).model_dump()
    executor = BrowserToolExecutor.for_scenario(scenario)
    assert executor.execute("find_benefits", {"query": "RMT"})["benefits"][0]["benefitId"] == "ben-massage"
    assert executor.execute("get_plan_overview", {})["members"][0] == {"alias": "[MEMBER_A]", "relationship": "self"}
    with pytest.raises(ToolError, match="Unknown member alias"):
        executor.execute(
            "estimate_reimbursement",
            {"benefit_id": "ben-physio", "member_alias": "[MEMBER_Z]", "service_date": TODAY, "charged_cents": 100},
        )
    with pytest.raises(ToolError, match="whole number of cents"):
        executor.execute(
            "estimate_reimbursement",
            {"benefit_id": "ben-physio", "member_alias": "[MEMBER_A]", "service_date": TODAY, "charged_cents": 1.5},
        )
    with pytest.raises(ToolError, match="not a browser tool"):
        executor.execute("ask_knowledge_agent", {})
    with pytest.raises(ToolError, match="can't move"):
        executor.execute(
            "update_claim",
            {"claim_id": "c-ca-1", "status": "draft", "paid_cents": None, "provider": None, "note": None},
        )
    with pytest.raises(ToolError, match="Nothing to update"):
        executor.execute(
            "update_claim", {"claim_id": "c-ca-5", "status": None, "paid_cents": None, "provider": None, "note": None}
        )
    drafted = executor.execute(
        "draft_claim",
        {
            "member_alias": "[MEMBER_C]",
            "provider": " ",
            "lines": [
                {
                    "benefit_id": "ben-physio",
                    "service_date": "2026-06-12",
                    "charged_cents": 8500,
                    "quantity": None,
                    "item_code": None,
                    "description": None,
                }
            ],
        },
    )
    assert drafted == {
        "claimId": "clm-eval0000000001",
        "status": "draft",
        "memberAlias": "[MEMBER_C]",
        "provider": None,
        "lines": 1,
        "totalChargedCents": 8500,
        "deadline": "2027-03-31",
    }
    listed = executor.execute("list_claims", {"status": "draft", "benefit_id": None})
    assert listed["total"] == 1 and listed["claims"][0]["claimId"] == "clm-eval0000000001"
