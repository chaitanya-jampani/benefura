"""Minimal PDF writer. Regenerate the fixture with ``uv run python -m knowledge.tests.pdfgen``."""

from __future__ import annotations

from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []
    font_id = 3
    page_ids: list[int] = []
    content_objs: list[tuple[int, bytes]] = []
    next_id = 4
    for lines in pages:
        stream_lines = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        for line in lines:
            stream_lines.append(f"({_escape(line)}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        content_objs.append((page_id, content_id.to_bytes(4, "big")))
        objects.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >> endobj\n".encode()
        )
        objects.append(
            f"{content_id} 0 obj << /Length {len(stream)} >> stream\n".encode() + stream + b"\nendstream endobj\n"
        )
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    header = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >> endobj\n".encode(),
        b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    all_objects = header + objects
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in all_objects:
        offsets.append(len(out))
        out += obj
    xref_at = len(out)
    out += f"xref\n0 {len(all_objects) + 1}\n0000000000 65535 f \n".encode()
    # Offsets line up with object ids because objects are written in id order.
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(all_objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


SYNTHETIC_GUIDE = [
    [
        "# Synthetic guide to claiming medical expenses",
        "This synthetic test document is not real guidance. It exists only to exercise the PDF path.",
        "Eligible expenses in this fictional guide include prescription glasses and physiotherapy.",
        "Keep receipts for every expense you claim, and claim within the period the guide describes.",
    ],
    [
        "# Synthetic travel section",
        "Fictional rule: emergency travel costs outside the home province are reviewed case by case.",
        "Fictional rule: attendant care expenses need a written certification from a practitioner.",
    ],
]


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / "synthetic-guide.pdf"
    target.write_bytes(make_pdf(SYNTHETIC_GUIDE))
    print(f"wrote {target}")
