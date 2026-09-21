from __future__ import annotations

import re
from typing import Any

import pytest
from app.models.extraction import ExtractionChunk
from app.models.plan import Plan

from conftest import BOOKLETS, golden_pages, load, norm, page_texts
from samplegen import au_policy, ca_booklet
from samplegen.doc import build_pdf, pages_json
from samplegen.extraction import chunk
from samplegen.fixtures import plan, quotes

SPECS = {"ca-northwind": ca_booklet.spec, "au-wattle": au_policy.spec}


@pytest.mark.parametrize("doc", BOOKLETS)
def test_fixture_plan_validates(doc: str) -> None:
    Plan.model_validate(plan(doc))


@pytest.mark.parametrize("doc", BOOKLETS)
def test_pages_json_shape(doc: str) -> None:
    pages = load(f"golden/{doc}.pages.json")
    assert [p["page"] for p in pages] == list(range(1, plan(doc)["document"]["pageCount"] + 1))
    for p in pages:
        assert set(p) == {"page", "markdown"}
        assert p["markdown"].strip()


@pytest.mark.parametrize("doc", BOOKLETS)
def test_golden_pages_contain_fixture_quotes(doc: str) -> None:
    md = golden_pages(doc)
    missing = [(k, s["page"]) for k, s in quotes(doc).items() if norm(s["quote"]) not in md[s["page"]]]
    assert not missing


@pytest.mark.parametrize("doc", BOOKLETS)
def test_golden_pages_have_no_raw_pii_and_carry_alias_tokens(doc: str) -> None:
    md = golden_pages(doc)
    entries = load(f"fixtures/{doc}.pii.json")
    for page, text in md.items():
        for value in {e["value"] for e in entries}:
            assert value not in text, (page, value)
    for e in entries:
        if e["aliasToken"]:
            assert e["aliasToken"] in md[e["page"]], e


@pytest.mark.parametrize("doc", BOOKLETS)
def test_extraction_validates(doc: str) -> None:
    ExtractionChunk.model_validate(load(f"golden/{doc}.extraction.json"))


def _rows(doc: str) -> list[tuple[str, dict[str, Any]]]:
    ex = load(f"golden/{doc}.extraction.json")
    return [(section, row) for section, rows in ex.items() for row in rows]


@pytest.mark.parametrize("doc", BOOKLETS)
def test_extraction_quotes_are_on_their_page(doc: str) -> None:
    md = golden_pages(doc)
    text = page_texts(BOOKLETS[doc])
    rows = _rows(doc)
    ids = [row["row_id"] for _, row in rows]
    assert len(ids) == len(set(ids))
    raw_by_token = {v.token: v.value for v in SPECS[doc]().registry.values() if v.token}
    for section, row in rows:
        assert norm(row["quote"]) in md[row["page"]], (section, row["row_id"])
        # The PDF text layer has the raw values where the redacted markdown has alias tokens and table pipes.
        unredacted = row["quote"].replace(" | ", " ")
        for token, value in raw_by_token.items():
            unredacted = unredacted.replace(token, value)
        assert norm(unredacted) in text[row["page"]], (section, row["row_id"])


@pytest.mark.parametrize("doc", BOOKLETS)
def test_alias_header_rows_give_members_and_identifiers(doc: str) -> None:
    from app.pipelines.assemble import IDENTIFIER_STEMS, relationship_hint

    header = load(f"golden/{doc}.extraction.json")["header"]
    alias = [r for r in header if r["row_id"].startswith("hdr-alias-")]
    assert alias and all(v is None for r in alias for k, v in r.items() if k not in ("row_id", "page", "quote"))
    quotes_ = [r["quote"] for r in alias]
    tokens = {t for q in quotes_ for t in re.findall(r"\[[A-Z]+_[A-Z0-9]+\]", q)}
    p = plan(doc)
    assert tokens == {m["alias"] for m in p["members"]} | set(p["identifiers"])
    assert {t for t in tokens if t[1:-1].rsplit("_", 1)[0] in IDENTIFIER_STEMS} == set(p["identifiers"])
    for m in p["members"]:
        stem = m["alias"][1:-1].rsplit("_", 1)[0]
        assert relationship_hint(m["alias"], stem, quotes_) == m["relationship"], m


@pytest.mark.parametrize("doc", BOOKLETS)
def test_golden_files_are_up_to_date(doc: str) -> None:
    spec = SPECS[doc]()
    _, pii = build_pdf(spec)
    assert pages_json(spec) == load(f"golden/{doc}.pages.json")
    assert ExtractionChunk.model_validate(chunk(doc)).model_dump(mode="json") == load(f"golden/{doc}.extraction.json")
    assert pii == load(f"fixtures/{doc}.pii.json")


def _cents(dollars: float | None) -> int | None:
    return None if dollars is None else round(dollars * 100)


@pytest.mark.parametrize("doc", BOOKLETS)
def test_extraction_benefits_match_fixture(doc: str) -> None:
    ex = load(f"golden/{doc}.extraction.json")
    p = plan(doc)
    rows = {r["benefit_name"]: r for r in ex["benefits"]}
    pools = {pool["id"]: pool for pool in p["limitPools"]}
    benefits = [(cat, b) for cat in p["categories"] for b in cat["benefits"]]
    assert len(rows) == len(ex["benefits"]) == len(benefits)
    for cat, b in benefits:
        r = rows[b["name"]]
        cov = b["coverage"]
        assert (r["page"], r["quote"]) == (b["source"]["page"], b["source"]["quote"])
        assert (r["category_name"], r["category_kind"]) == (cat["name"], cat["kind"])
        assert r["coverage_kind"] == cov["kind"]
        assert r["coverage_percent"] == cov["percent"]
        assert _cents(r["coverage_cap_amount"]) == cov["capCents"]
        assert _cents(r["coverage_amount"]) == cov["amountCents"]
        assert (r["item_codes"] or "") == ", ".join(b["itemCodes"])
        if b["limits"]:
            [lim] = b["limits"]
            value = _cents(r["limit_value"]) if r["limit_unit"] == "dollars" else r["limit_value"]
            assert value == lim["value"]
            assert r["limit_unit"] == ("dollars" if lim["unit"] == "cents" else lim["unit"])
            assert r["limit_period_kind"] == lim["period"]["kind"]
            assert r["limit_scope"] == lim["scope"]
        else:
            assert r["limit_value"] is None and r["limit_unit"] is None
        freq = b["frequency"]
        assert r["frequency_count"] == (freq["count"] if freq else None)
        assert r["frequency_period_months"] == (freq["period"]["months"] if freq else None)
        wp = b["waitingPeriod"]
        assert r["waiting_period_months"] == (wp["months"] if wp else None)
        assert r["pool_name"] == (pools[b["poolId"]]["name"] if b["poolId"] else None)
        for item in cov["scheduleItems"]:
            assert item["itemCode"] in (r["notes"] or "")


@pytest.mark.parametrize("doc", BOOKLETS)
def test_extraction_pools_cost_shares_rules_header_match_fixture(doc: str) -> None:
    ex = load(f"golden/{doc}.extraction.json")
    p = plan(doc)
    assert [(r["pool_name"], _cents(r["limit_value"]), r["page"]) for r in ex["pools"]] == [
        (pool["name"], pool["limit"]["value"], pool["source"]["page"]) for pool in p["limitPools"]
    ]
    assert [(r["name"], r["kind"], _cents(r["amount"]), r["scope"]) for r in ex["cost_shares"]] == [
        (cs["name"], cs["kind"], cs["amountCents"], cs["scope"]) for cs in p["costShares"]
    ]
    rules = p["claimRules"]
    [deadline] = [r for r in ex["rules"] if r["rule_kind"] == "submission_deadline"]
    assert deadline["quote"] == rules["source"]["quote"]
    if rules["submissionDays"]:
        assert (deadline["deadline_days"], deadline["deadline_basis"]) == (rules["submissionDays"], "service_date")
    else:
        assert (deadline["deadline_days"], deadline["deadline_basis"]) == (rules["daysAfterPeriodEnd"], "period_end")
    assert any(r["rule_kind"] == "receipts_required" for r in ex["rules"]) == rules["receiptsRequired"]
    rule_texts = " ".join(r["text"] or "" for r in ex["rules"])
    for note in rules["notes"]:
        assert note in rule_texts

    header: dict[str, Any] = {}
    for row in ex["header"]:
        header.update({k: v for k, v in row.items() if v is not None})
    assert header["insurer"] == p["insurer"]
    assert header["plan_name"] == p["planName"]
    assert header["currency"] == p["currency"]
    assert header["effective_date"] == p["effectiveDate"]
    assert header["benefit_period_kind"] == p["benefitPeriod"]["kind"]
    assert header["benefit_period_start_month"] == p["benefitPeriod"]["startMonth"]
    prof = p["profile"]
    if prof["kind"] == "CA":
        assert header["province"] == prof["province"]
        assert _cents(header["hsa_annual_credit"]) == prof["hsa"]["annualCreditCents"]
        assert header["hsa_carry_forward_years"] == prof["hsa"]["carryForwardYears"]
        assert ex["hospital_categories"] == []
    else:
        assert header["cover_type"] == prof["coverType"]
        assert header["hospital_tier"] == prof["hospital"]["tier"]
        assert header["hospital_plus"] == prof["hospital"]["plus"]
        assert [(r["name"], r["status"], r["page"], r["quote"]) for r in ex["hospital_categories"]] == [
            (c["name"], c["status"], c["source"]["page"], c["source"]["quote"]) for c in prof["hospital"]["categories"]
        ]
