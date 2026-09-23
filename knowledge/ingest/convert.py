"""HTML and PDF to markdown. Link targets and images are dropped because they add retrieval noise."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

logger = logging.getLogger(__name__)

CU_LAYOUT_ANALYZER = "prebuilt-layout"

_MAIN_SELECTORS = ("main", "[role=main]", "#wb-cont", "#pagecontent", "#main-content", "#content", "article", "body")
_DROP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "canvas",
    "form",
    "button",
    "nav",
    "header",
    "footer",
    "aside",
    "[role=navigation]",
    "[role=banner]",
    "[role=contentinfo]",
    "[role=search]",
    "[aria-hidden=true]",
    ".breadcrumb",
    ".breadcrumbs",
    "#wb-bc",
    "#wb-lng",
    "#wb-srch",
    ".pagedetails",
    "#wb-dtmd",
    ".gc-pg-hlpfl",
    ".skip-link",
    ".visually-hidden",
    ".wb-inv",
    ".sr-only",
    ".share",
    ".feedback",
    ".cookie",
    "#subnav",  # privatehealth.gov.au side menu
    "#menu",  # privatehealth.gov.au promo tiles
    ".btn-group",  # canada.ca A-Z jump buttons
    ".toc__wrapper",  # ontario.ca "On this page"
)
_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


@dataclass
class PageMarkdown:
    page: int
    markdown: str


@dataclass
class ConvertedDocument:
    markdown: str
    title: str | None = None
    pages: list[PageMarkdown] = field(default_factory=list)
    cu_pages: int = 0
    converter: str = "html"


def _pick_main(soup: BeautifulSoup) -> Tag:
    for selector in _MAIN_SELECTORS:
        found = soup.select_one(selector)
        if isinstance(found, Tag) and found.get_text(strip=True):
            return found
    return soup


def _tidy(markdown: str) -> str:
    text = markdown.replace("\xa0", " ")
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip() + "\n"


def html_to_markdown(html: str | bytes) -> ConvertedDocument:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    main = _pick_main(soup)
    for selector in _DROP_SELECTORS:
        for node in main.select(selector):
            if node is not main:
                node.decompose()
    md = markdownify(
        str(main),
        heading_style="ATX",
        bullets="-",
        strip=["a", "img", "span"],
        table_infer_header=True,
        escape_underscores=False,
        escape_asterisks=False,
    )
    return ConvertedDocument(markdown=_tidy(md), title=title, converter="html")


class PdfConverter(Protocol):
    name: str

    async def convert(self, data: bytes) -> ConvertedDocument: ...


class ContentUnderstandingPdfConverter:
    """Page spans are code-point offsets because the SDK forces ``string_encoding=codePoint`` for Python."""

    name = "content-understanding"

    def __init__(self, endpoint: str | None = None) -> None:
        self._endpoint = endpoint

    async def convert(self, data: bytes) -> ConvertedDocument:
        from azure.ai.contentunderstanding.aio import ContentUnderstandingClient

        from app.config import get_settings
        from app.services.foundry import get_credential

        endpoint = self._endpoint or get_settings().ai_services_endpoint
        if not endpoint:
            raise RuntimeError("AI_SERVICES_ENDPOINT is not set")
        # M0-verify: CU data-plane calls on the AIServices endpoint with an Entra token.
        async with ContentUnderstandingClient(endpoint=endpoint, credential=get_credential()) as client:
            poller = await client.begin_analyze_binary(CU_LAYOUT_ANALYZER, data, content_type="application/pdf")
            result = await poller.result()
            operation_id = poller.operation_id
            try:
                return _document_from_analysis(result)
            finally:
                try:
                    await client.delete_result(operation_id)
                except Exception as exc:  # the result expires after 24 h anyway; never fail the run
                    logger.warning("CU delete_result failed: %s", type(exc).__name__)


def _document_from_analysis(result: object) -> ConvertedDocument:
    contents = list(getattr(result, "contents", None) or [])
    if not contents:
        return ConvertedDocument(markdown="", converter="content-understanding")
    doc = contents[0]
    markdown: str = getattr(doc, "markdown", None) or ""
    pages: list[PageMarkdown] = []
    for page in getattr(doc, "pages", None) or []:
        spans = getattr(page, "spans", None) or []
        text = "".join(markdown[s.offset : s.offset + s.length] for s in spans)
        pages.append(PageMarkdown(page=page.page_number, markdown=text))
    start = getattr(doc, "start_page_number", None)
    end = getattr(doc, "end_page_number", None)
    cu_pages = len(pages) or ((end - start + 1) if start and end else 0)
    return ConvertedDocument(
        markdown=_tidy(markdown), pages=pages, cu_pages=cu_pages, converter="content-understanding"
    )


class LocalPdfConverter:
    """Offline test double: pypdf text only, no OCR or tables."""

    name = "local-pypdf"

    async def convert(self, data: bytes) -> ConvertedDocument:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages: list[PageMarkdown] = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            lines = [ln.strip() for ln in text.splitlines()]
            md_lines = [f"## {ln[2:].strip()}" if ln.startswith("# ") else ln for ln in lines]
            pages.append(PageMarkdown(number, "\n".join(md_lines)))
        markdown = "\n\n".join(p.markdown for p in pages)
        return ConvertedDocument(markdown=_tidy(markdown), pages=pages, cu_pages=0, converter=self.name)
