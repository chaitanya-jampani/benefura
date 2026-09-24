"""Registered Foundry prompt agents over the Responses API, normalized to chat loop events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIStatusError, AsyncOpenAI

from app.agents.base import AgentSpec
from app.chat.tool_registry import FORCED_FINAL_MAX_OUTPUT_TOKENS, tools_for
from app.config import Settings
from app.models.api import AgentName

# M0-verify: Foundry accepts `parallel_tool_calls` next to `agent_reference` (agent-framework-foundry #5130).
# Off until verified; the loop handles several calls per response either way.
SEND_PARALLEL_TOOL_CALLS_WITH_AGENT = False


@dataclass(frozen=True)
class TextDelta:
    item_id: str
    delta: str


@dataclass(frozen=True)
class FunctionCallDone:
    item_id: str
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ResponseDone:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    incomplete_reason: str | None = None


ModelEvent = TextDelta | FunctionCallDone | ResponseDone


class ContentFilterError(Exception):
    pass


class UpstreamModelError(Exception):
    """Messages never contain user content."""


@dataclass(frozen=True)
class ModelRequest:
    agent: AgentName
    input: list[dict[str, Any]]
    depth: int = 0
    force_final: bool = False  # step budget spent: answer without tools


class ModelClient(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...


def is_content_filter(exc: BaseException) -> bool:
    if not isinstance(exc, APIStatusError) or exc.status_code != 400:
        return False
    if exc.code == "content_filter":
        return True
    body = exc.body
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(inner.get("code", "")) if isinstance(inner, dict) else ""
        return code in {"content_filter", "ResponsibleAIPolicyViolation"}
    return False


def _code_of(obj: Any) -> str | None:
    error = getattr(obj, "error", None)
    code = getattr(error, "code", None) if error is not None else None
    return str(code) if code else None


class FoundryAgentModel:
    def __init__(self, client: AsyncOpenAI, settings: Settings, agents: dict[AgentName, AgentSpec]) -> None:
        self._client = client
        self._settings = settings
        self._agents = agents

    def request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        spec = self._agents[request.agent]
        kwargs: dict[str, Any] = {"input": request.input, "store": False, "stream": True}
        if request.force_final:
            # tool_choice can't go with an agent reference, so the forced final call targets the deployment.
            kwargs.update(
                model=self._settings.chat_model,
                instructions=spec.instructions,
                tools=[t.function_tool() for t in tools_for(request.agent)],
                tool_choice="none",
                parallel_tool_calls=False,
                max_output_tokens=FORCED_FINAL_MAX_OUTPUT_TOKENS,
                reasoning={"effort": self._settings.reasoning_effort},
            )
            return kwargs
        reference: dict[str, str] = {"type": "agent_reference", "name": spec.name}
        version = spec.version(self._settings)
        if version:
            reference["version"] = version
        # M0-verify: prompt agent invocation with store=False, stream=True and replayed function_call items.
        kwargs["extra_body"] = {"agent_reference": reference}
        if SEND_PARALLEL_TOOL_CALLS_WITH_AGENT:
            kwargs["parallel_tool_calls"] = False
        return kwargs

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        kwargs = self.request_kwargs(request)
        model_name = kwargs.get("model") or self._agents[request.agent].name
        try:
            events = await self._client.responses.create(**kwargs)
            async for event in events:  # type: ignore[union-attr]
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    yield TextDelta(item_id=event.item_id, delta=event.delta)
                elif kind == "response.output_item.done" and getattr(event.item, "type", "") == "function_call":
                    item = event.item
                    yield FunctionCallDone(
                        item_id=item.id or item.call_id, call_id=item.call_id, name=item.name, arguments=item.arguments
                    )
                elif kind in ("response.completed", "response.incomplete"):
                    response = event.response
                    reason = (
                        getattr(response.incomplete_details, "reason", None) if response.incomplete_details else None
                    )
                    if reason == "content_filter":
                        raise ContentFilterError("response filtered")
                    usage = response.usage
                    yield ResponseDone(
                        model=response.model or model_name,
                        input_tokens=usage.input_tokens if usage else 0,
                        output_tokens=usage.output_tokens if usage else 0,
                        incomplete_reason=reason,
                    )
                elif kind == "response.failed":
                    code = _code_of(event.response)
                    if code in {"content_filter", "ResponsibleAIPolicyViolation"}:
                        raise ContentFilterError("response failed: content filter")
                    raise UpstreamModelError(f"response failed: {code or 'unknown'}")
                elif kind == "error":
                    code = getattr(event, "code", None)
                    if code in {"content_filter", "ResponsibleAIPolicyViolation"}:
                        raise ContentFilterError("stream error: content filter")
                    raise UpstreamModelError(f"stream error: {code or 'unknown'}")
        except APIStatusError as exc:
            if is_content_filter(exc):
                raise ContentFilterError("request filtered") from exc
            raise UpstreamModelError(f"model call failed with HTTP {exc.status_code}") from exc
