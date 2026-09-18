from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SAMPLES = Path(__file__).resolve().parent.parent

# doc id -> (pdf file, page count)
BOOKLETS = {
    "ca-northwind": "ca-northwind-booklet.pdf",
    "au-wattle": "au-wattle-policy.pdf",
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load(rel: str) -> Any:
    return json.loads((SAMPLES / rel).read_text(encoding="utf-8"))


@cache
def page_texts(pdf: str) -> dict[int, str]:
    reader = PdfReader(SAMPLES / pdf)
    return {i: norm(page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)}


def golden_pages(doc: str) -> dict[int, str]:
    return {p["page"]: norm(p["markdown"]) for p in load(f"golden/{doc}.pages.json")}
