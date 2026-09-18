"""One block model rendered to both the PDF and CU-style golden markdown, so the two can't drift apart."""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from samplegen.pii import PiiTracker, PiiValue, redacted

BOLD = re.compile(r"\*\*(.+?)\*\*")


@dataclass(frozen=True)
class Theme:
    primary: colors.Color
    accent: colors.Color
    tint: colors.Color
    ink: colors.Color
    muted: colors.Color
    rule: colors.Color
    zebra: colors.Color
    pagesize: tuple[float, float]
    margin_x: float = 58
    margin_top: float = 80
    margin_bottom: float = 66
    body_size: float = 9.4
    body_leading: float = 13.2


def styles(t: Theme) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=t.body_size,
        leading=t.body_leading,
        textColor=t.ink,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    return {
        "body": base,
        "lead": ParagraphStyle(
            "lead", parent=base, fontSize=t.body_size + 1.4, leading=t.body_leading + 2.4, spaceAfter=8
        ),
        "small": ParagraphStyle(
            "small", parent=base, fontSize=t.body_size - 1.4, leading=t.body_leading - 2.2, textColor=t.muted
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=19,
            leading=23,
            textColor=t.primary,
            spaceBefore=0,
            spaceAfter=9,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=16,
            textColor=t.primary,
            spaceBefore=9,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=10.2,
            leading=13.5,
            textColor=t.accent,
            spaceBefore=6,
            spaceAfter=3,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            leftIndent=13,
            bulletIndent=2,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=t.body_size,
            bulletColor=t.accent,
            spaceAfter=4,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base, fontSize=t.body_size - 0.9, leading=t.body_leading - 1.8, spaceAfter=0
        ),
        "cellhead": ParagraphStyle(
            "cellhead",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=t.body_size - 0.9,
            leading=t.body_leading - 1.8,
            textColor=colors.white,
            spaceAfter=0,
        ),
        "callout_title": ParagraphStyle(
            "callout_title", parent=base, fontName="Helvetica-Bold", textColor=t.primary, spaceAfter=2
        ),
        "callout": ParagraphStyle("callout", parent=base, spaceAfter=2),
        "status_desc": ParagraphStyle(
            "status_desc",
            parent=base,
            fontSize=t.body_size - 1.2,
            leading=t.body_leading - 2.4,
            textColor=t.muted,
            spaceAfter=0,
        ),
    }


@dataclass
class H:
    level: int
    text: str


@dataclass
class P:
    text: str
    style: str = "body"


@dataclass
class Bullets:
    items: list[str]


@dataclass
class Grid:
    header: list[str]
    rows: list[list[str]]
    widths: list[float]  # fractions of the frame width
    label_col: bool = False


@dataclass
class Callout:
    title: str | None
    paragraphs: list[str]


@dataclass
class TwoCol:
    left: list[Callout]
    right: list[Callout]


@dataclass
class Status:
    name: str
    status: str  # "Covered" | "Restricted" | "Not covered"
    description: str


@dataclass
class Gap:
    height: float


Block = H | P | Bullets | Grid | Callout | TwoCol | Status | Gap


@dataclass
class CoverText:
    x: float
    y: float
    text: str
    font: str
    size: float
    color: colors.Color
    md: str | None = "p"  # None when not emitted on its own
    md_text: str | None = None  # e.g. one paragraph drawn over several lines
    align: str = "left"


@dataclass
class Page:
    blocks: list[Block] = field(default_factory=list)
    cover: Callable[[Canvas, Theme], list[CoverText]] | None = None


@dataclass
class DocSpec:
    filename: str
    title: str
    subject: str
    author: str
    theme: Theme
    header_left: str
    header_right: str
    footer_left: str
    pages: list[Page]
    registry: dict[str, PiiValue]


def pdf_markup(text: str) -> str:
    return BOLD.sub(r"<b>\1</b>", escape(text))


def md_text(text: str, registry: dict[str, PiiValue]) -> str:
    return redacted(BOLD.sub(r"\1", text), registry)


class StatusIcon(Flowable):
    def __init__(self, status: str, theme: Theme, size: float = 11):
        super().__init__()
        self.status = status
        self.theme = theme
        self.size = size

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        return self.size, self.size

    def draw(self) -> None:
        c = self.canv
        s = self.size
        r = s / 2
        fill = {
            "Covered": self.theme.accent,
            "Restricted": colors.HexColor("#C98B12"),
            "Not covered": colors.HexColor("#9A3B34"),
        }[self.status]
        c.setFillColor(fill)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.4)
        c.setLineCap(1)
        if self.status == "Covered":
            p = c.beginPath()
            p.moveTo(s * 0.27, s * 0.52)
            p.lineTo(s * 0.44, s * 0.34)
            p.lineTo(s * 0.74, s * 0.68)
            c.drawPath(p, stroke=1, fill=0)
        elif self.status == "Restricted":
            c.line(s * 0.28, r, s * 0.72, r)
        else:
            c.line(s * 0.32, s * 0.32, s * 0.68, s * 0.68)
            c.line(s * 0.32, s * 0.68, s * 0.68, s * 0.32)


class PageMarker(Flowable):
    """Zero-size flowable that records which physical page it landed on."""

    def __init__(self, label: tuple[str, int], seen: dict[tuple[str, int], int]):
        super().__init__()
        self.label = label
        self.seen = seen

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        return 0, 0

    def draw(self) -> None:
        self.seen[self.label] = self.canv.getPageNumber()


def _flowables(
    blocks: Sequence[Block], st: dict[str, ParagraphStyle], t: Theme, raw: Callable[[str], str], width: float
) -> list[Flowable]:
    out: list[Flowable] = []
    for b in blocks:
        if isinstance(b, H):
            out.append(Paragraph(pdf_markup(raw(b.text)), st[f"h{b.level}"]))
        elif isinstance(b, P):
            out.append(Paragraph(pdf_markup(raw(b.text)), st[b.style]))
        elif isinstance(b, Bullets):
            for item in b.items:
                out.append(Paragraph(pdf_markup(raw(item)), st["bullet"], bulletText="•"))
        elif isinstance(b, Grid):
            data = [[Paragraph(pdf_markup(raw(h)), st["cellhead"]) for h in b.header]]
            data += [[Paragraph(pdf_markup(raw(c)), st["cell"]) for c in row] for row in b.rows]
            tbl = Table(data, colWidths=[w * width for w in b.widths], repeatRows=1, hAlign="LEFT")
            cmds: list[tuple] = [
                ("BACKGROUND", (0, 0), (-1, 0), t.primary),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, t.rule),
            ]
            for i in range(1, len(data)):
                if i % 2 == 0:
                    cmds.append(("BACKGROUND", (0, i), (-1, i), t.zebra))
            if b.label_col:
                cmds.append(("BACKGROUND", (0, 1), (0, -1), t.tint))
            tbl.setStyle(TableStyle(cmds))
            out.append(tbl)
            out.append(Spacer(1, 8))
        elif isinstance(b, Callout):
            out.append(_callout(b, st, t, raw, width))
            out.append(Spacer(1, 8))
        elif isinstance(b, TwoCol):
            gap = 12
            colw = (width - gap) / 2
            left = [_callout(c, st, t, raw, colw) for c in b.left]
            right = [_callout(c, st, t, raw, colw) for c in b.right]
            tbl = Table([[left, "", right]], colWidths=[colw, gap, colw], hAlign="LEFT")
            tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            out.append(tbl)
            out.append(Spacer(1, 8))
        elif isinstance(b, Status):
            label = Paragraph(pdf_markup(raw(f"**{b.name}:** {b.status}")), st["body"])
            desc = Paragraph(pdf_markup(raw(b.description)), st["status_desc"])
            tbl = Table([[StatusIcon(b.status, t), [label, desc]]], colWidths=[20, width - 20], hAlign="LEFT")
            tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (0, 0), 1.5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.4, t.rule),
                    ]
                )
            )
            out.append(tbl)
            out.append(Spacer(1, 4))
        elif isinstance(b, Gap):
            out.append(Spacer(1, b.height))
    return out


def _callout(c: Callout, st: dict[str, ParagraphStyle], t: Theme, raw: Callable[[str], str], width: float) -> Table:
    inner: list[Flowable] = []
    if c.title:
        inner.append(Paragraph(pdf_markup(raw(c.title)), st["callout_title"]))
    inner += [Paragraph(pdf_markup(raw(p)), st["callout"]) for p in c.paragraphs]
    tbl = Table([[inner]], colWidths=[width], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), t.tint),
                ("LINEBEFORE", (0, 0), (0, -1), 3, t.accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return tbl


def build_pdf(spec: DocSpec) -> tuple[bytes, list[dict[str, object]]]:
    t = spec.theme
    st = styles(t)
    tracker = PiiTracker(spec.registry)
    pw, ph = t.pagesize
    width = pw - 2 * t.margin_x
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=t.pagesize,
        leftMargin=t.margin_x,
        rightMargin=t.margin_x,
        topMargin=t.margin_top,
        bottomMargin=t.margin_bottom,
        title=spec.title,
        author=spec.author,
        subject=spec.subject,
        creator="Benefura samples generator",
        invariant=1,
    )
    frame = Frame(
        t.margin_x,
        t.margin_bottom,
        width,
        ph - t.margin_top - t.margin_bottom,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )

    def draw_cover(canv: Canvas, _doc: BaseDocTemplate) -> None:
        n = canv.getPageNumber()
        page = spec.pages[n - 1]
        assert page.cover is not None
        canv.saveState()
        for ct in page.cover(canv, t):
            canv.setFont(ct.font, ct.size)
            canv.setFillColor(ct.color)
            draw = canv.drawRightString if ct.align == "right" else canv.drawString
            draw(ct.x, ct.y, tracker.raw(ct.text, n))
        canv.restoreState()

    def draw_running(canv: Canvas, _doc: BaseDocTemplate) -> None:
        n = canv.getPageNumber()
        canv.saveState()
        top = ph - 46
        canv.setFillColor(t.primary)
        canv.setFont("Helvetica-Bold", 8.6)
        canv.drawString(t.margin_x, top, tracker.raw(spec.header_left, n))
        canv.setFillColor(t.muted)
        canv.setFont("Helvetica", 8.2)
        canv.drawRightString(pw - t.margin_x, top, tracker.raw(spec.header_right, n))
        canv.setStrokeColor(t.accent)
        canv.setLineWidth(1.2)
        canv.line(t.margin_x, top - 8, pw - t.margin_x, top - 8)
        canv.setStrokeColor(t.rule)
        canv.setLineWidth(0.6)
        canv.line(t.margin_x, 46, pw - t.margin_x, 46)
        canv.setFillColor(t.muted)
        canv.setFont("Helvetica", 7.6)
        canv.drawString(t.margin_x, 32, tracker.raw(spec.footer_left, n))
        canv.setFillColor(t.primary)
        canv.setFont("Helvetica-Bold", 8.2)
        canv.drawRightString(pw - t.margin_x, 32, f"Page {n}")
        canv.restoreState()

    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame], onPage=draw_cover),
            PageTemplate(id="body", frames=[frame], onPage=draw_running),
        ]
    )
    if spec.pages[0].cover is None:
        doc.pageTemplates.reverse()

    seen: dict[tuple[str, int], int] = {}
    story: list[Flowable] = []
    for i, page in enumerate(spec.pages, start=1):
        story.append(PageMarker(("start", i), seen))
        story += _flowables(page.blocks, st, t, lambda s, i=i: tracker.raw(s, i), width)
        story.append(PageMarker(("end", i), seen))
        if i < len(spec.pages):
            story.append(NextPageTemplate("cover" if spec.pages[i].cover else "body"))
            story.append(PageBreak())
    doc.build(story)

    for (edge, i), actual in seen.items():
        if actual != i:
            raise AssertionError(f"{spec.filename}: page {i} content {edge} landed on page {actual} (overflow?)")
    return buf.getvalue(), tracker.entries()


def _md_cell(text: str, registry: dict[str, PiiValue]) -> str:
    return md_text(text, registry).replace("|", "\\|")


def _md_callout(c: Callout, registry: dict[str, PiiValue]) -> list[str]:
    parts = []
    if c.title:
        parts.append(md_text(c.title, registry))
    parts += [md_text(p, registry) for p in c.paragraphs]
    return parts


def page_markdown(spec: DocSpec, index: int, canvas_for_cover: Canvas | None = None) -> str:
    """``index`` is 1-based; the text reads as CU would see the redacted page image."""

    reg = spec.registry
    page = spec.pages[index - 1]
    parts: list[str] = []
    if page.cover is not None:
        dummy = canvas_for_cover or Canvas(io.BytesIO(), pagesize=spec.theme.pagesize)
        for ct in page.cover(dummy, spec.theme):
            text = md_text(ct.md_text or ct.text, reg)
            if ct.md == "h1":
                parts.append("# " + text)
            elif ct.md == "p":
                parts.append(text)
    else:
        parts.append(f'<!-- PageHeader="{md_text(spec.header_left, reg)}" -->')
        parts.append(f'<!-- PageHeader="{md_text(spec.header_right, reg)}" -->')
        for b in page.blocks:
            if isinstance(b, H):
                parts.append("#" * b.level + " " + md_text(b.text, reg))
            elif isinstance(b, P):
                parts.append(md_text(b.text, reg))
            elif isinstance(b, Bullets):
                parts.append("\n".join("- " + md_text(item, reg) for item in b.items))
            elif isinstance(b, Grid):
                lines = ["| " + " | ".join(_md_cell(h, reg) for h in b.header) + " |"]
                lines.append("| " + " | ".join("---" for _ in b.header) + " |")
                lines += ["| " + " | ".join(_md_cell(c, reg) for c in row) + " |" for row in b.rows]
                parts.append("\n".join(lines))
            elif isinstance(b, Callout):
                parts += _md_callout(b, reg)
            elif isinstance(b, TwoCol):
                for c in b.left + b.right:
                    parts += _md_callout(c, reg)
            elif isinstance(b, Status):
                parts.append(md_text(f"{b.name}: {b.status}", reg))
                parts.append(md_text(b.description, reg))
        parts.append(f'<!-- PageFooter="{md_text(spec.footer_left, reg)}" -->')
        parts.append(f'<!-- PageNumber="Page {index}" -->')
    return "\n\n".join(p for p in parts if p) + "\n"


def pages_json(spec: DocSpec) -> list[dict[str, object]]:
    return [{"page": i, "markdown": page_markdown(spec, i)} for i in range(1, len(spec.pages) + 1)]
