"""Drives one chat conversation like ``apps/web/src/agent/ChatPanel.tsx`` does, with the fake browser tools."""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evals.harness.browser_tools import APPROVAL_TOOLS, BROWSER_TOOLS, BrowserToolExecutor, ToolError
from evals.harness.client import BenefuraClient, StreamResult, chat_body

JSON = dict[str, Any]
MAX_ROUNDS_PER_TURN = 8
DEFAULT_DECLINE_REASON = "Not approved in this evaluation scenario."

# (tool name, tool input) -> (approved, reason)
ApprovalPolicy = Callable[[str, Any], tuple[bool, str | None]]


def decline_all(_name: str, _input: Any) -> tuple[bool, str | None]:
    return False, DEFAULT_DECLINE_REASON


@dataclass
class TurnLog:
    rounds: int = 0
    texts: list[str] = field(default_factory=list)
    tool_calls: list[JSON] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    approvals: list[JSON] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    data_types: set[str] = field(default_factory=set)
    citations: list[JSON] = field(default_factory=list)
    requests: list[JSON] = field(default_factory=list)
    responses: list[Any] = field(default_factory=list)
    finish_reasons: list[str | None] = field(default_factory=list)
    agent: str | None = None
    blocked: str | None = None
    denied: list[str] = field(default_factory=list)


def tool_call_record(tool_call_id: str, name: str, arguments: Any) -> JSON:
    """Foundry agent-evaluator ``tool_call`` content item."""
    return {"type": "tool_call", "tool_call_id": tool_call_id, "name": name, "arguments": arguments or {}}


class Conversation:
    def __init__(
        self,
        client: BenefuraClient,
        context: JSON,
        executor: BrowserToolExecutor,
        approvals: ApprovalPolicy = decline_all,
    ) -> None:
        self.client = client
        self.context = context
        self.executor = executor
        self.approvals = approvals
        self.messages: list[JSON] = []
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    def answer_browser_tools(self, message: JSON, result: StreamResult, log: TurnLog, policy: ApprovalPolicy) -> bool:
        """True when a continuation is needed."""
        pending = [
            p
            for p in message["parts"]
            if str(p.get("type", "")).startswith("tool-")
            and not p.get("providerExecuted")
            and p.get("state") in ("input-available", "approval-requested")
            and p["type"][len("tool-") :] in BROWSER_TOOLS
        ]
        for part in pending:
            name = part["type"][len("tool-") :]
            needs_approval = part["state"] == "approval-requested" or name in APPROVAL_TOOLS
            approval_id = (part.get("approval") or {}).get("id") or result.approval_requests.get(part["toolCallId"])
            if needs_approval:
                approved, reason = policy(name, part.get("input"))
                log.approvals.append({"tool": name, "decision": "approve" if approved else "decline"})
                if not approved:
                    part["state"] = "approval-responded"
                    part["approval"] = {
                        "id": approval_id or part["toolCallId"],
                        "approved": False,
                        "reason": reason or DEFAULT_DECLINE_REASON,
                    }
                    log.outputs[part["toolCallId"]] = {"declined": True, "reason": reason or DEFAULT_DECLINE_REASON}
                    continue
                part["approval"] = {"id": approval_id or part["toolCallId"], "approved": True}
            try:
                output = self.executor.execute(name, part.get("input"))
            except ToolError as exc:
                part["state"] = "output-error"
                part["errorText"] = str(exc)
                log.outputs[part["toolCallId"]] = {"error": str(exc)}
            else:
                part["state"] = "output-available"
                part["output"] = output
                log.outputs[part["toolCallId"]] = output
        return bool(pending)

    async def send(self, text: str, approvals: ApprovalPolicy | None = None) -> TurnLog:
        policy = approvals or self.approvals
        log = TurnLog()
        self.messages.append({"id": self._id("user"), "role": "user", "parts": [{"type": "text", "text": text}]})
        for _ in range(MAX_ROUNDS_PER_TURN):
            log.rounds += 1
            body = chat_body(self.messages, self.context)
            log.requests.append(copy.deepcopy(body))
            continuing = self.messages[-1] if self.messages[-1]["role"] == "assistant" else None
            result = await self.client.chat(body, continuing, self._id("assistant"))
            log.responses.append(result.chunks or result.error_body)
            if result.trace_id:
                log.trace_ids.append(result.trace_id)
            if result.status != 200:
                code = ((result.error_body or {}).get("error") or {}).get("code")
                log.errors.append(f"http_{result.status}:{code}")
                break
            message = result.message
            assert message is not None
            if continuing is None:
                self.messages.append(message)
            if result.error_text:
                log.errors.append("stream_error")
            log.finish_reasons.append(result.finish_reason)
            log.texts.extend(result.texts)
            log.data_types.update(c["type"] for c in result.data_chunks)
            log.citations.extend(result.citations)
            log.denied.extend(result.denied)
            log.agent = result.active_agent or log.agent
            log.blocked = log.blocked or result.metadata.get("blocked")
            for call in result.tool_calls:
                log.tool_calls.append(tool_call_record(call.tool_call_id, call.name, call.input))
            log.outputs.update(result.tool_outputs)
            if not self.answer_browser_tools(message, result, log, policy):
                break
        else:
            log.errors.append("max_rounds_exceeded")
        return log
