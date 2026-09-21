"""Prompt Shields; a document over the per-call limit is split and flagged if any piece is."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.fake_hooks import current_hooks
from app.services.language_pii import split_text, text_records

# M0-verify: Prompt Shields input limits (documents per call and cumulative characters).
MAX_DOCS_PER_CALL = 5
MAX_CHARS_PER_CALL = 10_000


@dataclass
class ShieldResult:
    document_attacks: list[bool] = field(default_factory=list)
    user_prompt_attack: bool = False
    records: int = 0


@dataclass(frozen=True)
class _Piece:
    doc_index: int
    text: str


def plan_batches(documents: list[str]) -> list[list[_Piece]]:
    batches: list[list[_Piece]] = []
    current: list[_Piece] = []
    chars = 0
    for index, document in enumerate(documents):
        for _, piece in split_text(document, MAX_CHARS_PER_CALL):
            if not piece.strip():
                continue
            if current and (len(current) >= MAX_DOCS_PER_CALL or chars + len(piece) > MAX_CHARS_PER_CALL):
                batches.append(current)
                current, chars = [], 0
            current.append(_Piece(index, piece))
            chars += len(piece)
    if current:
        batches.append(current)
    return batches


def parse_response(body: dict[str, Any], batch: list[_Piece], result: ShieldResult) -> None:
    analyses = body.get("documentsAnalysis") or []
    for piece, analysis in zip(batch, analyses, strict=False):
        if analysis.get("attackDetected"):
            result.document_attacks[piece.doc_index] = True
    if (body.get("userPromptAnalysis") or {}).get("attackDetected"):
        result.user_prompt_attack = True


async def shield_documents(
    documents: list[str], user_prompt: str | None = None, *, page_numbers: list[int] | None = None
) -> ShieldResult:
    """``page_numbers`` is only read by the fake-mode injection hook."""
    settings = get_settings()
    result = ShieldResult(document_attacks=[False] * len(documents))
    if settings.ai_mode != "live":
        pages = page_numbers or list(range(1, len(documents) + 1))
        flagged = current_hooks().injection_pages
        result.document_attacks = [page in flagged for page in pages]
        result.user_prompt_attack = bool(user_prompt) and 0 in flagged
        result.records = sum(text_records(d) for d in documents) + text_records(user_prompt or "")
        return result

    from app.services.rest import post_json

    path = f"contentsafety/text:shieldPrompt?api-version={settings.content_safety_api_version}"
    prompt_pieces = [piece for _, piece in split_text(user_prompt or "", MAX_CHARS_PER_CALL) if piece.strip()]
    batches = plan_batches(documents)
    calls = max(len(batches), len(prompt_pieces))
    for call in range(calls):
        batch = batches[call] if call < len(batches) else []
        payload: dict[str, Any] = {"documents": [p.text for p in batch]}
        if call < len(prompt_pieces):
            payload["userPrompt"] = prompt_pieces[call]  # M0-verify: userPrompt may be omitted for documents-only
        body = await post_json(path, payload, service="Prompt Shields")
        parse_response(body, batch, result)
        result.records += sum(text_records(p.text) for p in batch)
        if call < len(prompt_pieces):
            result.records += text_records(prompt_pieces[call])
    return result
