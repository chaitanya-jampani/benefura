"""Daily USD spend guard; namespaces are separate so an eval run or upload burst can't lock out chat."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol, get_args

from app.config import Settings, get_settings
from app.errors import ApiError
from app.models.api import BudgetNamespace, Usage
from app.telemetry import record_budget_spend, record_model_usage, timed_span

if TYPE_CHECKING:
    from azure.data.tables.aio import TableClient
    from fastapi import Request

logger = logging.getLogger("benefura.budget")

NAMESPACES: tuple[BudgetNamespace, ...] = get_args(BudgetNamespace)
DEMO_NAMESPACES: tuple[BudgetNamespace, ...] = ("demo-extraction", "demo-chat")
NAMESPACE_HEADER = "x-benefura-budget-namespace"
EVALS_KEY_HEADER = "x-benefura-evals-key"


@dataclass(frozen=True)
class Pricing:
    # USD per token (input, output). M0-verify: Global Standard list prices, September 2026.
    models: dict[str, tuple[float, float]]
    # M0-verify: CU content extraction (prebuilt-layout / analyzer base) per page.
    cu_page: float
    # M0-verify: CU field extraction (contextualization) per receipt page, on top of cu_page.
    cu_field_page: float
    # M0-verify: Language PII, S0 on the AIServices account, per 1,000-character text record.
    language_record: float
    # M0-verify: Content Safety text records (Prompt Shields) and images, S0 on the AIServices account.
    content_safety_record: float


PRICING = Pricing(
    models={
        "gpt-5-mini": (0.25 / 1_000_000, 2.00 / 1_000_000),  # M0-verify
        "gpt-5-nano": (0.05 / 1_000_000, 0.40 / 1_000_000),  # M0-verify
        "text-embedding-3-small": (0.02 / 1_000_000, 0.0),  # M0-verify
    },
    cu_page=5.0 / 1000,  # M0-verify: $5 per 1K pages
    cu_field_page=5.0 / 1000,  # M0-verify
    language_record=1.0 / 1000,  # M0-verify: $1 per 1K text records
    content_safety_record=0.75 / 1000,  # M0-verify: priced at the image rate (conservative for text)
)


def estimate_cost_usd(usage: Usage, *, model_tokens: dict[str, tuple[int, int]] | None = None) -> float:
    """Without ``model_tokens``, and for unknown models, tokens are priced at the chat model's rate."""
    settings = get_settings()
    fallback = PRICING.models.get(settings.chat_model, PRICING.models["gpt-5-mini"])
    tokens = (
        model_tokens if model_tokens is not None else {settings.chat_model: (usage.inputTokens, usage.outputTokens)}
    )
    cost = 0.0
    for model, (input_tokens, output_tokens) in tokens.items():
        price_in, price_out = PRICING.models.get(model, fallback)
        cost += input_tokens * price_in + output_tokens * price_out
    cost += usage.cuPages * PRICING.cu_page
    cost += usage.languageRecords * PRICING.language_record
    cost += usage.contentSafetyRecords * PRICING.content_safety_record
    return round(cost, 6)


@dataclass
class UsageTracker:
    """Accumulates usage for one request so partial work is still charged when a step raises."""

    model_tokens: dict[str, tuple[int, int]] = field(default_factory=dict)
    cu_pages: int = 0
    cu_field_pages: int = 0
    language_records: int = 0
    content_safety_records: int = 0
    by_step_ms: dict[str, int] = field(default_factory=dict)

    def add_tokens(self, model: str, input_tokens: int, output_tokens: int, *, agent: str | None, ms: int) -> None:
        prev_in, prev_out = self.model_tokens.get(model, (0, 0))
        self.model_tokens[model] = (prev_in + input_tokens, prev_out + output_tokens)
        record_model_usage(model, agent, input_tokens, output_tokens, ms)

    def add_ms(self, step: str, ms: int) -> None:
        self.by_step_ms[step] = self.by_step_ms.get(step, 0) + ms

    @contextmanager
    def step(self, name: str, **attributes: str | int | float | bool) -> Iterator[None]:
        """Span for one pipeline step; its duration lands in ``byStepMs`` even on error."""
        started = perf_counter()
        try:
            with timed_span(name, **attributes):
                yield
        finally:
            self.add_ms(name, int((perf_counter() - started) * 1000))

    def to_usage(self) -> Usage:
        usage = Usage(
            inputTokens=sum(t[0] for t in self.model_tokens.values()),
            outputTokens=sum(t[1] for t in self.model_tokens.values()),
            cuPages=self.cu_pages,
            languageRecords=self.language_records,
            contentSafetyRecords=self.content_safety_records,
            byStepMs=dict(self.by_step_ms),
        )
        cost = estimate_cost_usd(usage, model_tokens=self.model_tokens) + self.cu_field_pages * PRICING.cu_field_page
        usage.estimatedCostUsd = round(cost, 6)
        return usage


class Budget(Protocol):
    async def ensure_available(self, namespace: BudgetNamespace) -> None: ...

    async def charge(self, namespace: BudgetNamespace, usd: float) -> None: ...

    async def remaining_pct(self, namespace: BudgetNamespace) -> float: ...


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


def daily_limit_usd(namespace: BudgetNamespace, settings: Settings | None = None) -> float:
    settings = settings or get_settings()
    return {
        "demo-extraction": settings.daily_budget_usd_extraction,
        "demo-chat": settings.daily_budget_usd_chat,
        "evals": settings.daily_budget_usd_evals,
    }[namespace]


def _exhausted(namespace: BudgetNamespace) -> ApiError:
    area = {"demo-extraction": "extraction", "demo-chat": "chat", "evals": "eval"}[namespace]
    return ApiError(
        "budget_exhausted",
        f"Today's demo {area} budget is used up. It resets at midnight UTC.",
        retry_after=seconds_until_utc_midnight(),
    )


def _pct(limit: float, spent: float) -> float:
    if limit <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (limit - spent) / limit * 100)), 2)


class InMemoryBudget:
    """Process-local, so only correct with a single replica."""

    def __init__(self) -> None:
        self._spent: dict[tuple[str, str], float] = {}

    async def spent(self, namespace: BudgetNamespace) -> float:
        return self._spent.get((namespace, utc_today()), 0.0)

    async def ensure_available(self, namespace: BudgetNamespace) -> None:
        if await self.spent(namespace) >= daily_limit_usd(namespace):
            raise _exhausted(namespace)

    async def charge(self, namespace: BudgetNamespace, usd: float) -> None:
        if usd <= 0:
            return
        key = (namespace, utc_today())  # no await between read and write: atomic on the event loop
        self._spent[key] = self._spent.get(key, 0.0) + usd
        record_budget_spend(namespace, usd)

    async def remaining_pct(self, namespace: BudgetNamespace) -> float:
        return _pct(daily_limit_usd(namespace), await self.spent(namespace))


class TableBudget:
    """One entity per (namespace, UTC date), updated with ETag optimistic concurrency."""

    # M0-verify: Bicep creates the ``budgets`` table; the API identity only reads and writes entities.

    MAX_ATTEMPTS = 6

    def __init__(self, table: TableClient) -> None:
        self._table = table

    async def spent(self, namespace: BudgetNamespace) -> float:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = await self._table.get_entity(f"budget-{namespace}", utc_today())
        except ResourceNotFoundError:
            return 0.0
        return float(entity.get("spentUsd", 0.0))

    async def ensure_available(self, namespace: BudgetNamespace) -> None:
        try:
            spent = await self.spent(namespace)
        except Exception as exc:
            # Fail closed: without the ledger we cannot bound spend.
            logger.error("Budget ledger unavailable (%s)", type(exc).__name__)
            raise ApiError("upstream_error", "Budget ledger unavailable; try again shortly.", retry_after=30) from exc
        if spent >= daily_limit_usd(namespace):
            raise _exhausted(namespace)

    async def charge(self, namespace: BudgetNamespace, usd: float) -> None:
        if usd <= 0:
            return
        await update_entity_with_retry(
            self._table,
            f"budget-{namespace}",
            utc_today(),
            lambda entity: {"spentUsd": float(entity.get("spentUsd", 0.0)) + usd},
            max_attempts=self.MAX_ATTEMPTS,
        )
        record_budget_spend(namespace, usd)

    async def remaining_pct(self, namespace: BudgetNamespace) -> float:
        return _pct(daily_limit_usd(namespace), await self.spent(namespace))

    async def close(self) -> None:
        await self._table.close()


async def update_entity_with_retry(
    table: TableClient,
    partition_key: str,
    row_key: str,
    mutate: Any,
    *,
    max_attempts: int = 6,
) -> dict[str, Any]:
    """``mutate(current)`` returns the changed properties; if it raises (e.g. a cap check), nothing is written."""
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
    from azure.data.tables import UpdateMode

    for attempt in range(max_attempts):
        try:
            current = await table.get_entity(partition_key, row_key)
        except ResourceNotFoundError:
            entity = {"PartitionKey": partition_key, "RowKey": row_key, **mutate({})}
            try:
                await table.create_entity(entity)
                return entity
            except ResourceExistsError:
                continue
        changes = mutate(dict(current))
        entity = {"PartitionKey": partition_key, "RowKey": row_key, **changes}
        try:
            await table.update_entity(
                entity,
                mode=UpdateMode.MERGE,
                etag=current.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            return {**dict(current), **changes}
        except ResourceModifiedError:
            await asyncio.sleep(0.05 * (attempt + 1))
    raise ApiError("upstream_error", "Usage ledger is busy; try again shortly.", retry_after=5)


@lru_cache
def get_table_client() -> TableClient | None:
    settings = get_settings()
    if settings.ai_mode != "live" or not settings.storage_table_endpoint:
        return None
    from azure.data.tables.aio import TableClient

    from app.services.foundry import get_credential

    return TableClient(settings.storage_table_endpoint, settings.budget_table, credential=get_credential())


@lru_cache
def get_budget() -> Budget:
    settings = get_settings()
    table = get_table_client()
    if table is not None:
        return TableBudget(table)
    if settings.ai_mode == "live":
        logger.warning("STORAGE_TABLE_ENDPOINT is not set; using an in-memory budget (single replica only)")
    return InMemoryBudget()


async def close_budget() -> None:
    if get_table_client.cache_info().currsize:
        table = get_table_client()
        if table is not None:
            await table.close()
    get_table_client.cache_clear()
    get_budget.cache_clear()


def namespace_for(
    request_namespace: str | None, default: BudgetNamespace, *, evals_key: str | None = None
) -> BudgetNamespace:
    """A wrong or missing key silently keeps ``default`` so the header can't be used to probe the secret."""
    secret = get_settings().evals_shared_secret
    if (
        request_namespace == "evals"
        and secret
        and evals_key
        and hmac.compare_digest(evals_key.encode(), secret.encode())
    ):
        return "evals"
    return default


def namespace_from_request(request: Request, default: BudgetNamespace) -> BudgetNamespace:
    return namespace_for(
        request.headers.get(NAMESPACE_HEADER), default, evals_key=request.headers.get(EVALS_KEY_HEADER)
    )


async def charge_usage(budget: Budget, namespace: BudgetNamespace, tracker: UsageTracker) -> Usage:
    """Ledger failures are logged, not raised: the request already ran."""
    usage = tracker.to_usage()
    try:
        await budget.charge(namespace, usage.estimatedCostUsd)
    except Exception as exc:
        logger.error("Budget charge failed (%s)", type(exc).__name__)
    return usage
