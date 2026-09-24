from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from app.chat import agent_loop
from app.chat.agent_loop import CONTENT_FILTER_REFUSAL, AgentOutcome, TurnRuntime, run_agent
from app.chat.fakes import fake_search
from app.chat.history import call_signature
from app.chat.model import ContentFilterError, FunctionCallDone, ModelEvent, ModelRequest, ResponseDone, TextDelta
from app.chat.tool_registry import StepBudget
from tests.chat.helpers import chat_context, counter_ids

Script = Callable[[ModelRequest], list[ModelEvent]]


class ScriptedModel:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        for event in self.script(request):
            yield event


def call(call_id: str, name: str, args: dict[str, Any]) -> list[ModelEvent]:
    return [FunctionCallDone(f"fc_{call_id}", call_id, name, json.dumps(args)), ResponseDone(model="m")]


def say(text: str) -> list[ModelEvent]:
    return [TextDelta("msg", text), ResponseDone(model="m", input_tokens=10, output_tokens=5)]


def outputs(request: ModelRequest) -> dict[str, str]:
    names = {i["call_id"]: i["name"] for i in request.input if i.get("type") == "function_call"}
    return {names[i["call_id"]]: i["output"] for i in request.input if i.get("type") == "function_call_output"}


def runtime_for(model: Any, *, used: int = 0, declined: set[str] | None = None) -> TurnRuntime:
    return TurnRuntime(
        model=model,
        search=fake_search,
        context=chat_context(),
        budget=StepBudget(6, used=used),
        declined_signatures=declined or set(),
        id_factory=counter_ids(),
    )


async def collect(
    agent: Any, runtime: TurnRuntime, items: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], AgentOutcome]:
    chunks: list[dict[str, Any]] = []
    outcome: AgentOutcome | None = None
    async for event in run_agent(agent, items or [{"type": "message", "role": "user", "content": "hi"}], runtime):
        if isinstance(event, AgentOutcome):
            outcome = event
        else:
            chunks.append(event)
    assert outcome is not None
    return chunks, outcome


async def test_browser_tool_ends_the_request_with_tool_calls() -> None:
    model = ScriptedModel(lambda r: call("c1", "find_benefits", {"query": "massage"}))
    chunks, outcome = await collect("plan_claims", runtime_for(model))
    assert outcome.finish == "tool-calls"
    assert [c["type"] for c in chunks] == [
        "start-step",
        "tool-input-start",
        "tool-input-delta",
        "tool-input-available",
        "finish-step",
    ]
    assert "providerExecuted" not in chunks[3]
    assert len(model.requests) == 1


async def test_approval_tools_stream_an_approval_request() -> None:
    args = {"member_alias": "[MEMBER_A]", "provider": None, "lines": []}
    model = ScriptedModel(lambda r: call("c1", "draft_claim", args))
    chunks, outcome = await collect("plan_claims", runtime_for(model))
    assert outcome.finish == "tool-calls"
    approval = next(c for c in chunks if c["type"] == "tool-approval-request")
    assert approval == {"type": "tool-approval-request", "approvalId": "approval-c1", "toolCallId": "c1"}


async def test_server_tool_runs_in_loop_and_continues() -> None:
    def script(request: ModelRequest) -> list[ModelEvent]:
        if "search_public_knowledge" in outputs(request):
            return say("It depends on the province (Canada Revenue Agency).")
        return call("k1", "search_public_knowledge", {"query": "massage medical expense", "region": "CA", "top_k": 3})

    model = ScriptedModel(script)
    chunks, outcome = await collect("knowledge", runtime_for(model))
    assert outcome.finish == "stop"
    types = [c["type"] for c in chunks]
    assert types.index("data-citations") < types.index("tool-output-available")
    output = next(c for c in chunks if c["type"] == "tool-output-available")
    assert output["providerExecuted"] is True
    assert output["output"]["results"][0]["license"]
    documents = outputs(model.requests[1])["search_public_knowledge"]
    assert documents.startswith("<documents>") and "<document source=" in documents
    assert outcome.citations and outcome.text.startswith("It depends")


async def test_non_allowlisted_call_is_rejected_and_never_streamed() -> None:
    def script(request: ModelRequest) -> list[ModelEvent]:
        if "search_public_knowledge" in outputs(request):
            return say("Sorry, I can't do that.")
        return call("x1", "search_public_knowledge", {"query": "q", "region": "CA", "top_k": 1})

    model = ScriptedModel(script)
    chunks, outcome = await collect("plan_claims", runtime_for(model))
    assert not any(c["type"].startswith("tool-") for c in chunks)
    assert json.loads(outputs(model.requests[1])["search_public_knowledge"])["error"] == "tool_not_allowed"
    assert outcome.finish == "stop"


async def test_step_cap_forces_tool_choice_none() -> None:
    def script(request: ModelRequest) -> list[ModelEvent]:
        if request.force_final:
            return say("Here is what I found.")
        n = sum(1 for i in request.input if i.get("type") == "function_call") + 1
        return call(f"k{n}", "search_public_knowledge", {"query": f"q{n}", "region": "CA", "top_k": 1})

    model = ScriptedModel(script)
    runtime = runtime_for(model, used=4)
    chunks, outcome = await collect("knowledge", runtime)
    assert [r.force_final for r in model.requests] == [False, False, True]
    assert runtime.budget.used == 6
    assert outcome.text == "Here is what I found."
    assert sum(1 for c in chunks if c["type"] == "tool-output-available") == 2


async def test_forced_final_that_still_calls_tools_ends_instead_of_looping() -> None:
    model = ScriptedModel(lambda r: call("k", "search_public_knowledge", {"query": "q", "region": "CA", "top_k": 1}))
    chunks, outcome = await collect("knowledge", runtime_for(model, used=6))
    assert len(model.requests) == 1
    assert outcome.finish == "stop"
    assert agent_loop.STEP_LIMIT_FALLBACK in outcome.text
    assert not any(c["type"] == "tool-input-available" for c in chunks)


async def test_nested_knowledge_agent_shares_the_budget_and_cannot_use_browser_tools() -> None:
    def script(request: ModelRequest) -> list[ModelEvent]:
        done = outputs(request)
        if request.agent == "knowledge":
            if "find_benefits" not in done:
                return call("n1", "find_benefits", {"query": "massage"})  # not allowed for knowledge, nor nested
            if "search_public_knowledge" not in done:
                return call("n2", "search_public_knowledge", {"query": "massage", "region": "CA", "top_k": 2})
            return say("Depends on the province.")
        if "ask_knowledge_agent" not in done:
            return call("p1", "ask_knowledge_agent", {"question": "Is massage a medical expense?", "region": "CA"})
        return say("Massage is covered; for tax it depends on the province.")

    model = ScriptedModel(script)
    runtime = runtime_for(model)
    chunks, outcome = await collect("plan_claims", runtime)

    types = [c["type"] for c in chunks]
    assert types.count("tool-input-available") == 1  # only ask_knowledge_agent is visible
    statuses = [c for c in chunks if c["type"] == "data-status"]
    assert [s["data"]["state"] for s in statuses] == ["running", "done"]
    assert {s["id"] for s in statuses} == {"status-p1"}
    assert "data-citations" in types
    ask_output = next(c for c in chunks if c["type"] == "tool-output-available")["output"]
    assert ask_output["answer"] == "Depends on the province."
    assert ask_output["nestedToolCalls"] == 1
    # ask_knowledge_agent (1) + rejected find_benefits (1) + search (1)
    assert runtime.budget.used == 3
    assert [r.depth for r in model.requests] == [0, 1, 1, 1, 0]
    assert outcome.finish == "stop"


async def test_declined_approval_is_not_forwarded_again() -> None:
    args = {"member_alias": "[MEMBER_A]", "provider": None, "lines": []}

    def script(request: ModelRequest) -> list[ModelEvent]:
        if "draft_claim" in outputs(request):
            return say("Okay, I won't create it.")
        return call("retry", "draft_claim", args)  # a model that tries again

    model = ScriptedModel(script)
    runtime = runtime_for(model, declined={call_signature("draft_claim", args)})
    chunks, outcome = await collect("plan_claims", runtime)
    assert not any(c["type"] in ("tool-approval-request", "tool-input-available") for c in chunks)
    assert json.loads(outputs(model.requests[1])["draft_claim"])["declined"] is True
    assert outcome.finish == "stop"


async def test_content_filter_becomes_a_refusal_and_a_safety_event(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        agent_loop.telemetry, "record_safety_event", lambda kind, surface: events.append((kind, surface))
    )

    class Blocked:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            yield TextDelta("m", "Partial ")
            raise ContentFilterError("blocked")

    chunks, outcome = await collect("plan_claims", runtime_for(Blocked()))
    assert outcome.finish == "content-filter"
    assert events == [("content_filter", "chat_agent")]
    types = [c["type"] for c in chunks]
    assert types.count("text-start") == types.count("text-end") == 2
    assert any(c.get("delta") == CONTENT_FILTER_REFUSAL for c in chunks)
