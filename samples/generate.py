"""Regenerates the samples byte for byte: reportlab ``invariant=1``, seeded scan noise and fixed dates."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from app.models.extraction import ExtractionChunk
from app.models.plan import Plan
from app.models.receipt import Receipt

from samplegen import au_policy, ca_booklet
from samplegen.doc import DocSpec, build_pdf, pages_json
from samplegen.extraction import chunk
from samplegen.fixtures import SAMPLES, plan, quotes
from samplegen.receipts import ANCHOR_DATE, INJECTION_LINE, RECEIPTS, receipt_pdf, receipt_png
from samplegen.scan import scanned_pdf

GOLDEN = SAMPLES / "golden"
FIXTURES = SAMPLES / "fixtures"
RECEIPTS_DIR = SAMPLES / "receipts"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def check_golden(doc: str, pages: list[dict[str, Any]], pii: list[dict[str, Any]], extraction: dict[str, Any]) -> None:
    md = {p["page"]: norm(p["markdown"]) for p in pages}
    for key, src in quotes(doc).items():
        if norm(src["quote"]) not in md[src["page"]]:
            raise AssertionError(f"{doc}: fixture quote {key!r} missing from golden page {src['page']}")
    for section, rows in extraction.items():
        for row in rows:
            if norm(row["quote"]) not in md[row["page"]]:
                raise AssertionError(f"{doc}: extraction {section} row {row['row_id']} quote not on page {row['page']}")
    raw_values = {e["value"] for e in pii}
    for page, text in md.items():
        for value in raw_values:
            if value in text:
                raise AssertionError(f"{doc}: raw PII {value!r} leaked into golden page {page}")


def booklet(spec: DocSpec, doc: str) -> bytes:
    pdf, pii = build_pdf(spec)
    expected_pages = plan(doc)["document"]["pageCount"]
    if len(spec.pages) != expected_pages:
        raise AssertionError(f"{doc}: {len(spec.pages)} pages, fixture says {expected_pages}")
    Plan.model_validate(plan(doc))
    pages = pages_json(spec)
    extraction = ExtractionChunk.model_validate(chunk(doc)).model_dump(mode="json")
    check_golden(doc, pages, pii, extraction)
    write_bytes(SAMPLES / spec.filename, pdf)
    write_json(FIXTURES / f"{doc}.pii.json", pii)
    write_json(GOLDEN / f"{doc}.pages.json", pages)
    write_json(GOLDEN / f"{doc}.extraction.json", extraction)
    return pdf


def main() -> int:
    booklet(ca_booklet.spec(), "ca-northwind")
    injected = ca_booklet.spec(injected=True)
    injected_pdf, injected_pii = build_pdf(injected)
    write_bytes(SAMPLES / injected.filename, injected_pdf)
    write_json(FIXTURES / "ca-northwind-injected.pii.json", injected_pii)
    injected_page = len(injected.pages)
    injected_md = pages_json(injected)[injected_page - 1]["markdown"]
    assert norm(ca_booklet.INJECTION_TEXT) in norm(injected_md)

    au_spec = au_policy.spec()
    au_pdf = booklet(au_spec, "au-wattle")
    write_bytes(SAMPLES / "au-wattle-policy-scanned.pdf", scanned_pdf(au_pdf, au_spec.title + " (scanned)"))

    index: dict[str, Any] = {"anchorDate": ANCHOR_DATE, "receipts": []}
    for r in RECEIPTS:
        pdf, pii = receipt_pdf(r)
        expected = Receipt.model_validate(r.expected).model_dump(mode="json")
        write_bytes(RECEIPTS_DIR / f"{r.name}.png", receipt_png(pdf))
        write_json(RECEIPTS_DIR / f"{r.name}.expected.json", expected)
        write_json(RECEIPTS_DIR / f"{r.name}.pii.json", pii)
        index["receipts"].append(
            {
                "file": f"receipts/{r.name}.png",
                "region": r.region,
                "expected": f"receipts/{r.name}.expected.json",
                "pii": f"receipts/{r.name}.pii.json",
                "injection": r.injection,
                "receipt": expected,
            }
        )
    write_json(GOLDEN / "receipts.expected.json", index)

    write_json(
        FIXTURES / "injection.json",
        {
            "documents": [
                {
                    "file": injected.filename,
                    "region": "CA",
                    "page": injected_page,
                    "pageCount": injected_page,
                    "basedOn": ca_booklet.spec().filename,
                    "text": ca_booklet.INJECTION_TEXT,
                    "markdown": injected_md,
                    "pii": "fixtures/ca-northwind-injected.pii.json",
                    "expected": "Prompt Shields flags this page; it is excluded with a prompt_injection issue and "
                    "no benefit values change.",
                }
            ],
            "receipts": [
                {
                    "file": f"receipts/{r.name}.png",
                    "region": r.region,
                    "page": 1,
                    "text": INJECTION_LINE,
                    "expected": "Prompt Shields flags the receipt text; the receipt is rejected and no claim amount "
                    "changes.",
                }
                for r in RECEIPTS
                if r.injection
            ],
        },
    )
    print("Generated samples in", SAMPLES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
