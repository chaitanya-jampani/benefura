from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agent_framework.exceptions import AgentContentFilterException, ChatClientContentFilterException
from fastapi.testclient import TestClient

from app.errors import ContentFilteredError, is_content_filter_error


def test_openapi_is_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import export_openapi

    monkeypatch.setattr("sys.argv", ["export_openapi", "--check"])
    assert export_openapi.main() == 0, "run: uv run python -m scripts.export_openapi"


def test_every_response_gets_a_distinct_real_trace_id(client: TestClient) -> None:
    ids = {client.get("/healthz").headers["x-benefura-trace-id"] for _ in range(3)}
    assert len(ids) == 3 and all(len(i) == 32 and int(i, 16) for i in ids)


def test_metric_attributes_carry_categories_not_text() -> None:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from app import telemetry

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkey_meter = provider.get_meter(telemetry.METER_NAME)
    telemetry._instruments.cache_clear()
    original = metrics.get_meter
    try:
        metrics.get_meter = lambda *a, **k: monkey_meter  # type: ignore[assignment]
        telemetry.record_pii_hits({"CASocialInsuranceNumber": 2, "Person": 0}, "hard", "booklet")
        telemetry.record_safety_event("prompt_injection", "receipt")
        telemetry.record_budget_spend("demo-chat", 0.01)
        telemetry.record_search(12, 0, None)
    finally:
        metrics.get_meter = original  # type: ignore[assignment]
        telemetry._instruments.cache_clear()
    data = reader.get_metrics_data()
    points: dict[str, list[dict[str, Any]]] = {}
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points[metric.name] = [dict(p.attributes) for p in metric.data.data_points]
    # The Bicep workbook and alerts query these names and attribute keys.
    assert points["benefura.pii.hits"] == [
        {"benefura.pii_category": "CASocialInsuranceNumber", "benefura.pii_tier": "hard", "benefura.surface": "booklet"}
    ]
    assert points["benefura.safety.events"] == [
        {"benefura.safety_kind": "prompt_injection", "benefura.surface": "receipt"}
    ]
    assert points["benefura.search.queries"] == [{"benefura.zero_results": "true"}]
    assert points["benefura.budget.spend_usd"] == [{"benefura.namespace": "demo-chat"}]
    assert {"benefura.step": "search"} in points["benefura.step.duration"]


def test_content_filter_detection_variants() -> None:
    from azure.core.exceptions import HttpResponseError
    from openai import BadRequestError

    request = httpx.Request("POST", "https://example.invalid/openai/v1/responses")
    openai_error = BadRequestError(
        "blocked",
        response=httpx.Response(400, request=request),
        body={"code": "content_filter", "message": "blocked", "innererror": {"code": "ResponsibleAIPolicyViolation"}},
    )
    wrapped = RuntimeError("workflow failed")
    wrapped.__cause__ = ChatClientContentFilterException("content_filter")
    azure_error = HttpResponseError(message="blocked")
    azure_error.error = SimpleNamespace(code="ResponsibleAIPolicyViolation")  # type: ignore[assignment]

    assert is_content_filter_error(openai_error)
    assert is_content_filter_error(wrapped)
    assert is_content_filter_error(AgentContentFilterException("x"))
    assert is_content_filter_error(ContentFilteredError("x"))
    assert is_content_filter_error(azure_error)
    assert not is_content_filter_error(RuntimeError("boom"))
    assert not is_content_filter_error(HttpResponseError(message="throttled"))
