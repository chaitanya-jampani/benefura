from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from evals.custom.citations import evaluate_row, longest_shared_run_exceeds, score_citations
from evals.custom.extraction_accuracy import score_extraction
from evals.custom.numeric_exactness import (
    arguments_match,
    dates_in,
    evaluate_scenario,
    money_cents,
    score_scenarios,
)
from evals.custom.pii_leak import load_needles, scan_jsonl_files, scan_text
from evals.harness.browser_tools import load_plan
from evals.harness.datasets import Fact, load_scenarios, load_sources

SCENARIOS = {s.id: s for s in load_scenarios()}


def test_money_and_date_parsing():
    text = "The plan pays $80.00, you pay $40 and CA$1,292.50 is left; A$5 or 12 dollars. Next: March 10, 2027."
    assert money_cents(text) >= {8000, 4000, 129250, 500, 1200}
    assert money_cents("Costs $1,500.") == {150000}
    assert money_cents("$1\u2009802.00 left") == {180200}  # the app's thin-space grouping
    assert dates_in("on 2026-10-20, 10 March 2027 and Oct. 20, 2026") == {date(2026, 10, 20), date(2027, 3, 10)}


def row_for(scenario_id: str, text: str, calls: list[dict], errors: list[str] | None = None) -> dict:
    return {"response": text, "tool_calls": calls, "meta": {"id": scenario_id, "errors": errors or []}}


def call(name: str, arguments: dict) -> dict:
    return {"type": "tool_call", "tool_call_id": f"c-{name}", "name": name, "arguments": arguments}


MASSAGE = {
    "benefit_id": "ben-massage",
    "member_alias": "[MEMBER_A]",
    "service_date": "2026-06-15",
    "charged_cents": 12000,
    "quantity": None,
    "item_code": None,
}


def test_exact_scenario_passes():
    s = SCENARIOS["ca-01-massage-estimate-per-visit-cap"]
    row = row_for(
        s.id,
        "Your plan pays $80.00 of the $120 visit, so you pay $40.00.",
        [call("find_benefits", {"query": "massage"}), call("estimate_reimbursement", MASSAGE)],
    )
    score = evaluate_scenario(s, row)
    assert score.numeric_exact and score.tool_calls_ok


def test_wrong_number_in_answer_or_arguments_fails():
    s = SCENARIOS["ca-01-massage-estimate-per-visit-cap"]
    wrong_text = evaluate_scenario(
        s, row_for(s.id, "The plan pays $96.00; you pay $24.00.", [call("estimate_reimbursement", MASSAGE)])
    )
    assert not wrong_text.numeric_exact and wrong_text.tool_calls_ok
    wrong_args = evaluate_scenario(
        s,
        row_for(
            s.id,
            "The plan pays $80.00; you pay $40.00.",
            [call("estimate_reimbursement", {**MASSAGE, "charged_cents": 1200})],
        ),
    )
    assert not wrong_args.numeric_exact and wrong_args.arg_mismatches == ["estimate_reimbursement"]
    camel = {"benefitId": "ben-massage", "memberId": "m-a", "serviceDate": "2026-06-15", "chargedCents": 12000}
    camel_score = evaluate_scenario(
        s, row_for(s.id, "The plan pays $80.00; you pay $40.00.", [call("estimate_reimbursement", camel)])
    )
    assert camel_score.arg_mismatches == ["estimate_reimbursement"]  # only the registry's argument names count


def test_forbidden_tools_and_retried_declines_fail_tool_matching():
    s = SCENARIOS["ca-08-update-claim-declined-no-retry"]
    update = call(
        "update_claim", {"claim_id": "c-ca-5", "status": "paid", "paid_cents": 8000, "provider": None, "note": None}
    )
    once = evaluate_scenario(s, row_for(s.id, "Okay, I left it unchanged.", [update]))
    assert once.tool_calls_ok and once.numeric_exact
    twice = evaluate_scenario(s, row_for(s.id, "Trying again.", [update, update]))
    assert not twice.tool_calls_ok and twice.over_limit == ["update_claim"]
    s1 = SCENARIOS["ca-01-massage-estimate-per-visit-cap"]
    forbidden = evaluate_scenario(
        s1, row_for(s1.id, "$80.00 $40.00", [call("estimate_reimbursement", MASSAGE), call("draft_claim", {})])
    )
    assert forbidden.forbidden_used == ["draft_claim"]


def test_any_of_calls_dates_and_text_facts():
    s = SCENARIOS["au-15-hospital-pregnancy-and-excess"]
    ok = evaluate_scenario(
        s,
        row_for(
            s.id,
            "Pregnancy and birth is not covered. Your excess is $500 per year.",
            [call("search_plan_document", {"query": "pregnancy"})],
        ),
    )
    assert ok.numeric_exact and ok.tool_calls_ok
    s4 = SCENARIOS["ca-04-eye-exam-frequency"]
    args = {**MASSAGE, "benefit_id": "ben-eye-exam", "charged_cents": 11000}
    assert evaluate_scenario(
        s4, row_for(s4.id, "Next eligible on March 10, 2027.", [call("estimate_reimbursement", args)])
    ).numeric_exact
    assert not evaluate_scenario(
        s4, row_for(s4.id, "Next eligible on 2027-03-11.", [call("estimate_reimbursement", args)])
    ).numeric_exact


def test_errors_or_missing_rows_are_not_exact():
    s = SCENARIOS["ca-06-dental-maximum-and-next-cleaning"]
    usage = call("get_usage", {"benefit_id": None, "member_alias": "[MEMBER_A]"})
    assert not evaluate_scenario(s, None).numeric_exact
    errored = row_for(s.id, "$1,802.00 on 2026-10-20", [usage], errors=["http_429:budget_exhausted"])
    assert not evaluate_scenario(s, errored).numeric_exact
    summary = score_scenarios(list(SCENARIOS.values()), [row_for(s.id, "$1\u2009802.00 on 2026-10-20", [usage])])
    assert summary["scenarios"] == 1 and summary["numeric_exactness"] == 1.0


def test_arguments_match_nested_lists_and_member_aliases():
    plan = load_plan("samples/fixtures/ca-northwind.plan.json")
    expected = {"member_alias": "[MEMBER_C]", "lines": [{"benefit_id": "ben-physio", "charged_cents": 8500}]}
    actual = {
        "member_alias": "MEMBER_C",
        "provider": "Lakeside Physio",
        "lines": [{"benefit_id": "ben-physio", "charged_cents": 8500, "service_date": "2026-06-12"}],
    }
    assert arguments_match(expected, actual, plan)
    assert not arguments_match(expected, {**actual, "lines": []}, plan)
    assert not arguments_match({"charged_cents": 8500}, {"charged_cents": True}, plan)


def test_fact_model_validation():
    assert Fact(kind="money", cents=100).cents == 100


def knowledge_row(citations: list[dict], response: str = "Answer.", context: str = "Some context.") -> dict:
    return {
        "query": "q",
        "response": response,
        "context": context,
        "meta": {"id": "kq", "citations": citations, "expectedSources": ["phio-waiting-periods"]},
    }


PHIO = {
    "sourceId": "phio-waiting-periods-0123456789abcdef0123",
    "url": "https://www.privatehealth.gov.au/health_insurance/howitworks/waiting_periods.htm",
    "title": "Waiting periods",
    "license": "CC BY 3.0 AU",
    "attribution": "Licensed from the Commonwealth...",
}


def test_citation_present_and_licence_allowed():
    sources = load_sources()
    result = evaluate_row(knowledge_row([PHIO]), sources)
    assert result.passed and result.expected_source_hit
    assert not evaluate_row(knowledge_row([]), sources).passed


def test_link_only_unknown_and_licence_mismatch_fail():
    sources = load_sources()
    link_only = {
        "url": "https://www2.gov.bc.ca/gov/content/health/health-drug-coverage/msp",
        "license": "x",
        "attribution": "BC",
    }
    r = evaluate_row(knowledge_row([link_only]), sources)
    assert not r.passed and r.link_only_cited == ["bc-medical-services-plan"]
    assert not evaluate_row(knowledge_row([{"url": "https://example.com/x", "attribution": "a"}]), sources).passed
    wrong = evaluate_row(knowledge_row([{**PHIO, "license": "CC BY 4.0"}]), sources)
    assert wrong.licence_mismatch == ["phio-waiting-periods"]
    no_attr = evaluate_row(knowledge_row([{**PHIO, "attribution": ""}]), sources)
    assert not no_attr.passed


def test_excerpt_sources_limit_reproduced_runs():
    sources = load_sources()
    passage = " ".join(f"word{i}" for i in range(80))
    tbs = {
        "url": sources["tbs-pshcp-summary"]["url"],
        "license": sources["tbs-pshcp-summary"]["licence"],
        "attribution": "TBS",
    }
    assert longest_shared_run_exceeds(passage, passage, 50)
    long_copy = evaluate_row(knowledge_row([tbs], response=passage, context=passage), sources)
    assert long_copy.excerpt_overrun == ["tbs-pshcp-summary"] and not long_copy.passed
    short = " ".join(passage.split()[:30])
    assert evaluate_row(knowledge_row([tbs], response=short, context=passage), sources).passed


def test_verbatim_quotes_must_match_retrieved_text():
    sources = load_sources()
    ohip = {
        "url": "https://www.ontario.ca/page/what-ohip-covers",
        "license": sources["ontario-what-ohip-covers"]["licence"],
        "attribution": "© King's Printer for Ontario",
    }
    context = "OHIP covers eye examinations for people aged 19 and under and 65 and over."
    exact = '"OHIP covers eye examinations for people aged 19 and under" (What OHIP covers)'
    altered = '"OHIP pays for eye tests for anyone aged 19 or younger" (What OHIP covers)'
    assert evaluate_row(knowledge_row([ohip], response=exact, context=context), sources).passed
    bad = evaluate_row(knowledge_row([ohip], response=altered, context=context), sources)
    assert bad.altered_verbatim_quotes == 1 and not bad.passed


def test_citations_resolve_by_chunk_id_when_the_url_differs():
    sources = load_sources()
    moved = {**PHIO, "url": "https://www.privatehealth.gov.au/moved.htm"}
    assert evaluate_row(knowledge_row([moved]), sources).passed
    fake_mode = {
        "sourceId": "ca-cra-medical-expenses",
        "url": "https://example.org/x",
        "license": "x",
        "attribution": "a",
    }
    assert evaluate_row(knowledge_row([fake_mode]), sources).unknown_sources


def test_score_citations_rates():
    sources = load_sources()
    summary = score_citations([knowledge_row([PHIO]), knowledge_row([])], sources)
    assert summary["citation_licence_pass_rate"] == 0.5


PII_FILE = {
    "doc": "ca-test",
    "entries": [
        {"value": "Rowan Lindqvist", "kind": "person_name", "page": 1, "aliasToken": "[MEMBER_A]"},
        {"value": "046 832 150", "kind": "sin", "page": 2, "aliasToken": None},
        {"value": "rowan.lindqvist@example.net", "kind": "email", "page": 2, "aliasToken": "[EMAIL_A]"},
        {"value": "1986-04-19", "kind": "date_of_birth", "page": 1, "aliasToken": None},
        {"value": "ON", "kind": "province", "page": 1, "aliasToken": None},
    ],
}


@pytest.fixture
def needles(tmp_path):
    path = tmp_path / "ca-test.pii.json"
    path.write_text(json.dumps(PII_FILE))
    return load_needles([path])


def test_pii_needles_skip_aliases_and_short_values(needles):
    kinds = sorted({n.kind for n in needles})
    assert kinds == ["date_of_birth", "email", "person_name", "person_name:surname", "sin"]


def test_pii_scan_finds_values_in_any_format(needles):
    assert scan_text('{"text": "[MEMBER_A] had physio on 2026-06-15 for $80"}', needles) == {}
    assert scan_text("patient ROWAN   LINDQVIST", needles)["person_name"] == 1
    assert scan_text("Dr. Lindqvist", needles)["person_name:surname"] == 1
    assert scan_text("SIN 046-832-150", needles)["sin"] == 1
    assert scan_text("sin=046832150", needles)["sin"] == 1
    assert scan_text("amounts 150000 and 20260615", needles) == {}
    assert scan_text("born 1986-04-19", needles)["date_of_birth"] == 1


def test_real_sample_pii_files_load_and_never_match_alias_tokens():
    real = load_needles()
    assert len(real) >= 20
    assert {"person_name", "sin", "medicare_number", "email"} <= {n.kind for n in real}
    assert scan_text('{"text": "[MEMBER_A] and [MEMBER_B] had physio; claim [POLICY_1] for $80"}', real) == {}
    assert scan_text("Rowan Lindqvist", real)


def test_pii_scan_files_reports_locations_without_values(tmp_path, needles):
    clean = tmp_path / "plan_claims.jsonl"
    clean.write_text(json.dumps({"meta": {"id": "ok"}, "response": "[MEMBER_A] pays $40"}) + "\n")
    leaky = tmp_path / "payloads.jsonl"
    leaky.write_text(json.dumps({"id": "ca-01", "payload": {"text": "Rowan Lindqvist"}}) + "\n")
    report = scan_jsonl_files([clean, leaky], needles)
    assert report["pii_leak_hits"] == 2
    assert report["locations"] == [
        {"file": "payloads.jsonl", "line": 1, "item": "ca-01", "kinds": ["person_name", "person_name:surname"]}
    ]
    assert "Rowan" not in json.dumps(report)


GOLDEN = {
    "header": [
        {
            "row_id": "h1",
            "page": 3,
            "quote": "q",
            "insurer": "Northwind Life & Health",
            "plan_name": "Class A",
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
            "row_id": "h2",
            "page": 12,
            "quote": "q",
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
    "benefits": [
        {
            "row_id": "ben-massage",
            "page": 5,
            "quote": "q",
            "category_name": "Paramedical practitioners",
            "category_kind": "paramedical",
            "benefit_name": "Massage therapy",
            "keywords": "RMT",
            "item_codes": None,
            "coverage_kind": "percent_capped",
            "coverage_percent": 80.0,
            "coverage_cap_amount": 80.0,
            "coverage_amount": None,
            "limit_unit": "dollars",
            "limit_value": 500.0,
            "limit_period_kind": "benefit_year",
            "limit_period_months": None,
            "limit_scope": "per_person",
            "frequency_count": None,
            "frequency_period_kind": None,
            "frequency_period_months": None,
            "waiting_period_months": None,
            "requirements": None,
            "pool_name": "Paramedical combined maximum",
            "notes": None,
        },
        {
            "row_id": "ben-physio",
            "page": 5,
            "quote": "q",
            "category_name": "Paramedical practitioners",
            "category_kind": "paramedical",
            "benefit_name": "Physiotherapy",
            "keywords": None,
            "item_codes": "500, 505",
            "coverage_kind": "percent",
            "coverage_percent": 80.0,
            "coverage_cap_amount": None,
            "coverage_amount": None,
            "limit_unit": "dollars",
            "limit_value": 750.0,
            "limit_period_kind": "benefit_year",
            "limit_period_months": None,
            "limit_scope": "per_person",
            "frequency_count": None,
            "frequency_period_kind": None,
            "frequency_period_months": None,
            "waiting_period_months": None,
            "requirements": None,
            "pool_name": "Paramedical combined maximum",
            "notes": None,
        },
    ],
    "pools": [],
    "cost_shares": [],
    "rules": [
        {
            "row_id": "r1",
            "page": 13,
            "quote": "Claims within 90 days",
            "rule_kind": "submission_deadline",
            "deadline_days": 90,
            "deadline_basis": "period_end",
            "text": "Claims within 90 days",
        }
    ],
    "hospital_categories": [],
}


def as_chunk_response(rows: dict) -> dict:
    wrapped = {
        k: [
            {
                **r,
                "meta": {
                    "rowId": r["row_id"],
                    "kind": "benefit",
                    "page": r["page"],
                    "confidence": 0.9,
                    "grounded": True,
                },
            }
            for r in v
        ]
        for k, v in rows.items()
    }
    return {"pages": [1, 2, 3, 4, 5], "rows": wrapped, "issues": [], "usage": {}, "traceId": "0" * 32}


def test_perfect_extraction_scores_one():
    assert score_extraction(GOLDEN, GOLDEN)["field_accuracy"] == 1.0
    split = [
        as_chunk_response({**GOLDEN, "benefits": GOLDEN["benefits"][:1]}),
        as_chunk_response({**GOLDEN, "benefits": GOLDEN["benefits"]}),
    ]  # overlap duplicates collapse
    assert score_extraction(GOLDEN, split)["field_accuracy"] == 1.0


def test_field_errors_missing_rows_and_extra_rows_reduce_accuracy():
    predicted = copy.deepcopy(GOLDEN)
    predicted["benefits"][0]["coverage_cap_amount"] = 60.0
    predicted["benefits"][0]["benefit_name"] = "Massage Therapy (RMT)"  # fuzzy key still aligns
    result = score_extraction(GOLDEN, predicted)
    assert 0.9 < result["field_accuracy"] < 1.0
    assert (
        "benefits:Paramedical practitioners Massage therapy:coverage_cap_amount"
        in result["collections"]["benefits"]["wrong_fields"]
    )

    missing = copy.deepcopy(GOLDEN)
    missing["benefits"] = missing["benefits"][:1]
    assert score_extraction(GOLDEN, missing)["collections"]["benefits"]["unmatched_golden"] == 1

    extra = copy.deepcopy(GOLDEN)
    extra["hospital_categories"] = [{"row_id": "x", "page": 1, "quote": "q", "name": "Cataracts", "status": "covered"}]
    extra_score = score_extraction(GOLDEN, extra)
    assert extra_score["collections"]["hospital_categories"]["unmatched_predicted"] == 1
    assert extra_score["field_accuracy"] < 1.0


def test_numbers_lists_and_header_merge():
    predicted = copy.deepcopy(GOLDEN)
    predicted["benefits"][1]["item_codes"] = "505,500"
    predicted["benefits"][1]["coverage_percent"] = 80
    predicted["header"] = [{**GOLDEN["header"][0], "hsa_annual_credit": 500, "hsa_carry_forward_years": 1}]
    assert score_extraction(GOLDEN, predicted)["field_accuracy"] == 1.0
