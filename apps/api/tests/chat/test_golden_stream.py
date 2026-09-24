"""The web tests replay these ``.sse`` files through the AI SDK parser; regenerate with ``UPDATE_GOLDEN=1``."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.chat.helpers import (
    FIND_MASSAGE_OUTPUT,
    ChatHarness,
    assistant_from_chunks,
    build_harness,
    parse_sse,
    set_tool_output,
    user,
)

GOLDEN = Path(__file__).parent / "golden"


def _browser_tool_round(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("How much massage do I have left?")], {}


def _approval_request(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    first = [user("Draft a claim for my massage, $120")]
    assistant = assistant_from_chunks(parse_sse(h.post(first).text))
    set_tool_output(assistant, "call_pc_1", FIND_MASSAGE_OUTPUT)
    return [*first, assistant], {}


def _approval_declined(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    messages, _ = _approval_request(h)
    assistant = assistant_from_chunks(parse_sse(h.post(messages).text), base=messages[-1])
    part = next(p for p in assistant["parts"] if p.get("state") == "approval-requested")
    part["state"] = "approval-responded"
    part["approval"] = {**part["approval"], "approved": False, "reason": "Wrong amount"}
    return [messages[0], assistant], {}


def _mixed_status_citations(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("Is massage covered and is it a medical expense for tax?")], {}


def _knowledge_citations(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("Is massage therapy a medical expense for tax?")], {}


def _pii_warning(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("My SIN is 046 454 286, can you check my claim?")], {}


def _off_topic(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("Write me a poem about the weather")], {}


def _content_filter(h: ChatHarness) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return [user("How much massage do I have left?")], {}


SCENARIOS: dict[str, tuple[Callable[[ChatHarness], tuple[list[dict[str, Any]], dict[str, str]]], bool]] = {
    "browser-tool-round": (_browser_tool_round, False),
    "approval-request": (_approval_request, False),
    "approval-declined": (_approval_declined, False),
    "mixed-status-citations": (_mixed_status_citations, False),
    "knowledge-citations": (_knowledge_citations, False),
    "pii-warning": (_pii_warning, False),
    "off-topic": (_off_topic, False),
    "content-filter": (_content_filter, True),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_golden_stream(name: str) -> None:
    build, content_filter = SCENARIOS[name]
    setup = build_harness()
    messages, headers = build(setup)
    # Fresh harness so ids in the golden file do not depend on set-up requests.
    h = build_harness(content_filter=content_filter)
    response = h.post(messages, headers=headers)
    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.endswith("data: [DONE]\n\n")

    path = GOLDEN / f"{name}.sse"
    request_path = GOLDEN / f"{name}.request.json"
    if os.environ.get("UPDATE_GOLDEN") == "1" or not path.exists():
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(response.text)
        request_path.write_text(json.dumps({"messages": messages}, indent=2) + "\n")
    assert response.text == path.read_text()
