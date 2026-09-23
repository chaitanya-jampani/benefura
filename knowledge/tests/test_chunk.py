from __future__ import annotations

import re

import pytest

from knowledge.ingest.chunk import chunk_markdown, parse_blocks
from knowledge.ingest.tokens import count_tokens

SENTENCE = "Waiting periods apply to hospital and extras cover before you can claim benefits for a service."


def long_paragraph(n: int, word: str = "") -> str:
    return " ".join(f"{SENTENCE} {word}{i}." for i in range(n))


def table(rows: int) -> str:
    lines = ["| Service | Waiting period | Notes |", "| --- | --- | --- |"]
    lines += [
        f"| Service number {i} with a long description | {i} months | note {i} for this row |" for i in range(rows)
    ]
    return "\n".join(lines)


def test_parse_blocks_recognises_headings_tables_paragraphs_and_code():
    md = "# Title\n\nPara one\nline two\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n```\ncode\n```\n## Next"
    kinds = [b.kind for b in parse_blocks(md)]
    assert kinds == ["heading", "paragraph", "table", "code", "heading"]


def test_chunks_respect_target_size():
    md = "# Guide\n\n" + "\n\n".join(long_paragraph(8, "p") for _ in range(6)) + "\n\n## Table\n\n" + table(150)
    result = chunk_markdown(md, target_tokens=400, overlap_tokens=60)
    assert len(result.chunks) > 3
    assert all(c.tokens <= 400 for c in result.chunks)
    assert all(c.tokens == count_tokens(c.content) for c in result.chunks)


def test_default_target_is_about_800_tokens():
    md = "# Guide\n\n" + long_paragraph(200)
    result = chunk_markdown(md)
    assert max(c.tokens for c in result.chunks) <= 800
    assert all(c.tokens >= 400 for c in result.chunks[:-1])  # chunks are filled, not fragmented


def test_sections_start_with_their_heading_and_record_the_path():
    md = (
        "# Private health\n\n"
        + long_paragraph(20, "a")
        + "\n\n## Waiting periods\n\n"
        + long_paragraph(20, "b")
        + "\n\n### Pre-existing conditions\n\n"
        + long_paragraph(20, "c")
    )
    result = chunk_markdown(md, target_tokens=300, overlap_tokens=40)
    sections = [c.section for c in result.chunks]
    assert sections[0] == "Private health"
    assert "Private health > Waiting periods" in sections
    assert "Private health > Waiting periods > Pre-existing conditions" in sections
    for c in result.chunks:
        assert c.content.startswith("#"), c.content[:40]
        assert c.content.split("\n", 1)[0].lstrip("# ") == c.section.split(" > ")[-1]


def test_large_sections_do_not_cross_heading_boundaries():
    md = "## Alpha\n\n" + long_paragraph(15, "alpha") + "\n\n## Beta\n\n" + long_paragraph(15, "beta")
    result = chunk_markdown(md, target_tokens=300, overlap_tokens=40)
    for c in result.chunks:
        assert not ("alpha" in c.content and "beta" in c.content)


def test_tiny_sections_are_merged():
    md = "## One\n\nShort intro about coverage.\n\n## Two\n\nAnother short note about claims.\n\n## Three\n\nThird."
    result = chunk_markdown(md, target_tokens=400, overlap_tokens=40, min_tokens=120)
    assert len(result.chunks) == 1
    assert result.chunks[0].section == "One"
    assert "## Two" in result.chunks[0].content and "## Three" in result.chunks[0].content


def test_overlap_repeats_trailing_text_within_a_section():
    md = "## Section\n\n" + "\n\n".join(f"Paragraph {i}. {SENTENCE}" for i in range(40))
    result = chunk_markdown(md, target_tokens=250, overlap_tokens=60)
    assert len(result.chunks) >= 3
    for prev, nxt in zip(result.chunks, result.chunks[1:], strict=False):
        prev_paras = prev.content.split("\n\n")[1:]  # drop the heading
        next_paras = nxt.content.split("\n\n")[1:]
        k = max(n for n in range(len(next_paras) + 1) if next_paras[:n] == prev_paras[len(prev_paras) - n :])
        assert k >= 1, "next chunk should start with the previous chunk's trailing paragraphs"
        assert sum(count_tokens(p) for p in next_paras[:k]) <= 60
        assert next_paras[k:], "overlap must never be the whole next chunk"


def test_zero_overlap_does_not_repeat_text():
    md = "## Section\n\n" + "\n\n".join(f"Paragraph {i}. {SENTENCE}" for i in range(40))
    result = chunk_markdown(md, target_tokens=250, overlap_tokens=0)
    seen: set[str] = set()
    for c in result.chunks:
        paras = set(re.findall(r"Paragraph \d+\.", c.content))
        assert not paras & seen
        seen |= paras


def test_tables_are_never_split_mid_row_and_repeat_the_header():
    md = "## Waiting periods\n\n" + table(120)
    source_rows = set(table(120).split("\n")[2:])
    result = chunk_markdown(md, target_tokens=300, overlap_tokens=50)
    assert len(result.chunks) > 2
    covered: set[str] = set()
    for c in result.chunks:
        lines = [ln for ln in c.content.split("\n") if ln.startswith("|")]
        assert lines[0] == "| Service | Waiting period | Notes |"
        assert lines[1] == "| --- | --- | --- |"
        for row in lines[2:]:
            assert row in source_rows, f"partial or altered row: {row}"
            covered.add(row)
    assert covered == source_rows


def test_overly_long_sentence_is_hard_split_within_bounds():
    md = "## Big\n\n" + " ".join(["word"] * 3000)
    result = chunk_markdown(md, target_tokens=200, overlap_tokens=20)
    assert all(c.tokens <= 200 for c in result.chunks)
    assert sum(c.content.count("word") for c in result.chunks) >= 3000


def test_heading_only_document_counts_as_empty():
    result = chunk_markdown("# Title\n\n## Subtitle\n\n### Nothing here")
    assert result.chunks == []
    assert result.empty == 1
    assert result.candidates == 1


def test_junk_only_chunk_is_counted_empty():
    result = chunk_markdown("## Menu\n\n| | |\n| --- | --- |\n| - | - |")
    assert result.chunks == []
    assert result.empty == 1


def test_invalid_overlap_rejected():
    with pytest.raises(ValueError):
        chunk_markdown("text", target_tokens=100, overlap_tokens=60)
