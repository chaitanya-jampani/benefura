"""Agent loop as a UI stream generator; a guardrail block becomes a canned refusal, never an HTTP error."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app import telemetry
from app.chat import ui_stream
from app.chat.history import call_signature, canonical_json
from app.chat.model import ContentFilterError, FunctionCallDone, ModelClient, ModelRequest, ResponseDone, TextDelta
from app.chat.server_tools import (
    SERVER_TOOLS,
    NestedAgentResult,
    SearchFn,
    ServerToolContext,
    ServerToolResult,
)
from app.chat.tool_registry import StepBudget, ToolRejected, resolve_tool
from app.models.api import AgentName, ChatContext
from app.models.plan import Region

CONTENT_FILTER_REFUSAL = (
    "I can't help with that request. I can answer questions about your benefits, your claims and public "
    "health coverage information."
)
STEP_LIMIT_FALLBACK = (
    "I've used as many lookups as I can for one question and couldn't finish. Try asking a narrower question."
)

Outcome = Literal["stop", "tool-calls", "content-filter", "length"]


@dataclass
class TurnUsage:
    by_model: dict[str, tuple[int, int]] = field(default_factory=dict)
    model_calls: int = 0

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        prev_in, prev_out = self.by_model.get(model, (0, 0))
        self.by_model[model] = (prev_in + input_tokens, prev_out + output_tokens)
        self.model_calls += 1

    @property
    def input_tokens(self) -> int:
        return sum(i for i, _ in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(o for _, o in self.by_model.values())


@dataclass
class AgentOutcome:
    finish: Outcome
    text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0


@dataclass
class TurnRuntime:
    model: ModelClient
    search: SearchFn
    context: ChatContext
    budget: StepBudget
    declined_signatures: set[str] = field(default_factory=set)
    usage: TurnUsage = field(default_factory=TurnUsage)
    id_factory: Callable[[str], str] = field(default=lambda prefix: prefix)


def context_item(context: ChatContext) -> dict[str, Any]:
    payload = context.model_dump(mode="json", exclude_none=True)
    return {
        "type": "message",
        "role": "developer",
        "content": "Context (aliases are opaque labels): " + json.dumps(payload, separators=(",", ":")),
    }


def _parse_arguments(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def run_agent(
    agent: AgentName,
    input_items: list[dict[str, Any]],
    runtime: TurnRuntime,
    *,
    depth: int = 0,
) -> AsyncIterator[ui_stream.Chunk | AgentOutcome]:
    stream_to_ui = depth == 0
    items = list(input_items)
    collected_text: list[str] = []
    citations: list[dict[str, Any]] = []
    tool_calls = 0

    while True:
        force_final = runtime.budget.exhausted
        if stream_to_ui:
            yield ui_stream.start_step()

        open_text: dict[str, str] = {}
        step_items: list[dict[str, Any]] = []
        step_text: dict[str, list[str]] = {}
        calls: list[FunctionCallDone] = []
        started = time.monotonic()
        request = ModelRequest(agent=agent, input=items, depth=depth, force_final=force_final)
        incomplete: str | None = None
        try:
            async for event in runtime.model.stream(request):
                if isinstance(event, TextDelta):
                    if event.item_id not in step_text:
                        step_text[event.item_id] = []
                        step_items.append({"type": "message", "role": "assistant", "_text_item": event.item_id})
                        if stream_to_ui:
                            part_id = runtime.id_factory("text")
                            open_text[event.item_id] = part_id
                            yield ui_stream.text_start(part_id)
                    step_text[event.item_id].append(event.delta)
                    if stream_to_ui:
                        yield ui_stream.text_delta(open_text[event.item_id], event.delta)
                elif isinstance(event, FunctionCallDone):
                    calls.append(event)
                elif isinstance(event, ResponseDone):
                    incomplete = event.incomplete_reason
                    runtime.usage.add(event.model, event.input_tokens, event.output_tokens)
                    telemetry.record_model_usage(
                        event.model,
                        agent,
                        event.input_tokens,
                        event.output_tokens,
                        int((time.monotonic() - started) * 1000),
                    )
        except ContentFilterError:
            telemetry.record_safety_event("content_filter", "chat_agent" if depth == 0 else "chat_nested_agent")
            for part_id in open_text.values():
                yield ui_stream.text_end(part_id)
            if stream_to_ui:
                part_id = runtime.id_factory("text")
                for chunk in ui_stream.text(part_id, CONTENT_FILTER_REFUSAL):
                    yield chunk
                yield ui_stream.finish_step()
            yield AgentOutcome(
                finish="content-filter", text=CONTENT_FILTER_REFUSAL, citations=citations, tool_calls=tool_calls
            )
            return

        for part_id in open_text.values():
            yield ui_stream.text_end(part_id)
        for item in step_items:
            text = "".join(step_text[item.pop("_text_item")])
            item["content"] = text
            collected_text.append(text)
        items.extend(i for i in step_items if i["content"].strip())

        if not calls:
            if stream_to_ui:
                yield ui_stream.finish_step()
            finish: Outcome = "length" if incomplete == "max_output_tokens" else "stop"
            yield AgentOutcome(
                finish=finish,
                text="\n\n".join(t for t in collected_text if t),
                citations=citations,
                tool_calls=tool_calls,
            )
            return

        pending_browser = False
        for call in calls:
            arguments = _parse_arguments(call.arguments)
            function_call = {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments or "{}",
            }
            try:
                spec = resolve_tool(agent, call.name, depth=depth)
                if force_final:
                    raise ToolRejected("step_limit", "No more tool calls are allowed for this question. Answer now.")
                if arguments is None:
                    raise ToolRejected("tool_not_allowed", "The arguments were not valid JSON.")
                if spec.requires_approval and call_signature(call.name, arguments) in runtime.declined_signatures:
                    items.extend(
                        [
                            function_call,
                            {
                                "type": "function_call_output",
                                "call_id": call.call_id,
                                "output": canonical_json(
                                    {
                                        "declined": True,
                                        "reason": "The user already declined this exact change. Do not retry it.",
                                    }
                                ),
                            },
                        ]
                    )
                    telemetry.record_tool_call(call.name, spec.executor, 0, error="declined_retry_blocked")
                    runtime.budget.used += 1
                    continue
                runtime.budget.consume()
            except ToolRejected as rejected:
                # Rejected calls still spend a step so a model that keeps asking reaches the forced final answer.
                runtime.budget.used += 1
                telemetry.record_tool_call(call.name, "none", 0, error=rejected.code)
                items.extend(
                    [
                        function_call,
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": canonical_json(rejected.model_output()),
                        },
                    ]
                )
                continue

            tool_calls += 1
            provider_executed = True if spec.executor == "server" else None
            if stream_to_ui:
                yield ui_stream.tool_input_start(call.call_id, call.name, provider_executed=provider_executed)
                yield ui_stream.tool_input_delta(call.call_id, call.arguments or "{}")
                yield ui_stream.tool_input_available(
                    call.call_id, call.name, arguments, provider_executed=provider_executed
                )

            if spec.executor == "browser":
                pending_browser = True
                if stream_to_ui and spec.requires_approval:
                    yield ui_stream.tool_approval_request(f"approval-{call.call_id}", call.call_id)
                telemetry.record_tool_call(call.name, "browser", 0)
                continue

            tool_started = time.monotonic()
            ctx = ServerToolContext(
                tool_call_id=call.call_id,
                region=runtime.context.region,
                search=runtime.search,
                run_nested_knowledge=lambda question, region: _nested_knowledge(question, region, runtime, depth + 1),
                citations=citations,
            )
            result: ServerToolResult | None = None
            try:
                async for event in SERVER_TOOLS[call.name](arguments, ctx):
                    if isinstance(event, ServerToolResult):
                        result = event
                    else:
                        yield event
            except Exception as exc:  # noqa: BLE001 - the model gets a tool error, the turn continues
                telemetry.record_tool_call(
                    call.name, "server", int((time.monotonic() - tool_started) * 1000), type(exc).__name__
                )
                message = "The tool is unavailable right now."
                if stream_to_ui:
                    yield ui_stream.tool_output_error(call.call_id, message, provider_executed=True)
                items.extend(
                    [
                        function_call,
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": canonical_json({"error": message}),
                        },
                    ]
                )
                continue
            assert result is not None
            telemetry.record_tool_call(call.name, "server", int((time.monotonic() - tool_started) * 1000))
            if stream_to_ui:
                yield ui_stream.tool_output_available(call.call_id, result.ui_output, provider_executed=True)
            items.extend(
                [
                    function_call,
                    {"type": "function_call_output", "call_id": call.call_id, "output": result.model_output},
                ]
            )

        if pending_browser:
            if stream_to_ui:
                yield ui_stream.finish_step()
            yield AgentOutcome(
                finish="tool-calls", text="\n\n".join(collected_text), citations=citations, tool_calls=tool_calls
            )
            return
        if force_final:
            # The model ignored tool_choice="none"; end the run rather than loop.
            if not any(t.strip() for t in collected_text):
                collected_text.append(STEP_LIMIT_FALLBACK)
                if stream_to_ui:
                    for chunk in ui_stream.text(runtime.id_factory("text"), STEP_LIMIT_FALLBACK):
                        yield chunk
            if stream_to_ui:
                yield ui_stream.finish_step()
            yield AgentOutcome(
                finish="stop", text="\n\n".join(collected_text), citations=citations, tool_calls=tool_calls
            )
            return
        if stream_to_ui:
            yield ui_stream.finish_step()


async def _nested_knowledge(
    question: str, region: Region, runtime: TurnRuntime, depth: int
) -> AsyncIterator[ui_stream.Chunk | NestedAgentResult]:
    items = [
        context_item(runtime.context.model_copy(update={"region": region})),
        {"type": "message", "role": "user", "content": question},
    ]
    outcome: AgentOutcome | None = None
    async for event in run_agent("knowledge", items, runtime, depth=depth):
        if isinstance(event, AgentOutcome):
            outcome = event
        else:
            yield event
    outcome = outcome or AgentOutcome(finish="stop")
    yield NestedAgentResult(
        text=outcome.text,
        citations=outcome.citations,
        tool_calls=outcome.tool_calls,
        content_filtered=outcome.finish == "content-filter",
    )
