"""``POST /api/chat``: PII check, router, then the agent loop; tool continuations skip straight to the loop."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from opentelemetry import trace

from app import budget as shared_budget
from app import telemetry
from app.agents import knowledge, plan_claims
from app.agents.base import AgentSpec
from app.chat import ui_stream
from app.chat.agent_loop import CONTENT_FILTER_REFUSAL, AgentOutcome, TurnRuntime, context_item, run_agent
from app.chat.guards import (
    PII_WARNING_MESSAGE,
    Budget,
    LanguagePiiChecker,
    PiiChecker,
    categories,
)
from app.chat.history import ReplayedHistory, parse_tool_part, replay
from app.chat.model import ContentFilterError, FoundryAgentModel, ModelClient
from app.chat.router import OFF_TOPIC_REFUSAL, NanoRouter, Router, agent_for
from app.chat.server_tools import SearchFn
from app.chat.tool_registry import StepBudget
from app.config import Settings, get_settings
from app.errors import ApiError
from app.limits import RateLimiter, enforce_rate_limit, get_rate_limiter
from app.models.api import AgentName, BudgetNamespace, ChatRequest, ErrorResponse, Usage

logger = logging.getLogger(__name__)

TRACE_HEADER = "x-benefura-trace-id"
FAKE_CONTENT_FILTER_HEADER = "x-benefura-fake-content-filter"
BUDGET_NAMESPACE_HEADER = "x-benefura-budget-namespace"
UNAVAILABLE_MESSAGE = "The assistant is unavailable right now. Please try again in a moment."

AGENTS: dict[AgentName, AgentSpec] = {"plan_claims": plan_claims.AGENT, "knowledge": knowledge.AGENT}

router = APIRouter()


@dataclass
class ChatServices:
    settings: Settings
    router: Router
    model: ModelClient
    search: SearchFn
    pii: PiiChecker
    budget: Budget
    namespace: BudgetNamespace
    id_factory: Callable[[str], str] = field(default=lambda prefix: f"{prefix}_{secrets.token_hex(8)}")
    trace_id_factory: Callable[[], str] | None = None


def get_chat_services(request: Request) -> ChatServices:
    settings = get_settings()
    if not settings.ai_enabled:
        raise ApiError("ai_disabled", "AI features are turned off for this deployment.")
    namespace = shared_budget.namespace_from_request(request, "demo-chat")
    if settings.ai_mode == "fake":
        from app.chat import fakes

        return ChatServices(
            settings=settings,
            router=fakes.FakeRouter(),
            model=fakes.FakeModel(content_filter=request.headers.get(FAKE_CONTENT_FILTER_HEADER) == "1"),
            search=fakes.fake_search,
            pii=fakes.FakePiiChecker(),
            budget=shared_budget.get_budget(),
            namespace=namespace,
        )

    from app.services.foundry import get_openai_client
    from app.services.search import hybrid_search

    client = get_openai_client()
    return ChatServices(
        settings=settings,
        router=NanoRouter(client, settings),
        model=FoundryAgentModel(client, settings, AGENTS),
        search=hybrid_search,
        pii=LanguagePiiChecker(),
        budget=shared_budget.get_budget(),
        namespace=namespace,
    )


def _new_trace_id(services: ChatServices, span: trace.Span) -> str:
    if services.trace_id_factory is not None:
        return services.trace_id_factory()
    trace_id = span.get_span_context().trace_id
    return format(trace_id, "032x") if trace_id else secrets.token_hex(16)


def _current_turn_has_tools(messages: list[dict[str, Any]]) -> bool:
    last = messages[-1] if messages else {}
    parts = last.get("parts") if isinstance(last, dict) else None
    return isinstance(parts, list) and any(isinstance(p, dict) and parse_tool_part(p) for p in parts)


def rate_limit(request: Request, limiter: RateLimiter = Depends(get_rate_limiter)) -> None:  # noqa: B008
    enforce_rate_limit(request, limiter)


@router.post(
    "/api/chat",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "AI SDK UI message stream (v1)."},
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    # Body size is capped by GuardMiddleware; the rate limit is idempotent per request.
    dependencies=[Depends(rate_limit)],
)
async def chat(
    request: Request,
    body: ChatRequest,
    services: ChatServices = Depends(get_chat_services),  # noqa: B008
) -> StreamingResponse:
    await services.budget.ensure_available(services.namespace)

    history = replay(body.messages)
    if history.is_continuation and not _current_turn_has_tools(body.messages):
        raise ApiError("invalid_request", "Nothing to continue: the last message has no tool results.")
    if not history.is_continuation and not history.latest_user_text:
        raise ApiError("invalid_request", "The latest message has no text.")

    span = telemetry.get_tracer().start_span("chat.turn")
    trace_id = _new_trace_id(services, span)
    request.state.trace_id = trace_id
    headers = {**ui_stream.UI_MESSAGE_STREAM_HEADERS, TRACE_HEADER: trace_id}
    return StreamingResponse(
        _encode(stream_turn(body, history, services, trace_id), span),
        headers=headers,
        media_type="text/event-stream",
    )


async def _encode(chunks: AsyncIterator[ui_stream.Chunk], span: trace.Span) -> AsyncIterator[str]:
    with trace.use_span(span, end_on_exit=True):
        try:
            async for chunk in chunks:
                yield ui_stream.encode(chunk)
        except Exception as exc:  # noqa: BLE001 - the stream has started; report inside it, never log content
            logger.error("chat stream failed: %s", type(exc).__name__)
            span.record_exception(exc)
            yield ui_stream.encode(ui_stream.error(UNAVAILABLE_MESSAGE))
        yield ui_stream.DONE_FRAME


def _metadata(agent: AgentName | None, services: ChatServices, trace_id: str, **extra: Any) -> dict[str, Any]:
    spec = AGENTS[agent] if agent else None
    version = spec.version(services.settings) if spec else None
    if spec and version is None and services.settings.ai_mode == "fake":
        version = "fake"
    return {
        "agent": agent,
        "agentName": spec.name if spec else None,
        "agentVersion": version,
        "traceId": trace_id,
        **extra,
    }


async def stream_turn(
    body: ChatRequest, history: ReplayedHistory, services: ChatServices, trace_id: str
) -> AsyncIterator[ui_stream.Chunk]:
    runtime = TurnRuntime(
        model=services.model,
        search=services.search,
        context=body.context,
        budget=StepBudget(services.settings.max_tool_steps, used=history.tool_steps_used),
        declined_signatures=set(history.declined_signatures),
        id_factory=services.id_factory,
    )
    language_records = 0
    try:
        if history.is_continuation:
            agent: AgentName = body.activeAgent or history.last_agent or "plan_claims"
            yield ui_stream.start(metadata=_metadata(agent, services, trace_id))
            for call_id in history.newly_denied_call_ids:
                yield ui_stream.tool_output_denied(call_id)
        else:
            text = history.latest_user_text or ""
            message_id = services.id_factory("msg")

            pii = await services.pii.check(text, body.context.region)
            language_records += pii.records
            if pii.advisory:
                telemetry.record_pii_hits(categories(pii.advisory), "advisory", "chat")
            if pii.hard:
                found = categories(pii.hard)
                telemetry.record_pii_hits(found, "hard", "chat")
                yield ui_stream.start(message_id, _metadata(None, services, trace_id, blocked="pii"))
                yield ui_stream.data("pii-warning", {"categories": sorted(found), "message": PII_WARNING_MESSAGE})
                yield ui_stream.finish("stop")
                return

            try:
                started = time.monotonic()
                decision = await services.router.classify(text, history.items, body.context)
                telemetry.record_router_decision(decision.route, int((time.monotonic() - started) * 1000))
                if decision.model:
                    runtime.usage.add(decision.model, decision.input_tokens, decision.output_tokens)
            except ContentFilterError:
                telemetry.record_safety_event("content_filter", "chat_router")
                yield ui_stream.start(
                    message_id, _metadata(None, services, trace_id, route=None, blocked="content_filter")
                )
                for chunk in ui_stream.text(services.id_factory("text"), CONTENT_FILTER_REFUSAL):
                    yield chunk
                yield ui_stream.finish("content-filter")
                return

            routed = agent_for(decision.route)
            if routed is None:
                yield ui_stream.start(message_id, _metadata(None, services, trace_id, route="off_topic"))
                for chunk in ui_stream.text(services.id_factory("text"), OFF_TOPIC_REFUSAL):
                    yield chunk
                yield ui_stream.finish("stop")
                return
            agent = routed
            yield ui_stream.start(message_id, _metadata(agent, services, trace_id, route=decision.route))

        outcome = AgentOutcome(finish="stop")
        async for event in run_agent(agent, [context_item(body.context), *history.items], runtime):
            if isinstance(event, AgentOutcome):
                outcome = event
            else:
                yield event

        if outcome.finish == "content-filter":
            yield ui_stream.finish("content-filter", {"blocked": "content_filter"})
        else:
            yield ui_stream.finish(outcome.finish)
    finally:
        await _charge(services, runtime, language_records)


async def _charge(services: ChatServices, runtime: TurnRuntime, language_records: int) -> None:
    if services.settings.ai_mode != "live":
        return
    try:
        usage = Usage(
            inputTokens=runtime.usage.input_tokens,
            outputTokens=runtime.usage.output_tokens,
            languageRecords=language_records,
        )
        usd = shared_budget.estimate_cost_usd(usage, model_tokens=runtime.usage.by_model)
        await services.budget.charge(services.namespace, usd)
        telemetry.record_budget_spend(services.namespace, usd)
    except Exception as exc:  # noqa: BLE001 - accounting must never break a finished answer
        logger.warning("chat budget charge failed: %s", type(exc).__name__)
