"""OpenTelemetry → Application Insights; content recording stays off and attributes never carry user text."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from time import perf_counter
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer

from app.config import VERSION, get_settings

logger = logging.getLogger("benefura.telemetry")

SERVICE_NAME = "benefura-api"
METER_NAME = "benefura.api"


def get_tracer() -> Tracer:
    return trace.get_tracer("benefura.api")


def current_trace_id() -> str:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.trace_id else "0" * 32


def ensure_tracer_provider() -> None:
    """Exporter-less fallback so requests still get a real ``x-benefura-trace-id`` without App Insights."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        from opentelemetry.sdk.resources import Resource

        trace.set_tracer_provider(
            TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME, "service.version": VERSION}))
        )


async def configure_telemetry() -> None:
    """Never raises: telemetry must not take the API down."""
    settings = get_settings()
    connection_string = settings.applicationinsights_connection_string
    if not connection_string and settings.ai_mode == "live" and settings.foundry_project_endpoint:
        try:
            from app.services.foundry import get_project_client

            connection_string = await get_project_client().telemetry.get_application_insights_connection_string()
        except Exception as exc:
            logger.warning("App Insights connection string unavailable (%s); traces stay local", type(exc).__name__)

    if connection_string:
        try:
            _configure_azure_monitor(connection_string)
        except Exception as exc:
            logger.warning("Azure Monitor setup failed (%s); traces stay local", type(exc).__name__)
    ensure_tracer_provider()
    _instrument_ai_clients()


def _configure_azure_monitor(connection_string: str) -> None:
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.sdk.resources import Resource

    configure_azure_monitor(
        connection_string=connection_string,
        # M0-verify: if App Insights local auth is disabled, pass credential= and grant Monitoring Metrics Publisher.
        # 100%: a sampled-out trace would make the x-benefura-trace-id header (and the smoke test) lie.
        sampling_ratio=1.0,
        resource=Resource.create({"service.name": SERVICE_NAME, "service.version": VERSION}),
        # FastAPI is instrumented on the app instance in create_app(); avoid a second server span.
        instrumentation_options={"fastapi": {"enabled": False}},
        enable_live_metrics=False,
    )


def _instrument_ai_clients() -> None:
    # M0-verify: AIProjectInstrumentor still gates on this experimental flag in azure-ai-projects 2.6.
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    try:
        from azure.ai.projects.telemetry import AIProjectInstrumentor

        AIProjectInstrumentor().instrument(enable_content_recording=False)
    except Exception as exc:
        logger.warning("AIProjectInstrumentor unavailable (%s)", type(exc).__name__)
    try:
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation(enable_sensitive_data=False)
    except Exception as exc:
        logger.warning("Agent Framework instrumentation unavailable (%s)", type(exc).__name__)


def instrument_fastapi(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


class _Instruments:
    """The Bicep workbook and alerts query these names and ``benefura.*`` attribute keys in ``customMetrics``."""

    def __init__(self) -> None:
        meter = metrics.get_meter(METER_NAME, VERSION)
        self.model_tokens: Counter = meter.create_counter("benefura.model.tokens", unit="{token}")
        self.model_ms: Histogram = meter.create_histogram("benefura.model.duration", unit="ms")
        self.step_ms: Histogram = meter.create_histogram("benefura.step.duration", unit="ms")
        self.router_decisions: Counter = meter.create_counter("benefura.router.decisions", unit="{decision}")
        self.tool_calls: Counter = meter.create_counter("benefura.tool.calls", unit="{call}")
        self.tool_ms: Histogram = meter.create_histogram("benefura.tool.duration", unit="ms")
        self.safety_events: Counter = meter.create_counter("benefura.safety.events", unit="{event}")
        self.pii_hits: Counter = meter.create_counter("benefura.pii.hits", unit="{hit}")
        self.search_queries: Counter = meter.create_counter("benefura.search.queries", unit="{query}")
        self.search_ms: Histogram = meter.create_histogram("benefura.search.duration", unit="ms")
        self.search_top_score: Histogram = meter.create_histogram("benefura.search.top_score", unit="1")
        self.verifier_verdicts: Counter = meter.create_counter("benefura.verifier.verdicts", unit="{row}")
        self.budget_spend: Counter = meter.create_counter("benefura.budget.spend_usd", unit="USD")
        self.cu_pages: Counter = meter.create_counter("benefura.cu.pages", unit="{page}")


@lru_cache
def _instruments() -> _Instruments:
    return _Instruments()


_REJECTED_TOOL_CODES = frozenset({"tool_not_allowed", "nesting_too_deep", "step_limit"})


def record_router_decision(route: str, ms: int) -> None:
    _instruments().router_decisions.add(1, {"benefura.route": route})
    record_step_latency("router", ms)


def record_tool_call(name: str, executor: str, ms: int, error: str | None = None) -> None:
    if error is None:
        outcome = "ok"
    elif error in _REJECTED_TOOL_CODES:
        outcome = "rejected"
    elif error.startswith("declined"):
        outcome = "declined"
    else:
        outcome = "error"
    base = {"benefura.tool": name, "benefura.executor": executor}
    _instruments().tool_calls.add(1, {**base, "benefura.outcome": outcome, "benefura.error": error or "none"})
    _instruments().tool_ms.record(ms, base)


def record_safety_event(kind: str, surface: str) -> None:
    attrs = {"benefura.safety_kind": kind, "benefura.surface": surface}
    _instruments().safety_events.add(1, attrs)
    trace.get_current_span().add_event("benefura.safety_event", attrs)


def record_pii_hits(categories: dict[str, int], tier: str, surface: str) -> None:
    for category, count in categories.items():
        if count:
            _instruments().pii_hits.add(
                count, {"benefura.pii_category": category, "benefura.pii_tier": tier, "benefura.surface": surface}
            )


def record_search(latency_ms: int, hits: int, top_score: float | None) -> None:
    _instruments().search_queries.add(1, {"benefura.zero_results": "true" if hits == 0 else "false"})
    _instruments().search_ms.record(latency_ms)
    record_step_latency("search", latency_ms)
    if top_score is not None:
        _instruments().search_top_score.record(top_score)


def record_model_usage(model: str, agent: str | None, input_tokens: int, output_tokens: int, ms: int) -> None:
    attrs = {"benefura.model": model, "benefura.agent": agent or "none"}
    _instruments().model_tokens.add(input_tokens, {**attrs, "benefura.token_type": "input"})
    _instruments().model_tokens.add(output_tokens, {**attrs, "benefura.token_type": "output"})
    _instruments().model_ms.record(ms, attrs)
    record_step_latency("model", ms)


def record_step_latency(step: str, ms: int) -> None:
    _instruments().step_ms.record(ms, {"benefura.step": step})


def record_budget_spend(namespace: str, usd: float) -> None:
    if usd > 0:
        _instruments().budget_spend.add(usd, {"benefura.namespace": namespace})


def record_cu_pages(analyzer: str, pages: int) -> None:
    _instruments().cu_pages.add(pages, {"benefura.analyzer": analyzer})


def record_verifier_verdicts(verdicts: dict[str, int]) -> None:
    for verdict, count in verdicts.items():
        if count:
            _instruments().verifier_verdicts.add(count, {"benefura.verdict": verdict})


@contextmanager
def timed_span(step: str, **attributes: str | int | float | bool) -> Iterator[dict[str, int]]:
    """Yields a dict that receives ``ms`` on exit."""
    out: dict[str, int] = {}
    started = perf_counter()
    with get_tracer().start_as_current_span(f"benefura.{step}", attributes={"benefura.step": step, **attributes}):
        try:
            yield out
        finally:
            out["ms"] = int((perf_counter() - started) * 1000)
            record_step_latency(step, out["ms"])
