from __future__ import annotations

import io
from datetime import date

import pytest
from app.models.receipt import Receipt
from PIL import Image
from pypdf import PdfReader

from conftest import SAMPLES, load, norm
from samplegen.pii import AU_PII, CA_PII
from samplegen.receipts import ANCHOR_DATE, RECEIPTS, ReceiptSpec, receipt_pdf

BY_NAME = {r.name: r for r in RECEIPTS}


def _text(spec: ReceiptSpec) -> str:
    pdf, _ = receipt_pdf(spec)
    return norm(PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "")


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def test_expected_receipt_set() -> None:
    names = {p.name.removesuffix(".png") for p in (SAMPLES / "receipts").glob("*.png")}
    assert (
        names
        == set(BY_NAME)
        == {"ca-rmt-massage", "ca-optometry", "au-physio", "au-dental", "ca-eob", "injection-receipt"}
    )


@pytest.mark.parametrize("name", BY_NAME)
def test_expected_json_validates_and_is_consistent(name: str) -> None:
    receipt = Receipt.model_validate(load(f"receipts/{name}.expected.json"))
    spec = BY_NAME[name]
    assert receipt.currency == ("CAD" if spec.region == "CA" else "AUD")
    assert receipt.totalCents == sum(line.amountCents or 0 for line in receipt.serviceLines)
    anchor = date.fromisoformat(ANCHOR_DATE)
    for line in receipt.serviceLines:
        assert line.serviceDate is not None and 0 <= (anchor - line.serviceDate).days <= 60


@pytest.mark.parametrize("name", BY_NAME)
def test_expected_values_are_printed_on_the_receipt(name: str) -> None:
    spec = BY_NAME[name]
    receipt = Receipt.model_validate(load(f"receipts/{name}.expected.json"))
    text = _text(spec)
    assert receipt.providerName and receipt.providerName in text
    if receipt.providerRegistrationNo:
        assert receipt.providerRegistrationNo in text
    assert _money(receipt.totalCents or 0) in text
    if receipt.insurerPaidCents is not None:
        assert _money(receipt.insurerPaidCents) in text
    for line in receipt.serviceLines:
        assert line.description and line.description in text
        assert _money(line.amountCents or 0) in text
        if line.itemCode:
            assert line.itemCode in text
        assert line.serviceDate is not None
        iso = line.serviceDate.isoformat()
        au = line.serviceDate.strftime("%d/%m/%Y")
        assert (iso if spec.region == "CA" else au) in text


@pytest.mark.parametrize("name", BY_NAME)
def test_png_dimensions_and_size(name: str) -> None:
    path = SAMPLES / "receipts" / f"{name}.png"
    with Image.open(path) as img:
        assert img.width == 1200
        assert 700 <= img.height <= 1400
    assert path.stat().st_size < 1_000_000


@pytest.mark.parametrize("name", BY_NAME)
def test_pii_json_matches_receipt(name: str) -> None:
    spec = BY_NAME[name]
    entries = load(f"receipts/{name}.pii.json")
    _, regenerated = receipt_pdf(spec)
    assert entries == regenerated
    assert entries, "every receipt names a patient"
    text = _text(spec)
    registry = CA_PII if spec.region == "CA" else AU_PII
    allowed = {v.value for v in registry.values()}
    for e in entries:
        assert e["page"] == 1
        assert e["value"] in text
        assert e["value"] in allowed  # receipts reuse the booklet's people, so saved aliases apply
    assert any(
        e["kind"] == "person_name" and e["aliasToken"] and e["aliasToken"].startswith("[MEMBER_") for e in entries
    )


def test_injection_receipt_is_recorded() -> None:
    inj = load("fixtures/injection.json")
    [entry] = inj["receipts"]
    assert entry["file"] == "receipts/injection-receipt.png"
    assert norm(entry["text"]) in _text(BY_NAME["injection-receipt"])
    expected = Receipt.model_validate(load("receipts/injection-receipt.expected.json"))
    assert expected.totalCents == 7500  # the instruction must not change the amounts


def test_receipts_index() -> None:
    index = load("golden/receipts.expected.json")
    assert index["anchorDate"] == ANCHOR_DATE
    assert [r["file"] for r in index["receipts"]] == [f"receipts/{r.name}.png" for r in RECEIPTS]
    for r in index["receipts"]:
        assert r["receipt"] == load(r["expected"])
        assert r["injection"] == (r["file"] == "receipts/injection-receipt.png")
