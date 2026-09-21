from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableEntity
from starlette.requests import HTTPConnection

from app.budget import (
    PRICING,
    InMemoryBudget,
    TableBudget,
    UsageTracker,
    estimate_cost_usd,
    namespace_for,
    seconds_until_utc_midnight,
)
from app.errors import ApiError
from app.limits import InMemoryCapStore, RateLimiter, TableCapStore, client_ip
from app.models.api import Usage


class FakeTable:
    """Just enough of the async TableClient: ETags, 404/409/412 like the service."""

    def __init__(self, *, conflicts: int = 0) -> None:
        self.rows: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
        self.conflicts = conflicts
        self.updates = 0

    async def get_entity(self, partition_key: str, row_key: str) -> TableEntity:
        if (partition_key, row_key) not in self.rows:
            raise ResourceNotFoundError("missing")
        data, version = self.rows[(partition_key, row_key)]
        entity = TableEntity(data)
        entity._metadata = {"etag": f"W/{version}", "timestamp": None}
        return entity

    async def create_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.rows:
            raise ResourceExistsError("exists")
        self.rows[key] = (dict(entity), 1)
        return entity

    async def update_entity(self, entity: dict[str, Any], *, mode: Any, etag: str, match_condition: Any) -> None:
        assert match_condition == MatchConditions.IfNotModified
        key = (entity["PartitionKey"], entity["RowKey"])
        data, version = self.rows[key]
        if self.conflicts:
            self.conflicts -= 1
            self.rows[key] = (data, version + 1)  # someone else wrote first
            raise ResourceModifiedError("412")
        if etag != f"W/{version}":
            raise ResourceModifiedError("412")
        self.updates += 1
        self.rows[key] = ({**data, **entity}, version + 1)

    async def close(self) -> None:
        return None


def test_estimate_cost_prices_each_model_and_record() -> None:
    usage = Usage(cuPages=10, languageRecords=20, contentSafetyRecords=4)
    cost = estimate_cost_usd(usage, model_tokens={"gpt-5-mini": (100_000, 10_000), "gpt-5-nano": (50_000, 0)})
    mini_in, mini_out = PRICING.models["gpt-5-mini"]
    nano_in, _ = PRICING.models["gpt-5-nano"]
    expected = (
        100_000 * mini_in
        + 10_000 * mini_out
        + 50_000 * nano_in
        + 10 * PRICING.cu_page
        + 20 * PRICING.language_record
        + 4 * PRICING.content_safety_record
    )
    assert cost == pytest.approx(expected)
    assert estimate_cost_usd(Usage(inputTokens=1_000_000)) == pytest.approx(mini_in * 1_000_000)


def test_usage_tracker_accumulates() -> None:
    tracker = UsageTracker()
    tracker.add_tokens("gpt-5-mini", 1000, 100, agent="Extractor", ms=5)
    tracker.add_tokens("gpt-5-mini", 500, 50, agent="Verifier", ms=5)
    tracker.cu_pages = 5
    with tracker.step("cu_layout"):
        pass
    usage = tracker.to_usage()
    assert (usage.inputTokens, usage.outputTokens, usage.cuPages) == (1500, 150, 5)
    assert "cu_layout" in usage.byStepMs and usage.estimatedCostUsd > 0


async def test_in_memory_budget_is_per_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DAILY_BUDGET_USD_EXTRACTION", "1.0")
    monkeypatch.setenv("DAILY_BUDGET_USD_CHAT", "2.0")
    get_settings.cache_clear()
    budget = InMemoryBudget()
    await budget.charge("demo-extraction", 0.25)
    assert await budget.remaining_pct("demo-extraction") == 75.0
    await budget.charge("demo-extraction", 0.80)
    with pytest.raises(ApiError) as caught:
        await budget.ensure_available("demo-extraction")
    assert caught.value.code == "budget_exhausted" and caught.value.status == 429
    assert 0 < (caught.value.retry_after or 0) <= 86400
    await budget.ensure_available("demo-chat")  # a scripted upload burst cannot silence chat
    await budget.ensure_available("evals")
    assert await budget.remaining_pct("demo-extraction") == 0.0
    assert await budget.remaining_pct("demo-chat") == 100.0


async def test_table_budget_retries_on_etag_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DAILY_BUDGET_USD_CHAT", "2.0")
    get_settings.cache_clear()
    table = FakeTable()
    budget = TableBudget(table)  # type: ignore[arg-type]
    await budget.charge("demo-chat", 0.5)
    table.conflicts = 2
    await budget.charge("demo-chat", 0.25)
    today = datetime.now(UTC).date().isoformat()
    assert table.rows[("budget-demo-chat", today)][0]["spentUsd"] == pytest.approx(0.75)
    assert await budget.remaining_pct("demo-chat") == pytest.approx(62.5)


async def test_table_budget_fails_closed_when_ledger_is_down() -> None:
    class Broken(FakeTable):
        async def get_entity(self, partition_key: str, row_key: str) -> TableEntity:
            raise ConnectionError("down")

    with pytest.raises(ApiError) as caught:
        await TableBudget(Broken()).ensure_available("demo-extraction")  # type: ignore[arg-type]
    assert caught.value.code == "upstream_error"


def test_seconds_until_midnight() -> None:
    assert seconds_until_utc_midnight(datetime(2026, 9, 16, 23, 59, 0, tzinfo=UTC)) == 60


@pytest.mark.parametrize(
    ("secret", "header", "key", "expected"),
    [
        ("", "evals", "", "demo-chat"),
        ("", "evals", "anything", "demo-chat"),
        ("s3cret", "evals", "wrong", "demo-chat"),
        ("s3cret", "evals", None, "demo-chat"),
        ("s3cret", "demo-extraction", "s3cret", "demo-chat"),
        ("s3cret", "evals", "s3cret", "evals"),
    ],
)
def test_evals_namespace_requires_shared_secret(
    monkeypatch: pytest.MonkeyPatch, secret: str, header: str, key: str | None, expected: str
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("EVALS_SHARED_SECRET", secret)
    get_settings.cache_clear()
    assert namespace_for(header, "demo-chat", evals_key=key) == expected


def test_evals_namespace_over_http(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.budget import get_budget
    from app.config import get_settings
    from tests.conftest import image_pdf

    monkeypatch.setenv("EVALS_SHARED_SECRET", "s3cret")
    get_settings.cache_clear()
    response = client.post(
        "/api/plan/analyze-chunk",
        data={"documentId": "evals-booklet-1", "region": "CA", "pages": "1,2"},
        files={"file": ("c.pdf", image_pdf(2), "application/pdf")},
        headers={"x-benefura-budget-namespace": "evals", "x-benefura-evals-key": "s3cret"},
    )
    assert response.status_code == 200
    budget = get_budget()
    assert isinstance(budget, InMemoryBudget)
    today = datetime.now(UTC).date().isoformat()
    assert budget._spent.get(("evals", today), 0) > 0
    assert ("demo-extraction", today) not in budget._spent


def connection(headers: dict[str, str], peer: str = "10.1.1.1") -> HTTPConnection:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return HTTPConnection({"type": "http", "headers": raw, "client": (peer, 1234)})


def test_client_ip_hops(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    forwarded = {"X-Forwarded-For": "198.51.100.4, 10.0.0.2"}
    assert client_ip(connection(forwarded)) == "198.51.100.4"
    assert client_ip(connection({})) == "10.1.1.1"
    monkeypatch.setenv("CLIENT_IP_HOP", "last")
    get_settings.cache_clear()
    assert client_ip(connection(forwarded)) == "10.0.0.2"


def test_rate_limiter_sliding_window() -> None:
    limiter = RateLimiter(3)
    for t in (0.0, 1.0, 2.0):
        limiter.check("ip", now=t)
    with pytest.raises(ApiError) as caught:
        limiter.check("ip", now=30.0)
    assert caught.value.code == "rate_limited" and caught.value.retry_after == 30
    limiter.check("other", now=30.0)
    limiter.check("ip", now=60.5)  # the first hit left the window


@pytest.mark.parametrize("store_kind", ["memory", "table"])
async def test_booklet_and_page_caps(store_kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAX_BOOKLETS_PER_IP_PER_DAY", "2")
    monkeypatch.setenv("MAX_BOOKLET_PAGES", "10")
    get_settings.cache_clear()
    store = InMemoryCapStore() if store_kind == "memory" else TableCapStore(FakeTable())  # type: ignore[arg-type]

    await store.register_chunk("1.1.1.1", "doc-a", [1, 2, 3, 4, 5])
    await store.register_chunk("1.1.1.1", "doc-a", [5, 6, 7, 8, 9])  # same booklet: no new slot
    await store.register_chunk("1.1.1.1", "doc-b", [1])
    with pytest.raises(ApiError) as booklet:
        await store.register_chunk("1.1.1.1", "doc-c", [1])
    assert booklet.value.code == "booklet_cap_exceeded" and booklet.value.status == 429
    await store.register_chunk("2.2.2.2", "doc-c", [1])  # another caller has its own cap

    with pytest.raises(ApiError) as page_number:
        await store.register_chunk("1.1.1.1", "doc-a", [11])
    assert page_number.value.code == "page_cap_exceeded" and page_number.value.status == 413

    await store.register_chunk("1.1.1.1", "doc-b", [2, 3, 4, 5, 6])
    await store.register_chunk("1.1.1.1", "doc-b", [6, 7, 8, 9, 10])
    await store.register_chunk("1.1.1.1", "doc-b", [2, 3, 4, 5, 6])  # retries count toward the 2× budget
    with pytest.raises(ApiError) as retries:
        await store.register_chunk("1.1.1.1", "doc-b", [2, 3, 4, 5, 6])
    assert retries.value.code == "page_cap_exceeded"


async def test_table_caps_store_digests_not_raw_values() -> None:
    table = FakeTable()
    await TableCapStore(table).register_chunk("203.0.113.7", "my-document-id", [1, 2])  # type: ignore[arg-type]
    stored = repr(table.rows)
    assert "203.0.113.7" not in stored and "my-document-id" not in stored
