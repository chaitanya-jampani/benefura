"""``sources.yaml`` manifest and the licence filter, kept in one place so the pipeline cannot bypass it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from knowledge.ingest.chunk import Chunk
from knowledge.ingest.tokens import count_tokens

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = KNOWLEDGE_DIR / "sources.yaml"

EXCERPT_MAX_TOKENS = 300
EXCERPT_MAX_CHUNKS = 20

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_SENTENCE_END = re.compile(r"((?<=[.!?])[ \t]+|\n+)")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    url: HttpUrl
    region: Literal["CA", "AU"]
    title: str = Field(min_length=3)
    publisher: str = Field(min_length=2)
    licence: str = Field(min_length=2)
    licence_url: HttpUrl | None = None
    licence_note: str = Field(min_length=10, description="The licence/terms text relied on, quoted.")
    attribution: str = Field(min_length=5)
    reproduction: Literal["verbatim", "excerpt"]
    index: bool
    format: Literal["html", "pdf"] = "html"
    topics: list[str] = Field(default_factory=list, description="Editorial topic hints merged with enrichment.")
    fixture: str | None = Field(default=None, description="Offline test fixture file name (tests only).")

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id must be kebab-case (a-z, 0-9, '-'), 3-80 chars")
        return v

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme != "https":
            raise ValueError("source urls must be https")
        return v

    @model_validator(mode="after")
    def _link_only_is_consistent(self) -> Source:
        if not self.index and self.fixture:
            raise ValueError("link-only sources (index: false) cannot have fixtures")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    sources: list[Source]

    @model_validator(mode="after")
    def _unique(self) -> Manifest:
        ids = [s.id for s in self.sources]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate source ids: {sorted(dupes)}")
        urls = [str(s.url) for s in self.sources]
        dupe_urls = {u for u in urls if urls.count(u) > 1}
        if dupe_urls:
            raise ValueError(f"duplicate source urls: {sorted(dupe_urls)}")
        return self


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Manifest:
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))


def select_sources(manifest: Manifest, only: Iterable[str] | None = None) -> tuple[list[Source], list[Source]]:
    wanted = list(only or [])
    known = {s.id for s in manifest.sources}
    missing = [i for i in wanted if i not in known]
    if missing:
        raise KeyError(f"unknown source ids: {missing}")
    chosen = [s for s in manifest.sources if not wanted or s.id in wanted]
    return [s for s in chosen if s.index], [s for s in chosen if not s.index]


def bounded_excerpt(text: str, max_tokens: int = EXCERPT_MAX_TOKENS) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    kept = ""
    # Keep the separators so tables and lists stay intact.
    pieces = _SENTENCE_END.split(text)
    for i in range(0, len(pieces), 2):
        candidate = kept + pieces[i]
        if count_tokens(candidate.rstrip() + " …") > max_tokens:
            break
        kept = candidate + (pieces[i + 1] if i + 1 < len(pieces) else "")
    kept = kept.rstrip()
    if not kept:  # first sentence too long: fall back to words
        for word in text.split():
            candidate = f"{kept} {word}".strip()
            if count_tokens(candidate + " …") > max_tokens:
                break
            kept = candidate
    return kept + " …"


def apply_licence(source: Source, chunks: list[Chunk]) -> list[Chunk]:
    if not source.index:
        return []
    if source.reproduction == "verbatim":
        return chunks
    limited: list[Chunk] = []
    for chunk in chunks[:EXCERPT_MAX_CHUNKS]:
        excerpt = bounded_excerpt(chunk.content)
        limited.append(Chunk(chunk.index, chunk.section, excerpt, count_tokens(excerpt)))
    return limited
