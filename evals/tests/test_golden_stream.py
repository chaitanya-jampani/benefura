from __future__ import annotations

import copy
import json

import pytest

from evals.harness.browser_tools import BrowserToolExecutor, load_plan
from evals.harness.client import StreamResult, UIStreamAssembler, chat_body, new_assistant_message, parse_sse_lines
from evals.harness.conversation import Conversation, TurnLog, decline_all
from evals.tests.conftest import GOLDEN_CONTEXT, GOLDEN_DIR, golden_request, golden_sse

GOLDENS = sorted(p.stem for p in GOLDEN_DIR.glob("*.sse"))


def assemble(name: str, message: dict | None = None) -> StreamResult:
    result = StreamResult(status=200)
    target = message if message is not None else new_assistant_message("assistant-1")
    assembler = UIStreamAssembler(result, target, continued=message is not None)
    for chunk in parse_sse_lines(golden_sse(name)):
        assembler.feed(chunk)
    return result


def raw_frames(name: str) -> list[dict]:
    frames = [line[len("data: ") :] for line in golden_sse(name) if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    return [json.loads(f) for f in frames[:-1]]


def test_all_goldens_are_present():
    assert set(GOLDENS) >= {
        "approval-declined",
        "approval-request",
        "browser-tool-round",
        "content-filter",
        "knowledge-citations",
        "mixed-status-citations",
        "off-topic",
        "pii-warning",
    }


@pytest.mark.parametrize("name", GOLDENS)
def test_parser_reads_every_frame(name):
    assert parse_sse_lines(golden_sse(name)) == raw_frames(name)
    result = assemble(name)
    assert result.finish_reason in ("stop", "tool-calls", "content-filter")
    assert result.metadata.get("traceId") == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_browser_tool_round_then_continuation_matches_the_browser_request():
    user = golden_request("browser-tool-round")["messages"]
    result = assemble("browser-tool-round")
    message = result.message
    assert result.active_agent == "plan_claims"
    assert [p["type"] for p in message["parts"]] == ["step-start", "tool-find_benefits"]
    assert message["parts"][1] == {
        "type": "tool-find_benefits",
        "toolCallId": "call_pc_1",
        "state": "input-available",
        "input": {"query": "massage"},
    }

    executor = BrowserToolExecutor(plan=load_plan("samples/fixtures/ca-northwind.plan.json"), today="2026-09-16")
    conversation = Conversation(object(), GOLDEN_CONTEXT, executor)  # type: ignore[arg-type]  # nothing is sent
    assert conversation.answer_browser_tools(message, result, TurnLog(), decline_all) is True
    body = chat_body([*copy.deepcopy(user), message], GOLDEN_CONTEXT)
    expected = golden_request("approval-request")["messages"]
    assert body["activeAgent"] == "plan_claims" and body["context"] == GOLDEN_CONTEXT
    sent = body["messages"][1]
    golden = expected[1]
    assert sent["metadata"] == golden["metadata"]
    assert [p["type"] for p in sent["parts"]] == [p["type"] for p in golden["parts"]]
    part, golden_part = sent["parts"][1], golden["parts"][1]
    for key in ("toolCallId", "state", "input"):
        assert part[key] == golden_part[key]
    for key in ("benefitId", "name", "category", "coverage", "limits"):
        assert part["output"]["benefits"][0][key] == golden_part["output"]["benefits"][0][key]
    assert part["output"]["benefits"][0]["source"]["page"] == 5


def test_decline_builds_exactly_the_golden_decline_request():
    request = golden_request("approval-request")
    messages = copy.deepcopy(request["messages"])
    result = assemble("approval-request", messages[-1])
    draft = messages[-1]["parts"][-1]
    assert draft["state"] == "approval-requested" and draft["approval"] == {"id": "approval-call_pc_2"}
    assert result.approval_requests == {"call_pc_2": "approval-call_pc_2"}

    executor = BrowserToolExecutor(plan=load_plan("samples/fixtures/ca-northwind.plan.json"), today="2026-09-16")
    conversation = Conversation(object(), GOLDEN_CONTEXT, executor)  # type: ignore[arg-type]
    log = TurnLog()
    assert conversation.answer_browser_tools(messages[-1], result, log, lambda _n, _i: (False, "Wrong amount"))
    assert executor.calls == []  # a declined tool never runs
    body = chat_body(messages, GOLDEN_CONTEXT)
    assert body["messages"] == golden_request("approval-declined")["messages"]
    assert body["activeAgent"] == "plan_claims"
    assert log.approvals == [{"tool": "draft_claim", "decision": "decline"}]


def test_declined_stream_marks_the_call_denied_and_answers():
    messages = copy.deepcopy(golden_request("approval-declined")["messages"])
    result = assemble("approval-declined", messages[-1])
    assert result.denied == ["call_pc_2"]
    draft = next(p for p in messages[-1]["parts"] if p.get("toolCallId") == "call_pc_2")
    assert draft["state"] == "output-denied" and draft["approval"]["approved"] is False
    assert result.text == "Okay, I won't create that claim. Tell me what you'd like to change."
    assert messages[-1]["id"] == "assistant-1"  # continued, not replaced


def test_approval_on_approve_runs_the_tool_and_keeps_the_approval():
    messages = copy.deepcopy(golden_request("approval-request")["messages"])
    result = assemble("approval-request", messages[-1])
    executor = BrowserToolExecutor(plan=load_plan("samples/fixtures/ca-northwind.plan.json"), today="2026-09-16")
    conversation = Conversation(object(), GOLDEN_CONTEXT, executor)  # type: ignore[arg-type]
    conversation.answer_browser_tools(messages[-1], result, TurnLog(), lambda _n, _i: (True, None))
    draft = messages[-1]["parts"][-1]
    assert draft["state"] == "output-available"
    assert draft["approval"] == {"id": "approval-call_pc_2", "approved": True}
    assert draft["output"]["totalChargedCents"] == 12000 and draft["output"]["status"] == "draft"


def test_knowledge_stream_citations_and_server_tool_output():
    result = assemble("knowledge-citations")
    assert result.active_agent == "knowledge"
    assert [c.name for c in result.tool_calls] == ["search_public_knowledge"]
    assert result.tool_calls[0].provider_executed
    assert [c["sourceId"] for c in result.citations] == ["ca-cra-authorized-practitioners", "ca-cra-medical-expenses"]
    output = result.tool_outputs["call_kn_1"]
    assert output["results"][0]["snippet"].startswith("Whether fees for a practitioner")
    citation_parts = [p for p in result.message["parts"] if p["type"] == "data-citations"]
    assert len(citation_parts) == 1 and citation_parts[0]["id"] == "citations-call_kn_1"
    assert chat_body([result.message], GOLDEN_CONTEXT)["messages"][0]["parts"] == [
        p for p in result.message["parts"] if not p["type"].startswith("data-")
    ]


def test_mixed_stream_replaces_status_by_id_and_leaves_a_browser_call_pending():
    result = assemble("mixed-status-citations")
    statuses = [p for p in result.message["parts"] if p["type"] == "data-status"]
    assert len(statuses) == 1 and statuses[0]["data"] == {
        "message": "Checked public reference material",
        "state": "done",
    }
    assert len(result.data("status")) == 2
    ask = next(p for p in result.message["parts"] if p.get("toolCallId") == "call_pc_1")
    assert ask["providerExecuted"] and ask["state"] == "output-available" and "answer" in ask["output"]
    find = next(p for p in result.message["parts"] if p.get("toolCallId") == "call_pc_2")
    assert find["state"] == "input-available"
    sent = chat_body([result.message], GOLDEN_CONTEXT)["messages"][0]
    assert not any(p["type"].startswith("data-") for p in sent["parts"])


@pytest.mark.parametrize(
    ("name", "agent", "blocked", "finish"),
    [
        ("off-topic", None, None, "stop"),
        ("pii-warning", None, "pii", "stop"),
        ("content-filter", "plan_claims", "content_filter", "content-filter"),
    ],
)
def test_refusals_and_blocks(name, agent, blocked, finish):
    result = assemble(name)
    assert result.active_agent == agent
    assert result.metadata.get("blocked") == blocked
    assert result.finish_reason == finish
    assert result.tool_calls == []
    if name == "pii-warning":
        assert result.texts == [] and result.data("pii-warning")[0]["categories"] == ["CASocialInsuranceNumber"]
    else:
        assert result.text
