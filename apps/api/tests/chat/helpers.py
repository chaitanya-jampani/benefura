from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat import fakes
from app.chat.guards import UnmeteredBudget
from app.chat.model import ModelEvent, ModelRequest
from app.chat.route import ChatServices, get_chat_services, router
from app.config import Settings
from app.errors import ApiError, api_error_handler
from app.limits import RateLimiter, get_rate_limiter
from app.models.api import ChatContext

CONTEXT: dict[str, Any] = {
    "region": "CA",
    "today": "2026-09-16",
    "tz": "America/Toronto",
    "currency": "CAD",
    "planName": "Group Extended Health and Dental, Class A",
    "memberAliases": ["[MEMBER_A]", "[MEMBER_B]"],
    "categories": ["Paramedical practitioners", "Vision care"],
}
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def chat_context() -> ChatContext:
    return ChatContext.model_validate(CONTEXT)


def counter_ids() -> Callable[[str], str]:
    counter = itertools.count(1)
    return lambda prefix: f"{prefix}_{next(counter)}"


def user(text: str, message_id: str = "user-1") -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def parse_sse(body: str) -> list[dict[str, Any]]:
    chunks = []
    for frame in body.split("\n\n"):
        if not frame.startswith("data: "):
            continue
        data = frame[len("data: ") :]
        if data == "[DONE]":
            continue
        chunks.append(json.loads(data))
    return chunks


@dataclass
class SpyRouter:
    inner: Any = field(default_factory=fakes.FakeRouter)
    calls: int = 0

    async def classify(self, text: str, history: list[dict[str, Any]], context: ChatContext) -> Any:
        self.calls += 1
        return await self.inner.classify(text, history, context)


@dataclass
class SpyModel:
    inner: Any = field(default_factory=fakes.FakeModel)
    requests: list[ModelRequest] = field(default_factory=list)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        async for event in self.inner.stream(request):
            yield event


@dataclass
class ChatHarness:
    client: TestClient
    services: ChatServices
    router: SpyRouter
    model: SpyModel

    def post(
        self, messages: list[dict[str, Any]], *, active_agent: str | None = None, headers: dict[str, str] | None = None
    ):
        return self.client.post(
            "/api/chat",
            json={"messages": messages, "context": CONTEXT, "activeAgent": active_agent},
            headers=headers or {},
        )


def build_harness(*, content_filter: bool = False, ai_mode: str = "fake", per_minute: int = 100) -> ChatHarness:
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(router)
    spy_router = SpyRouter()
    spy_model = SpyModel(inner=fakes.FakeModel(content_filter=content_filter))
    services = ChatServices(
        settings=Settings(ai_mode=ai_mode),  # type: ignore[arg-type]
        router=spy_router,
        model=spy_model,
        search=fakes.fake_search,
        pii=fakes.FakePiiChecker(),
        budget=UnmeteredBudget(),
        namespace="demo-chat",
        id_factory=counter_ids(),
        trace_id_factory=lambda: TRACE_ID,
    )

    def services_dep() -> ChatServices:
        if not services.settings.ai_enabled:
            raise ApiError("ai_disabled", "AI features are turned off for this deployment.")
        return services

    limiter = RateLimiter(per_minute)
    app.dependency_overrides[get_chat_services] = services_dep
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    return ChatHarness(TestClient(app), services, spy_router, spy_model)


def assistant_from_chunks(
    chunks: list[dict[str, Any]], *, message_id: str = "assistant-1", base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Minimal reimplementation of the AI SDK's message assembly, enough to build continuation requests."""
    message = base or {"id": message_id, "role": "assistant", "parts": [], "metadata": {}}
    texts: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        kind = chunk["type"]
        if kind == "start" and chunk.get("messageMetadata"):
            message["metadata"] = {**message.get("metadata", {}), **chunk["messageMetadata"]}
        elif kind == "start-step":
            message["parts"].append({"type": "step-start"})
        elif kind == "text-start":
            part = {"type": "text", "text": "", "state": "streaming"}
            texts[chunk["id"]] = part
            message["parts"].append(part)
        elif kind == "text-delta":
            texts[chunk["id"]]["text"] += chunk["delta"]
        elif kind == "text-end":
            texts[chunk["id"]]["state"] = "done"
        elif kind == "tool-input-available":
            message["parts"].append(
                {
                    "type": f"tool-{chunk['toolName']}",
                    "toolCallId": chunk["toolCallId"],
                    "state": "input-available",
                    "input": chunk["input"],
                    **({"providerExecuted": True} if chunk.get("providerExecuted") else {}),
                }
            )
        elif kind == "tool-approval-request":
            part = next(p for p in message["parts"] if p.get("toolCallId") == chunk["toolCallId"])
            part["state"] = "approval-requested"
            part["approval"] = {"id": chunk["approvalId"]}
        elif kind == "tool-output-available":
            part = next(p for p in message["parts"] if p.get("toolCallId") == chunk["toolCallId"])
            part["state"] = "output-available"
            part["output"] = chunk["output"]
        elif kind == "tool-output-denied":
            part = next(p for p in message["parts"] if p.get("toolCallId") == chunk["toolCallId"])
            part["state"] = "output-denied"
        elif kind.startswith("data-"):
            message["parts"].append(
                {"type": kind, "data": chunk["data"], **({"id": chunk["id"]} if "id" in chunk else {})}
            )
        elif kind == "finish" and chunk.get("messageMetadata"):
            message["metadata"] = {**message.get("metadata", {}), **chunk["messageMetadata"]}
    return message


def set_tool_output(message: dict[str, Any], tool_call_id: str, output: Any) -> None:
    part = next(p for p in message["parts"] if p.get("toolCallId") == tool_call_id)
    part["state"] = "output-available"
    part["output"] = output


FIND_MASSAGE_OUTPUT = {
    "query": "massage",
    "benefits": [
        {
            "benefitId": "ben-massage",
            "name": "Massage therapy",
            "category": "Paramedical practitioners",
            "coverage": "80% up to $80.00 per visit",
            "limits": ["$500.00 per person per benefit year"],
            "pool": "Paramedical combined maximum",
            "source": {"page": 5, "quote": "Massage therapy (registered massage therapist): 80% up to $80 per visit"},
        }
    ],
}

USAGE_OUTPUT = {
    "asOf": "2026-09-16",
    "usage": [
        {
            "benefitId": "ben-massage",
            "name": "Massage therapy",
            "memberAlias": "[MEMBER_A]",
            "remainingCents": 42000,
            "usedCents": 8000,
        }
    ],
}
