"""Heading-aware markdown chunking. Tables split only between rows and repeat their header in each chunk."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from knowledge.ingest.tokens import count_tokens

DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100
DEFAULT_MIN_TOKENS = 120
MIN_BODY_CHARS = 20

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_LINE = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=[\"'(\[]?[A-Z0-9])")
_LETTERS = re.compile(r"[A-Za-z0-9]")

UnitKind = Literal["heading", "text", "row"]


@dataclass(frozen=True)
class Unit:
    kind: UnitKind
    text: str
    block_id: int
    joiner: str = "\n"
    table_header: str | None = None
    heading_level: int = 0


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str
    content: str
    tokens: int


@dataclass
class ChunkResult:
    chunks: list[Chunk] = field(default_factory=list)
    empty: int = 0

    @property
    def candidates(self) -> int:
        return len(self.chunks) + self.empty


@dataclass
class _Block:
    kind: Literal["heading", "paragraph", "table", "code"]
    lines: list[str]
    level: int = 0


def parse_blocks(markdown: str) -> list[_Block]:
    blocks: list[_Block] = []
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE.match(line):
            fence = [line]
            i += 1
            while i < len(lines):
                fence.append(lines[i])
                if _FENCE.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append(_Block("code", fence))
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(_Block("heading", [m.group(2).strip()], level=len(m.group(1))))
            i += 1
            continue
        if _TABLE_LINE.match(line):
            table: list[str] = []
            while i < len(lines) and _TABLE_LINE.match(lines[i]):
                table.append(lines[i].rstrip())
                i += 1
            blocks.append(_Block("table", table))
            continue
        para: list[str] = []
        while i < len(lines) and lines[i].strip():
            if _HEADING.match(lines[i]) or _TABLE_LINE.match(lines[i]) or _FENCE.match(lines[i]):
                break
            para.append(lines[i].rstrip())
            i += 1
        blocks.append(_Block("paragraph", para))
    return blocks


def _hard_split(text: str, budget: int, counter: Callable[[str], int]) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    for word in text.split(" "):
        candidate = " ".join([*current, word])
        if current and counter(candidate) > budget:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return [p for p in pieces if p.strip()]


def _split_text(text: str, budget: int, counter: Callable[[str], int]) -> list[tuple[str, str]]:
    if counter(text) <= budget:
        return [(text, "\n")]
    out: list[tuple[str, str]] = []
    lines = text.split("\n")
    if len(lines) > 1:
        for line in lines:
            out.extend(_split_text(line, budget, counter))
        return out
    for sentence in _SENTENCE_END.split(text):
        if counter(sentence) <= budget:
            out.append((sentence, " "))
        else:
            out.extend((piece, " ") for piece in _hard_split(sentence, budget, counter))
    return out


def _units_for_block(block: _Block, block_id: int, budget: int, counter: Callable[[str], int]) -> list[Unit]:
    if block.kind == "heading":
        return [Unit("heading", block.lines[0], block_id, heading_level=block.level)]
    if block.kind == "table":
        header: str | None = None
        rows = block.lines
        if len(rows) >= 2 and _TABLE_SEPARATOR.match(rows[1]):
            header = f"{rows[0]}\n{rows[1]}"
            rows = rows[2:]
        header_tokens = counter(header) + 1 if header else 0
        units: list[Unit] = []
        for row in rows:
            if counter(row) + header_tokens <= budget:
                units.append(Unit("row", row, block_id, "\n", table_header=header))
            else:  # a single row bigger than a chunk
                units.extend(
                    Unit("row", piece, block_id, " ", table_header=header)
                    for piece in _hard_split(row, budget - header_tokens, counter)
                )
        if not rows and header:
            units.append(Unit("row", "", block_id, "\n", table_header=header))
        return units
    text = "\n".join(block.lines)
    return [Unit("text", piece, block_id, joiner) for piece, joiner in _split_text(text, budget, counter)]


def render_units(units: list[Unit]) -> str:
    parts: list[str] = []
    prev: Unit | None = None
    for unit in units:
        if unit.kind == "heading":
            parts.append(("\n\n" if parts else "") + "#" * unit.heading_level + " " + unit.text)
        elif prev is not None and prev.kind != "heading" and prev.block_id == unit.block_id:
            parts.append(unit.joiner + unit.text)
        else:
            prefix = "\n\n" if parts else ""
            if unit.kind == "row" and unit.table_header:
                parts.append(prefix + unit.table_header + ("\n" + unit.text if unit.text else ""))
            else:
                parts.append(prefix + unit.text)
        prev = unit
    return "".join(parts).strip()


def has_body(content: str) -> bool:
    body = "\n".join(
        line for line in content.split("\n") if not _HEADING.match(line) and not _TABLE_SEPARATOR.match(line)
    )
    return len(_LETTERS.findall(body)) >= MIN_BODY_CHARS


def chunk_markdown(
    markdown: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    counter: Callable[[str], int] | None = None,
) -> ChunkResult:
    if overlap_tokens >= target_tokens // 2:
        raise ValueError("overlap_tokens must be less than half of target_tokens")
    count = counter or count_tokens
    result = ChunkResult()
    path: list[tuple[int, str]] = []

    current: list[Unit] = []
    section_at_start: str | None = None
    continuation = False

    def context_heading() -> list[Unit]:
        if not path:
            return []
        level, text = path[-1]
        return [Unit("heading", text, -1, heading_level=level)]

    def content_units(units: list[Unit]) -> list[Unit]:
        return [u for u in units if u.kind != "heading"]

    def emit(units: list[Unit], section: str | None) -> None:
        while units and units[-1].kind == "heading":
            units = units[:-1]
        content = render_units(units)
        if content and has_body(content):
            result.chunks.append(Chunk(len(result.chunks), section or "", content, count(content)))
        else:
            result.empty += 1

    heading_budget = max((count("#" * lvl + " " + txt) for lvl, txt in path), default=0)

    for block_id, block in enumerate(parse_blocks(markdown)):
        if block.kind == "heading":
            while path and path[-1][0] >= block.level:
                path.pop()
            path.append((block.level, block.lines[0]))
            heading_budget = max(heading_budget, count("#" * block.level + " " + block.lines[0]))
            body = content_units(current)
            if body and (continuation or count(render_units(body)) >= min_tokens):
                emit(current, section_at_start)
                current, section_at_start, continuation = [], None, False
            if not body:
                current = []  # consecutive headings: keep only the deeper one
            unit = _units_for_block(block, block_id, target_tokens, count)[0]
            if current and count(render_units([*current, unit])) > target_tokens:
                emit(current, section_at_start)
                current, section_at_start, continuation = [], None, False
            current.append(unit)
            continue

        budget = max(32, target_tokens - heading_budget - 8)
        for unit in _units_for_block(block, block_id, budget, count):
            candidate = [*current, unit]
            if count(render_units(candidate)) <= target_tokens:
                current = candidate
                if section_at_start is None:
                    section_at_start = " > ".join(text for _, text in path)
                continue
            if content_units(current):
                emit(current, section_at_start)
            overlap: list[Unit] = []
            carried = 0
            for prev in reversed(content_units(current)):
                t = count(prev.text)
                if carried + t > overlap_tokens:
                    break
                overlap.insert(0, prev)
                carried += t
            section_at_start = " > ".join(text for _, text in path)
            continuation = True
            current = [*context_heading(), *overlap, unit]
            if count(render_units(current)) > target_tokens:
                current = [*context_heading(), unit]
            if count(render_units(current)) > target_tokens:
                current = [unit]

    if content_units(current):
        emit(current, section_at_start)
    elif not result.chunks:
        result.empty += 1
    return result
