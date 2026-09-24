from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from knowledge.ingest.convert import LocalPdfConverter, _document_from_analysis, html_to_markdown
from knowledge.tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_html_conversion_keeps_headings_and_tables_and_drops_chrome():
    doc = html_to_markdown((FIXTURES / "ca-medical-expenses.html").read_bytes())
    md = doc.markdown
    assert doc.title == "Synthetic eligible medical expenses"
    assert md.startswith("# Synthetic eligible medical expenses")
    assert "## Who can claim" in md
    assert "| Expense | Eligible | Conditions |" in md
    assert "| Prescription glasses | Yes | Prescribed by an optometrist or medical doctor |" in md
    for chrome in ("Skip to main content", "Home", "Date modified", "Terms and conditions", "Privacy"):
        assert chrome not in md, chrome
    assert "](" not in md  # link targets dropped


def test_html_conversion_without_main_element_uses_content_div():
    doc = html_to_markdown((FIXTURES / "au-waiting-periods.html").read_bytes())
    assert "| Treatment | Maximum waiting period |" in doc.markdown
    assert "Copyright notice" not in doc.markdown
    assert "### Changing insurers" in doc.markdown


async def test_local_pdf_converter_extracts_pages():
    data = make_pdf([["# First page", "Alpha text."], ["# Second page", "Beta text."]])
    doc = await LocalPdfConverter().convert(data)
    assert [p.page for p in doc.pages] == [1, 2]
    assert "## First page" in doc.markdown and "Beta text." in doc.markdown
    assert doc.cu_pages == 0


@dataclass
class Span:
    offset: int
    length: int


@dataclass
class Page:
    page_number: int
    spans: list[Span]


@dataclass
class Content:
    markdown: str
    pages: list[Page]
    start_page_number: int = 1
    end_page_number: int = 2


@dataclass
class Result:
    contents: list[Content]


def test_content_understanding_result_mapping():
    markdown = "# Guide\n\nPage one text.\n<!-- PageBreak -->\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    split = markdown.index("<!-- PageBreak -->")
    result = Result([Content(markdown, [Page(1, [Span(0, split)]), Page(2, [Span(split, len(markdown) - split)])])])
    doc = _document_from_analysis(result)
    assert doc.cu_pages == 2
    assert doc.pages[0].markdown.startswith("# Guide")
    assert "| 1 | 2 |" in doc.pages[1].markdown
    assert doc.converter == "content-understanding"
    assert _document_from_analysis(Result([])).markdown == ""
