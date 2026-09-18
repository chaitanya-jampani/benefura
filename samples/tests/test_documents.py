from __future__ import annotations

import pytest
from pypdf import PdfReader

from conftest import BOOKLETS, SAMPLES, load, norm, page_texts
from samplegen.fixtures import plan, quotes
from samplegen.pii import luhn_ok, medicare_ok

TOKENS = {
    "[MEMBER_A]",
    "[MEMBER_B]",
    "[MEMBER_C]",
    "[EMPLOYER_A]",
    "[POLICY_1]",
    "[CERT_1]",
    "[ADDRESS_A]",
    "[PHONE_A]",
    "[EMAIL_A]",
}
BLACKED_OUT_KINDS = {"sin", "health_card_number", "medicare_number", "date_of_birth", "bsb", "bank_account_number"}


@pytest.mark.parametrize("doc", BOOKLETS)
def test_page_count_matches_fixture(doc: str) -> None:
    assert len(PdfReader(SAMPLES / BOOKLETS[doc]).pages) == plan(doc)["document"]["pageCount"]
    assert plan(doc)["document"]["name"] == BOOKLETS[doc]


def test_variant_page_counts() -> None:
    assert len(PdfReader(SAMPLES / "ca-northwind-booklet-injected.pdf").pages) == 15
    assert len(PdfReader(SAMPLES / "au-wattle-policy-scanned.pdf").pages) == 12


@pytest.mark.parametrize(
    "pdf", ["ca-northwind-booklet.pdf", "ca-northwind-booklet-injected.pdf", "au-wattle-policy.pdf"]
)
def test_every_fixture_quote_is_on_its_page(pdf: str) -> None:
    doc = "ca-northwind" if pdf.startswith("ca-") else "au-wattle"
    texts = page_texts(pdf)
    missing = [(key, src["page"]) for key, src in quotes(doc).items() if norm(src["quote"]) not in texts[src["page"]]]
    assert not missing


@pytest.mark.parametrize(
    ("pdf", "pii"),
    [
        ("ca-northwind-booklet.pdf", "fixtures/ca-northwind.pii.json"),
        ("ca-northwind-booklet-injected.pdf", "fixtures/ca-northwind-injected.pii.json"),
        ("au-wattle-policy.pdf", "fixtures/au-wattle.pii.json"),
    ],
)
def test_pii_json_is_exact(pdf: str, pii: str) -> None:
    entries = load(pii)
    texts = page_texts(pdf)
    for e in entries:
        assert e["value"] in texts[e["page"]], e
    listed = {(e["value"], e["page"]) for e in entries}
    for value in {e["value"] for e in entries}:
        for page, text in texts.items():
            if value in text:
                assert (value, page) in listed, (value, page)


@pytest.mark.parametrize("doc", BOOKLETS)
def test_pii_entries_are_well_formed(doc: str) -> None:
    entries = load(f"fixtures/{doc}.pii.json")
    kinds = {e["kind"] for e in entries}
    for e in entries:
        assert set(e) == {"value", "kind", "page", "aliasToken"}
        if e["kind"] in BLACKED_OUT_KINDS:
            assert e["aliasToken"] is None, e
        else:
            assert e["aliasToken"] in TOKENS, e
    tokens = {e["aliasToken"] for e in entries}
    if doc == "ca-northwind":
        assert kinds >= {
            "person_name",
            "date_of_birth",
            "employer_name",
            "group_policy_number",
            "certificate_number",
            "sin",
            "health_card_number",
            "address",
            "phone",
            "email",
        }
        assert tokens >= {
            "[MEMBER_A]",
            "[MEMBER_B]",
            "[MEMBER_C]",
            "[EMPLOYER_A]",
            "[POLICY_1]",
            "[CERT_1]",
            "[ADDRESS_A]",
            "[PHONE_A]",
            "[EMAIL_A]",
        }
    else:
        assert kinds >= {
            "person_name",
            "date_of_birth",
            "membership_number",
            "medicare_number",
            "address",
            "phone",
            "email",
            "bsb",
            "bank_account_number",
        }
        assert tokens >= {"[MEMBER_A]", "[MEMBER_B]", "[POLICY_1]", "[ADDRESS_A]", "[PHONE_A]", "[EMAIL_A]"}


def test_identifiers_pass_checksums_but_are_obviously_fictional() -> None:
    by_kind = {
        e["kind"]: e["value"] for e in load("fixtures/ca-northwind.pii.json") + load("fixtures/au-wattle.pii.json")
    }
    assert by_kind["sin"].startswith("046") and luhn_ok(by_kind["sin"])
    assert luhn_ok(by_kind["health_card_number"][:12])
    assert medicare_ok(by_kind["medicare_number"])
    assert "-555-01" in by_kind_ca("phone")
    assert by_kind_au("phone").startswith("0491 570")  # ACMA fictitious mobile range
    assert by_kind_ca("email").endswith("@example.net") and by_kind_au("email").endswith("@example.org")


def by_kind_ca(kind: str) -> str:
    return next(e["value"] for e in load("fixtures/ca-northwind.pii.json") if e["kind"] == kind)


def by_kind_au(kind: str) -> str:
    return next(e["value"] for e in load("fixtures/au-wattle.pii.json") if e["kind"] == kind)


def test_direct_debit_page_has_bank_details() -> None:
    bank = [e for e in load("fixtures/au-wattle.pii.json") if e["kind"] in {"bsb", "bank_account_number"}]
    assert {e["kind"] for e in bank} == {"bsb", "bank_account_number"}
    assert {e["page"] for e in bank} == {12}


def test_insurer_contact_details_are_not_listed_as_pii() -> None:
    values = {e["value"] for e in load("fixtures/ca-northwind.pii.json") + load("fixtures/au-wattle.pii.json")}
    assert not any(
        "Northwind" in v or "Wattle Health" in v or "1300 975 707" in v or "1-800-555-0142" in v for v in values
    )


def test_injection_page_is_recorded() -> None:
    inj = load("fixtures/injection.json")
    [entry] = inj["documents"]
    assert entry["file"] == "ca-northwind-booklet-injected.pdf"
    texts = page_texts(entry["file"])
    assert entry["page"] == len(texts) == 15
    assert norm(entry["text"]) in texts[entry["page"]]
    assert norm(entry["text"]) in norm(entry["markdown"])
    # The injection appears nowhere in the clean booklet, and pages 1-14 are otherwise the same.
    clean = page_texts("ca-northwind-booklet.pdf")
    assert all(norm(entry["text"]) not in t for t in clean.values())
    assert all(clean[i] == texts[i] for i in clean)


def test_scanned_variant_has_no_text_layer() -> None:
    reader = PdfReader(SAMPLES / "au-wattle-policy-scanned.pdf")
    clean = PdfReader(SAMPLES / "au-wattle-policy.pdf")
    for page, original in zip(reader.pages, clean.pages, strict=True):
        assert (page.extract_text() or "").strip() == ""
        assert len(page.images) == 1
        assert [round(float(v)) for v in page.mediabox] == [round(float(v)) for v in original.mediabox]


@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("ca-northwind-booklet.pdf", 1_000_000),
        ("ca-northwind-booklet-injected.pdf", 1_000_000),
        ("au-wattle-policy.pdf", 1_000_000),
        ("au-wattle-policy-scanned.pdf", 4_000_000),
    ],
)
def test_file_sizes(name: str, limit: int) -> None:
    assert (SAMPLES / name).stat().st_size < limit
