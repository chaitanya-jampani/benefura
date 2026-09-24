"""AI SDK ``UIMessage[]`` → Responses API input; nothing is stored server-side, so every request replays."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from app.chat.server_tools import replay_server_tool_output
from app.errors import ApiError
from app.models.api import AgentName

MAX_REPLAYED_OUTPUT_CHARS = 12_000
AGENT_NAMES: tuple[AgentName, ...] = ("plan_claims", "knowledge")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def call_signature(name: str, arguments: Any) -> str:
    """Identity of a call for the "declined approvals are not retried" rule."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    return f"{name}:{canonical_json(arguments)}"


@dataclass
class ToolPart:
    tool_call_id: str
    name: str
    state: str
    input: Any
    output: Any = None
    error_text: str | None = None
    approval: dict[str, Any] | None = None
    provider_executed: bool = False

    @property
    def declined(self) -> bool:
        if self.state == "output-denied":
            return True
        return self.state == "approval-responded" and bool(self.approval) and self.approval.get("approved") is False

    @property
    def decline_reason(self) -> str | None:
        reason = (self.approval or {}).get("reason")
        return reason if isinstance(reason, str) and reason.strip() else None


@dataclass
class ReplayedHistory:
    items: list[dict[str, Any]] = field(default_factory=list)
    latest_user_text: str | None = None
    is_continuation: bool = False
    tool_steps_used: int = 0
    declined_signatures: set[str] = field(default_factory=set)
    # Declined approvals the browser has not yet seen as ``output-denied``.
    newly_denied_call_ids: list[str] = field(default_factory=list)
    last_assistant_id: str | None = None
    last_agent: AgentName | None = None


def _parts(message: dict[str, Any]) -> list[Any]:
    parts = message.get("parts")
    return parts if isinstance(parts, list) else []


def _text_of(parts: list[Any]) -> str:
    return "\n".join(
        p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
    ).strip()


def parse_tool_part(part: dict[str, Any]) -> ToolPart | None:
    kind = part.get("type")
    if not isinstance(kind, str):
        return None
    if kind == "dynamic-tool":
        name = part.get("toolName")
    elif kind.startswith("tool-"):
        name = kind[len("tool-") :]
    else:
        return None
    call_id = part.get("toolCallId")
    if not isinstance(name, str) or not isinstance(call_id, str):
        return None
    approval = part.get("approval")
    return ToolPart(
        tool_call_id=call_id,
        name=name,
        state=str(part.get("state", "")),
        input=part.get("input"),
        output=part.get("output"),
        error_text=part.get("errorText") if isinstance(part.get("errorText"), str) else None,
        approval=cast(dict[str, Any], approval) if isinstance(approval, dict) else None,
        provider_executed=bool(part.get("providerExecuted")),
    )


def tool_output_for_model(tool: ToolPart) -> Any:
    if tool.declined:
        return {"declined": True, "reason": tool.decline_reason}
    if tool.state == "output-available":
        return tool.output if isinstance(tool.output, dict | list | str) else {"result": tool.output}
    if tool.state == "output-error":
        return {"error": tool.error_text or "The tool failed."}
    if tool.state == "approval-requested":
        return {"declined": True, "reason": "The user did not respond to the approval request."}
    return {"error": "not_executed", "message": "The tool did not run."}


def _serialize_output(tool: ToolPart) -> str:
    payload = tool_output_for_model(tool)
    if tool.state == "output-available" and tool.provider_executed:
        replayed = replay_server_tool_output(tool.name, payload)
        if replayed is not None:
            payload = replayed
    text = payload if isinstance(payload, str) else canonical_json(payload)
    if len(text) > MAX_REPLAYED_OUTPUT_CHARS:
        text = text[:MAX_REPLAYED_OUTPUT_CHARS] + "…[truncated]"
    return text


def _nested_steps(tool: ToolPart) -> int:
    if tool.state == "output-available" and isinstance(tool.output, dict):
        nested = tool.output.get("nestedToolCalls")
        if isinstance(nested, int) and nested > 0:
            return nested
    return 0


def _blocked(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("blocked"))


def replay(messages: list[dict[str, Any]]) -> ReplayedHistory:
    usable = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not usable:
        raise ApiError("invalid_request", "The conversation is empty.")

    # Blocked turns (hard-tier PII, content filter) are dropped so their text never reaches a model later.
    skip: set[int] = set()
    for i, message in enumerate(usable):
        if message["role"] == "assistant" and _blocked(message):
            skip.add(i)
            if i > 0 and usable[i - 1]["role"] == "user":
                skip.add(i - 1)

    last_user_index = max((i for i, m in enumerate(usable) if m["role"] == "user"), default=-1)
    result = ReplayedHistory(is_continuation=usable[-1]["role"] == "assistant")

    for i, message in enumerate(usable):
        parts = _parts(message)
        if i in skip:
            continue
        if message["role"] == "user":
            content = _text_of(parts)
            if content:
                result.items.append({"type": "message", "role": "user", "content": content})
            continue

        in_current_turn = i > last_user_index
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                content = part.get("text")
                if isinstance(content, str) and content.strip():
                    result.items.append({"type": "message", "role": "assistant", "content": content})
                continue
            tool = parse_tool_part(part)
            if tool is None or tool.state == "input-streaming":
                continue
            arguments = tool.input if tool.input is not None else {}
            result.items.append(
                {
                    "type": "function_call",
                    "call_id": tool.tool_call_id,
                    "name": tool.name,
                    "arguments": canonical_json(arguments),
                }
            )
            result.items.append(
                {"type": "function_call_output", "call_id": tool.tool_call_id, "output": _serialize_output(tool)}
            )
            if in_current_turn:
                result.tool_steps_used += 1 + _nested_steps(tool)
                if tool.declined:
                    result.declined_signatures.add(call_signature(tool.name, arguments))
                    if tool.state == "approval-responded":
                        result.newly_denied_call_ids.append(tool.tool_call_id)

    last = usable[-1]
    if last["role"] == "user":
        if last_user_index in skip:
            result.latest_user_text = None
        else:
            result.latest_user_text = _text_of(_parts(last))
    else:
        result.last_assistant_id = last.get("id") if isinstance(last.get("id"), str) else None
        metadata = last.get("metadata")
        agent = metadata.get("agent") if isinstance(metadata, dict) else None
        result.last_agent = agent if agent in AGENT_NAMES else None
    return result
