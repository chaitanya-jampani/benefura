"""AI SDK 7 UI message stream (v1) over SSE; chunks mirror ``uiMessageChunkSchema`` in ``ai@7.0.105``."""

from __future__ import annotations

import json
from typing import Any, Literal

Chunk = dict[str, Any]

FinishReason = Literal["stop", "length", "content-filter", "tool-calls", "error", "other"]

# Mirrors ``UI_MESSAGE_STREAM_HEADERS`` in the ai package.
UI_MESSAGE_STREAM_HEADERS: dict[str, str] = {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "x-accel-buffering": "no",
}

DONE_FRAME = "data: [DONE]\n\n"


def encode(chunk: Chunk) -> str:
    """Byte-compatible with ``JSON.stringify``."""
    return f"data: {json.dumps(chunk, separators=(',', ':'), ensure_ascii=False)}\n\n"


def _opt(chunk: Chunk, **optional: Any) -> Chunk:
    # The SDK's zod schema rejects null for optional keys, so they are omitted instead.
    for key, value in optional.items():
        if value is not None:
            chunk[key] = value
    return chunk


def start(message_id: str | None = None, metadata: dict[str, Any] | None = None) -> Chunk:
    return _opt({"type": "start"}, messageId=message_id, messageMetadata=metadata)


def finish(reason: FinishReason | None = None, metadata: dict[str, Any] | None = None) -> Chunk:
    return _opt({"type": "finish"}, finishReason=reason, messageMetadata=metadata)


def message_metadata(metadata: dict[str, Any]) -> Chunk:
    return {"type": "message-metadata", "messageMetadata": metadata}


def start_step() -> Chunk:
    return {"type": "start-step"}


def finish_step() -> Chunk:
    return {"type": "finish-step"}


def error(error_text: str) -> Chunk:
    return {"type": "error", "errorText": error_text}


def text_start(part_id: str) -> Chunk:
    return {"type": "text-start", "id": part_id}


def text_delta(part_id: str, delta: str) -> Chunk:
    return {"type": "text-delta", "id": part_id, "delta": delta}


def text_end(part_id: str) -> Chunk:
    return {"type": "text-end", "id": part_id}


def text(part_id: str, content: str) -> list[Chunk]:
    return [text_start(part_id), text_delta(part_id, content), text_end(part_id)]


def tool_input_start(tool_call_id: str, tool_name: str, *, provider_executed: bool | None = None) -> Chunk:
    return _opt(
        {"type": "tool-input-start", "toolCallId": tool_call_id, "toolName": tool_name},
        providerExecuted=provider_executed,
    )


def tool_input_delta(tool_call_id: str, input_text_delta: str) -> Chunk:
    return {"type": "tool-input-delta", "toolCallId": tool_call_id, "inputTextDelta": input_text_delta}


def tool_input_available(
    tool_call_id: str, tool_name: str, tool_input: Any, *, provider_executed: bool | None = None
) -> Chunk:
    return _opt(
        {"type": "tool-input-available", "toolCallId": tool_call_id, "toolName": tool_name, "input": tool_input},
        providerExecuted=provider_executed,
    )


def tool_input_error(
    tool_call_id: str, tool_name: str, tool_input: Any, error_text: str, *, provider_executed: bool | None = None
) -> Chunk:
    return _opt(
        {
            "type": "tool-input-error",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "input": tool_input,
            "errorText": error_text,
        },
        providerExecuted=provider_executed,
    )


def tool_approval_request(approval_id: str, tool_call_id: str, *, reason: str | None = None) -> Chunk:
    return _opt(
        {"type": "tool-approval-request", "approvalId": approval_id, "toolCallId": tool_call_id},
        reason=reason,
    )


def tool_output_available(tool_call_id: str, output: Any, *, provider_executed: bool | None = None) -> Chunk:
    return _opt(
        {"type": "tool-output-available", "toolCallId": tool_call_id, "output": output},
        providerExecuted=provider_executed,
    )


def tool_output_error(tool_call_id: str, error_text: str, *, provider_executed: bool | None = None) -> Chunk:
    return _opt(
        {"type": "tool-output-error", "toolCallId": tool_call_id, "errorText": error_text},
        providerExecuted=provider_executed,
    )


def tool_output_denied(tool_call_id: str) -> Chunk:
    return {"type": "tool-output-denied", "toolCallId": tool_call_id}


def data(name: str, payload: Any, *, part_id: str | None = None, transient: bool | None = None) -> Chunk:
    """Parts with the same ``id`` replace each other in the browser."""
    return _opt({"type": f"data-{name}", "data": payload}, id=part_id, transient=transient)


def encode_all(chunks: list[Chunk], *, done: bool = True) -> str:
    body = "".join(encode(c) for c in chunks)
    return body + DONE_FRAME if done else body
