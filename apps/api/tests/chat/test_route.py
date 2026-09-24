from __future__ import annotations

from typing import Any

import pytest

from app.chat import route as chat_route
from tests.chat.helpers import (
    FIND_MASSAGE_OUTPUT,
    TRACE_ID,
    USAGE_OUTPUT,
    ChatHarness,
    assistant_from_chunks,
    build_harness,
    parse_sse,
    set_tool_output,
    user,
)


def test_headers_and_agent_provenance(harness: ChatHarness) -> None:
    response = harness.post([user("How much massage do I have left?")])
    assert response.status_code == 200
    assert response.headers["x-benefura-trace-id"] == TRACE_ID
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert response.headers["cache-control"] == "no-cache"
    start = parse_sse(response.text)[0]
    assert start["type"] == "start"
    assert start["messageMetadata"] == {
        "agent": "plan_claims",
        "agentName": "benefura-plan-claims",
        "agentVersion": "fake",
        "traceId": TRACE_ID,
        "route": "plan_claims",
    }


def test_browser_tool_round_trip_to_answer(harness: ChatHarness) -> None:
    messages: list[dict[str, Any]] = [user("How much massage do I have left?")]
    first = parse_sse(harness.post(messages).text)
    assert first[-1] == {"type": "finish", "finishReason": "tool-calls"}
    assistant = assistant_from_chunks(first)
    set_tool_output(assistant, "call_pc_1", FIND_MASSAGE_OUTPUT)

    second = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    usage_call = next(c for c in second if c["type"] == "tool-input-available")
    assert usage_call["toolName"] == "get_usage"
    assert usage_call["input"] == {"benefit_id": "ben-massage", "member_alias": "[MEMBER_A]"}
    assistant = assistant_from_chunks(second, base=assistant)
    set_tool_output(assistant, usage_call["toolCallId"], USAGE_OUTPUT)

    third = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    text = "".join(c["delta"] for c in third if c["type"] == "text-delta")
    assert "$420.00 left for Massage therapy" in text
    assert third[-1] == {"type": "finish", "finishReason": "stop"}
    # Router runs once for the user message; continuations stay with the active agent.
    assert harness.router.calls == 1
    assert "messageId" not in third[0]


def test_hard_tier_pii_never_reaches_a_model(harness: ChatHarness) -> None:
    response = harness.post([user("My SIN is 046 454 286, what can I claim?")])
    chunks = parse_sse(response.text)
    assert response.status_code == 200
    warning = next(c for c in chunks if c["type"] == "data-pii-warning")
    assert warning["data"]["categories"] == ["CASocialInsuranceNumber"]
    assert chunks[0]["messageMetadata"]["blocked"] == "pii"
    assert harness.router.calls == 0
    assert harness.model.requests == []
    assert "046" not in response.text


def test_advisory_pii_proceeds(harness: ChatHarness) -> None:
    response = harness.post([user("My physio's number is 416-555-0142, how much physio do I have left?")])
    chunks = parse_sse(response.text)
    assert not any(c["type"] == "data-pii-warning" for c in chunks)
    assert harness.router.calls == 1
    assert len(harness.model.requests) == 1


def test_blocked_pii_turn_is_not_replayed_later(harness: ChatHarness) -> None:
    blocked_user = user("My SIN is 046 454 286", "u1")
    blocked = assistant_from_chunks(parse_sse(harness.post([blocked_user]).text), message_id="a1")
    harness.post([blocked_user, blocked, user("How much massage do I have left?", "u2")])
    replayed = str(harness.model.requests[-1].input)
    assert "046 454 286" not in replayed
    assert "How much massage do I have left?" in replayed


def test_off_topic_gets_a_refusal_without_a_model_call(harness: ChatHarness) -> None:
    chunks = parse_sse(harness.post([user("Write me a poem about the weather")]).text)
    text = "".join(c["delta"] for c in chunks if c["type"] == "text-delta")
    assert text == chat_route.OFF_TOPIC_REFUSAL
    assert chunks[0]["messageMetadata"]["route"] == "off_topic"
    assert harness.model.requests == []


def test_content_filter_is_a_refusal_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(chat_route.telemetry, "record_safety_event", lambda k, s: events.append((k, s)))
    h = build_harness(content_filter=True)
    response = h.post([user("How much massage do I have left?")])
    assert response.status_code == 200
    chunks = parse_sse(response.text)
    assert chunks[-1] == {
        "type": "finish",
        "finishReason": "content-filter",
        "messageMetadata": {"blocked": "content_filter"},
    }
    assert "I can't help with that request" in response.text
    assert events == [("content_filter", "chat_agent")]


def test_mixed_question_uses_both_agents_with_status_and_citations(harness: ChatHarness) -> None:
    messages: list[dict[str, Any]] = [user("Is massage covered and is it a medical expense for tax?")]
    first = parse_sse(harness.post(messages).text)
    types = [c["type"] for c in first]
    assert types.count("data-status") == 2
    assert "data-citations" in types
    assert [r.agent for r in harness.model.requests] == ["plan_claims", "knowledge", "knowledge", "plan_claims"]
    assistant = assistant_from_chunks(first)
    find_call = next(c for c in first if c["type"] == "tool-input-available" and c["toolName"] == "find_benefits")
    set_tool_output(assistant, find_call["toolCallId"], FIND_MASSAGE_OUTPUT)

    second = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    text = "".join(c["delta"] for c in second if c["type"] == "text-delta")
    assert "Massage therapy is covered" in text and "(booklet p. 5)" in text
    assert "On the tax side:" in text
    assert [r.agent for r in harness.model.requests] == [
        "plan_claims",
        "knowledge",
        "knowledge",
        "plan_claims",
        "plan_claims",
    ]


def test_approval_flow_and_declined_approval_is_not_retried(harness: ChatHarness) -> None:
    messages: list[dict[str, Any]] = [user("Draft a claim for my massage, $120")]
    assistant = assistant_from_chunks(parse_sse(harness.post(messages).text))
    set_tool_output(assistant, "call_pc_1", FIND_MASSAGE_OUTPUT)

    second = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    approval = next(c for c in second if c["type"] == "tool-approval-request")
    assert second[-1]["finishReason"] == "tool-calls"
    assistant = assistant_from_chunks(second, base=assistant)
    part = next(p for p in assistant["parts"] if p.get("toolCallId") == approval["toolCallId"])
    assert part["input"]["lines"][0]["charged_cents"] == 12000
    part["state"] = "approval-responded"
    part["approval"] = {"id": approval["approvalId"], "approved": False, "reason": "Wrong amount"}

    third = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    assert third[1] == {"type": "tool-output-denied", "toolCallId": approval["toolCallId"]}
    assert not any(c["type"] in ("tool-approval-request", "tool-input-available") for c in third)
    assert "won't create that claim" in "".join(c["delta"] for c in third if c["type"] == "text-delta")
    replayed = harness.model.requests[-1].input
    declined_output = next(
        i for i in replayed if i.get("type") == "function_call_output" and i["call_id"] == approval["toolCallId"]
    )
    assert declined_output["output"] == '{"declined":true,"reason":"Wrong amount"}'


def test_approved_claim_completes(harness: ChatHarness) -> None:
    messages: list[dict[str, Any]] = [user("Draft a claim for my massage, $120")]
    assistant = assistant_from_chunks(parse_sse(harness.post(messages).text))
    set_tool_output(assistant, "call_pc_1", FIND_MASSAGE_OUTPUT)
    second = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    assistant = assistant_from_chunks(second, base=assistant)
    part = next(p for p in assistant["parts"] if p.get("state") == "approval-requested")
    part["approval"]["approved"] = True
    set_tool_output(assistant, part["toolCallId"], {"claimId": "clm_1", "status": "draft", "totalChargedCents": 12000})

    third = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    assert not any(c["type"] == "tool-output-denied" for c in third)
    assert "draft claim for $120.00" in "".join(c["delta"] for c in third if c["type"] == "text-delta")


def test_ai_off_is_503() -> None:
    h = build_harness(ai_mode="off")
    response = h.post([user("hello")])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_disabled"


def test_rate_limit_is_429() -> None:
    h = build_harness(per_minute=1)
    assert h.post([user("How much massage do I have left?")]).status_code == 200
    response = h.post([user("How much massage do I have left?")])
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1


def test_continuation_without_tool_results_is_rejected(harness: ChatHarness) -> None:
    response = harness.post(
        [user("hi"), {"id": "a", "role": "assistant", "parts": [{"type": "text", "text": "Hello"}]}]
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_request_schema_is_enforced(harness: ChatHarness) -> None:
    response = harness.client.post("/api/chat", json={"messages": [user("hi")], "context": {"region": "NZ"}})
    assert response.status_code == 422


def test_failed_browser_tool_is_reported_not_retried_forever(harness: ChatHarness) -> None:
    messages: list[dict[str, Any]] = [user("How much massage do I have left?")]
    assistant = assistant_from_chunks(parse_sse(harness.post(messages).text))
    part = next(p for p in assistant["parts"] if p.get("toolCallId") == "call_pc_1")
    part["state"] = "output-error"
    part["errorText"] = "No plan is loaded yet."

    chunks = parse_sse(harness.post([*messages, assistant], active_agent="plan_claims").text)
    text = "".join(c["delta"] for c in chunks if c["type"] == "text-delta")
    assert "couldn't find massage in your plan" in text
    assert not any(c["type"] == "tool-input-available" for c in chunks)
    assert chunks[-1] == {"type": "finish", "finishReason": "stop"}
