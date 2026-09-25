"""Spike 2 (server half): scripted FastAPI endpoint emitting the AI SDK UI message stream v1 over SSE."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="s02 UI message stream spike")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["content-type"],
    expose_headers=["x-benefura-trace-id"],
)


def sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"


def last_assistant_tool_part(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        for part in reversed(message.get("parts", [])):
            if str(part.get("type", "")).startswith("tool-"):
                return part
        return None
    return None


async def script(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    part = last_assistant_tool_part(messages)
    continuing = bool(messages) and messages[-1].get("role") == "assistant"
    # A continuation keeps appending to the client's assistant message, so no new messageId.
    yield sse({"type": "start"} if continuing else {"type": "start", "messageId": f"msg_{uuid.uuid4().hex[:12]}"})
    yield sse({"type": "start-step"})

    if part is None:
        text_id = "t1"
        yield sse({"type": "text-start", "id": text_id})
        yield sse({"type": "text-delta", "id": text_id, "delta": "Checking your massage usage. "})
        yield sse({"type": "text-end", "id": text_id})
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        yield sse({"type": "tool-input-start", "toolCallId": call_id, "toolName": "get_usage"})
        yield sse({"type": "tool-input-available", "toolCallId": call_id, "toolName": "get_usage",
                   "input": {"benefitId": "massage", "memberId": None}})
    elif part.get("type") == "tool-get_usage" and part.get("state") == "output-available":
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        yield sse({"type": "tool-input-start", "toolCallId": call_id, "toolName": "draft_claim"})
        yield sse({"type": "tool-input-available", "toolCallId": call_id, "toolName": "draft_claim",
                   "input": {"benefitId": "massage", "serviceDate": "2026-09-10", "chargedCents": 9000}})
        yield sse({"type": "tool-approval-request", "approvalId": f"appr_{uuid.uuid4().hex[:8]}", "toolCallId": call_id})
    else:
        approval = part.get("approval") or {}
        approved = bool(approval.get("approved"))
        yield sse({"type": "tool-output-available", "toolCallId": part.get("toolCallId"),
                   "output": {"declined": not approved} if not approved else {"claimId": "clm_demo_1", "status": "draft"}})
        text_id = "t2"
        yield sse({"type": "text-start", "id": text_id})
        yield sse({"type": "text-delta", "id": text_id,
                   "delta": "Draft saved." if approved else "Okay, I won't create that claim."})
        yield sse({"type": "text-end", "id": text_id})

    yield sse({"type": "finish-step"})
    yield sse({"type": "finish"})
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    return StreamingResponse(
        script(body.get("messages", [])),
        media_type="text/event-stream",
        headers={
            "x-vercel-ai-ui-message-stream": "v1",
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
            "x-benefura-trace-id": uuid.uuid4().hex,
        },
    )
