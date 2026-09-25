"""API client and UI message stream assembler, built to send requests exactly as ``transport.ts`` does."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

JSON = dict[str, Any]
NAMESPACE_HEADER = "x-benefura-budget-namespace"
EVALS_KEY_HEADER = "x-benefura-evals-key"
TRACE_HEADER = "x-benefura-trace-id"
DEFAULT_API_URL = "http://localhost:8000"


def api_url_from_env() -> str:
    return os.environ.get("BENEFURA_API_URL", DEFAULT_API_URL).rstrip("/")


def evals_headers(secret: str | None = None) -> dict[str, str]:
    key = secret if secret is not None else os.environ.get("EVALS_SHARED_SECRET", "")
    headers = {NAMESPACE_HEADER: "evals"}
    if key:
        headers[EVALS_KEY_HEADER] = key
    return headers


@dataclass
class ToolCall:
    tool_call_id: str
    name: str
    input: Any
    provider_executed: bool = False


@dataclass
class StreamResult:
    status: int
    message: JSON | None = None
    trace_id: str | None = None
    texts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    tool_errors: dict[str, str] = field(default_factory=dict)
    denied: list[str] = field(default_factory=list)
    approval_requests: dict[str, str] = field(default_factory=dict)  # toolCallId -> approvalId
    data_chunks: list[JSON] = field(default_factory=list)  # transient ones included
    finish_reason: str | None = None
    error_text: str | None = None
    error_body: JSON | None = None
    chunks: list[JSON] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(t for t in self.texts if t)

    @property
    def metadata(self) -> JSON:
        return (self.message or {}).get("metadata") or {}

    @property
    def active_agent(self) -> str | None:
        agent = self.metadata.get("agent")
        return agent if isinstance(agent, str) else None

    def data(self, name: str) -> list[Any]:
        return [c.get("data") for c in self.data_chunks if c.get("type") == f"data-{name}"]

    @property
    def citations(self) -> list[JSON]:
        found: list[JSON] = []
        for payload in self.data("citations"):
            items = payload.get("citations") if isinstance(payload, dict) else payload
            found.extend(c for c in items or [] if isinstance(c, dict))
        return found


def new_assistant_message(message_id: str) -> JSON:
    return {"id": message_id, "role": "assistant", "parts": [], "metadata": {}}


class UIStreamAssembler:
    """Subset of ai@7 ``processUIMessageStream``."""

    def __init__(self, result: StreamResult, message: JSON, *, continued: bool = False) -> None:
        self.r = result
        self.message = message
        self.continued = continued
        message.setdefault("parts", [])
        message.setdefault("metadata", {})
        self._texts: dict[str, JSON] = {}
        self._tools: dict[str, JSON] = {
            p["toolCallId"]: p for p in message["parts"] if isinstance(p, dict) and "toolCallId" in p
        }
        result.message = message

    @property
    def parts(self) -> list[JSON]:
        return self.message["parts"]

    def _tool(self, chunk: JSON) -> JSON:
        call_id = chunk["toolCallId"]
        part = self._tools.get(call_id)
        if part is None:
            part = {
                "type": f"tool-{chunk.get('toolName', 'unknown')}",
                "toolCallId": call_id,
                "state": "input-streaming",
            }
            if chunk.get("providerExecuted"):
                part["providerExecuted"] = True
            self._tools[call_id] = part
            self.parts.append(part)
        return part

    def feed(self, chunk: JSON) -> None:
        self.r.chunks.append(chunk)
        kind = str(chunk.get("type", ""))
        if kind in ("start", "finish", "message-metadata"):
            if kind == "start" and chunk.get("messageId") and not self.continued:
                self.message["id"] = chunk["messageId"]
            if isinstance(chunk.get("messageMetadata"), dict):
                self.message["metadata"] = {**self.message["metadata"], **chunk["messageMetadata"]}
            if kind == "finish":
                self.r.finish_reason = chunk.get("finishReason") or "stop"
        elif kind == "start-step":
            self.parts.append({"type": "step-start"})
        elif kind == "text-start":
            part = {"type": "text", "text": "", "state": "streaming"}
            self._texts[chunk["id"]] = part
            self.parts.append(part)
        elif kind == "text-delta":
            part = self._texts.get(chunk["id"])
            if part is None:
                part = {"type": "text", "text": "", "state": "streaming"}
                self._texts[chunk["id"]] = part
                self.parts.append(part)
            part["text"] += chunk.get("delta", "")
        elif kind == "text-end":
            part = self._texts.get(chunk["id"])
            if part is not None:
                part["state"] = "done"
                self.r.texts.append(part["text"])
        elif kind == "tool-input-start":
            self._tool(chunk)
        elif kind in ("tool-input-available", "tool-input-error"):
            part = self._tool(chunk)
            part["input"] = chunk.get("input")
            if kind == "tool-input-available":
                part["state"] = "input-available"
            else:
                part["state"] = "output-error"
                part["errorText"] = chunk.get("errorText", "")
            self.r.tool_calls.append(
                ToolCall(
                    chunk["toolCallId"], chunk["toolName"], chunk.get("input"), bool(chunk.get("providerExecuted"))
                )
            )
        elif kind == "tool-approval-request":
            part = self._tools.get(chunk["toolCallId"])
            self.r.approval_requests[chunk["toolCallId"]] = chunk["approvalId"]
            if part is not None:
                part["state"] = "approval-requested"
                part["approval"] = {"id": chunk["approvalId"]}
        elif kind == "tool-output-available":
            part = self._tools.get(chunk["toolCallId"])
            self.r.tool_outputs[chunk["toolCallId"]] = chunk.get("output")
            if part is not None:
                part["state"] = "output-available"
                part["output"] = chunk.get("output")
        elif kind == "tool-output-error":
            part = self._tools.get(chunk["toolCallId"])
            self.r.tool_errors[chunk["toolCallId"]] = chunk.get("errorText", "")
            if part is not None:
                part["state"] = "output-error"
                part["errorText"] = chunk.get("errorText", "")
        elif kind == "tool-output-denied":
            part = self._tools.get(chunk["toolCallId"])
            self.r.denied.append(chunk["toolCallId"])
            if part is not None:
                part["state"] = "output-denied"
        elif kind.startswith("data-"):
            self.r.data_chunks.append(chunk)
            if chunk.get("transient"):
                return
            existing = next(
                (p for p in self.parts if p.get("type") == kind and chunk.get("id") and p.get("id") == chunk.get("id")),
                None,
            )
            if existing is not None:
                existing["data"] = chunk.get("data")
            else:
                self.parts.append(
                    {"type": kind, **({"id": chunk["id"]} if chunk.get("id") else {}), "data": chunk.get("data")}
                )
        elif kind == "error":
            self.r.error_text = chunk.get("errorText", "error")
        elif kind == "abort":
            self.r.finish_reason = "abort"


def parse_sse_lines(lines: list[str]) -> list[JSON]:
    chunks: list[JSON] = []
    buffer: list[str] = []

    def flush() -> bool:
        if not buffer:
            return False
        payload = "\n".join(buffer)
        buffer.clear()
        if payload.strip() == "[DONE]":
            return True
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return False
        if isinstance(value, dict):
            chunks.append(value)
        return False

    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if flush():
                return chunks
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffer.append(line[6:] if line.startswith("data: ") else line[5:])
    flush()
    return chunks


def last_agent(messages: list[JSON]) -> str | None:
    """Mirrors ``lastAgent`` in transport.ts."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return None
        agent = (message.get("metadata") or {}).get("agent")
        if message.get("role") == "assistant" and agent:
            return agent
    return None


def outgoing_messages(messages: list[JSON]) -> list[JSON]:
    """Mirrors ``outgoingMessages`` in transport.ts."""
    return [
        {**m, "parts": [p for p in m.get("parts", []) if not str(p.get("type", "")).startswith("data-")]}
        for m in messages
    ]


def chat_body(messages: list[JSON], context: JSON) -> JSON:
    return {"messages": outgoing_messages(messages), "context": context, "activeAgent": last_agent(messages)}


class BenefuraClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        evals_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 240.0,
    ) -> None:
        self.base_url = (base_url or api_url_from_env()).rstrip("/")
        self._headers = evals_headers(evals_secret)
        self._client = httpx.AsyncClient(base_url=self.base_url, transport=transport, timeout=timeout_s)

    async def close(self) -> None:
        await self._client.aclose()

    async def healthz(self) -> JSON:
        res = await self._client.get("/healthz")
        res.raise_for_status()
        return res.json()

    async def analyze_chunk(
        self, document_id: str, region: str, pages: list[int], filename: str, pdf: bytes
    ) -> httpx.Response:
        return await self._client.post(
            "/api/plan/analyze-chunk",
            data={"documentId": document_id, "region": region, "pages": ",".join(map(str, pages))},
            files={"file": (filename, pdf, "application/pdf")},
            headers=self._headers,
        )

    async def chat(self, body: JSON, continue_message: JSON | None, new_message_id: str) -> StreamResult:
        headers = {**self._headers, "accept": "text/event-stream", "content-type": "application/json"}
        result = StreamResult(status=0)
        async with self._client.stream("POST", "/api/chat", json=body, headers=headers) as res:
            result.status = res.status_code
            result.trace_id = res.headers.get(TRACE_HEADER)
            if res.status_code != 200:
                raw = await res.aread()
                try:
                    result.error_body = json.loads(raw)
                except ValueError:
                    result.error_body = {"error": {"code": "unknown", "message": raw[:200].decode(errors="replace")}}
                return result
            lines = [line async for line in res.aiter_lines()]
        message = continue_message if continue_message is not None else new_assistant_message(new_message_id)
        assembler = UIStreamAssembler(result, message, continued=continue_message is not None)
        for chunk in parse_sse_lines(lines):
            assembler.feed(chunk)
        return result
