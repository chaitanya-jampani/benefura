"""Embeddings in token-bounded batches. Title and section are prepended so short chunks keep page context."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Protocol

from knowledge.ingest.tokens import count_tokens

EMBEDDING_DIMENSIONS = 512
MAX_BATCH_ITEMS = 64
MAX_BATCH_TOKENS = 60_000
MAX_INPUT_TOKENS = 8_000


def embedding_text(title: str, section: str, content: str) -> str:
    header = f"{title} — {section}" if section else title
    return f"{header}\n\n{content}"


def batches(
    texts: Sequence[str], *, max_items: int = MAX_BATCH_ITEMS, max_tokens: int = MAX_BATCH_TOKENS
) -> list[list[int]]:
    out: list[list[int]] = []
    current: list[int] = []
    tokens = 0
    for i, text in enumerate(texts):
        t = min(count_tokens(text), MAX_INPUT_TOKENS)
        if current and (len(current) >= max_items or tokens + t > max_tokens):
            out.append(current)
            current, tokens = [], 0
        current.append(i)
        tokens += t
    if current:
        out.append(current)
    return out


class Embedder(Protocol):
    name: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FoundryEmbedder:
    name = "text-embedding-3-small"

    def __init__(self, model: str | None = None, dimensions: int | None = None) -> None:
        from app.config import get_settings

        settings = get_settings()
        self._model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        if self.dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(f"index expects {EMBEDDING_DIMENSIONS} dimensions, settings say {self.dimensions}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from app.services.foundry import get_openai_client

        client = get_openai_client()
        vectors: list[list[float]] = [[] for _ in texts]
        for batch in batches(texts):
            # M0-verify: embeddings through the project's OpenAI-compatible endpoint with a deployment name.
            res = await client.embeddings.create(
                model=self._model, input=[texts[i] for i in batch], dimensions=self.dimensions
            )
            for item in res.data:
                vectors[batch[item.index]] = list(item.embedding)
        if any(len(v) != self.dimensions for v in vectors):
            raise RuntimeError("embedding response missing vectors or wrong dimensions")
        return vectors


class FakeEmbedder:
    """Deterministic hash vectors for tests; not semantic."""

    name = "fake-hash"
    dimensions = EMBEDDING_DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        raw = b""
        counter = 0
        while len(raw) < self.dimensions * 4:
            raw += hashlib.sha256(f"{counter}:{text}".encode()).digest()
            counter += 1
        values = [v / 2**32 - 0.5 for v in struct.unpack(f"<{self.dimensions}I", raw[: self.dimensions * 4])]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
