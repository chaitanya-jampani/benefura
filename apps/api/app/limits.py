"""Per-IP rate limit and booklet/page caps; with the daily budgets these, not CORS, are the abuse controls."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import deque
from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

from app.budget import get_table_client, seconds_until_utc_midnight, update_entity_with_retry, utc_today
from app.config import get_settings
from app.errors import ApiError

if TYPE_CHECKING:
    from azure.data.tables.aio import TableClient
    from starlette.requests import HTTPConnection

RATE_LIMIT_CHECKED = "benefura_rate_limit_checked"


def client_ip(connection: HTTPConnection) -> str:
    """The first hop is spoofable by a forged ``X-Forwarded-For``; ``CLIENT_IP_HOP=last`` takes the hop ACA
    ingress appended. The daily budgets bound the worst case either way."""
    forwarded = connection.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if hops:
        return hops[0] if get_settings().client_ip_hop == "first" else hops[-1]
    return connection.client.host if connection.client else "unknown"


def _digest(kind: str, value: str) -> str:
    # Day-scoped salt: keys are unlinkable across days and never reveal the raw value.
    return hashlib.sha256(f"benefura:{kind}:{utc_today()}:{value}".encode()).hexdigest()[:40]


class RateLimiter:
    """Sliding window per key, in memory because Container Apps runs 0–1 replicas."""

    def __init__(self, per_minute: int, *, window_s: float = 60.0) -> None:
        self.per_minute = per_minute
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.per_minute:
            retry_after = max(1, math.ceil(hits[0] + self.window_s - now))
            raise ApiError("rate_limited", "Too many requests; slow down and retry shortly.", retry_after=retry_after)
        hits.append(now)
        if len(self._hits) > 10_000:
            self._prune(now)

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window_s]:
            del self._hits[key]


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_settings().rate_limit_per_minute)


def enforce_rate_limit(connection: HTTPConnection, limiter: RateLimiter | None = None) -> None:
    """Idempotent per request: the guard middleware already calls it for every ``POST /api/*``."""
    state = connection.scope.setdefault("state", {})
    if state.get(RATE_LIMIT_CHECKED):
        return
    state[RATE_LIMIT_CHECKED] = True
    (limiter or get_rate_limiter()).check(_digest("ip", client_ip(connection)))


class CapStore(Protocol):
    async def register_chunk(self, ip: str, document_id: str, pages: list[int]) -> None: ...


def _booklet_check(document_ids: list[str], doc_key: str) -> list[str]:
    settings = get_settings()
    if doc_key in document_ids:
        return document_ids
    if len(document_ids) >= settings.max_booklets_per_ip_per_day:
        raise ApiError(
            "booklet_cap_exceeded",
            f"The demo allows {settings.max_booklets_per_ip_per_day} booklets per day. Try again tomorrow.",
            retry_after=seconds_until_utc_midnight(),
        )
    return [*document_ids, doc_key]


def _page_check(seen: set[int], analyzed: int, pages: list[int]) -> tuple[set[int], int]:
    """Distinct pages stay within the cap; analyzed pages, counting overlaps and retries, within 2×."""
    max_pages = get_settings().max_booklet_pages
    merged = seen | set(pages)
    total = analyzed + len(pages)
    if max(pages, default=0) > max_pages or len(merged) > max_pages or total > 2 * max_pages:
        raise ApiError("page_cap_exceeded", f"Booklets are limited to {max_pages} pages in the demo.")
    return merged, total


class InMemoryCapStore:
    def __init__(self) -> None:
        self._booklets: dict[str, list[str]] = {}
        self._pages: dict[str, tuple[set[int], int]] = {}

    async def register_chunk(self, ip: str, document_id: str, pages: list[int]) -> None:
        ip_key, doc_key = _digest("ip", ip), _digest("doc", document_id)
        booklets = _booklet_check(self._booklets.get(ip_key, []), doc_key)
        seen, analyzed = self._pages.get(doc_key, (set(), 0))
        self._pages[doc_key] = _page_check(seen, analyzed, pages)
        self._booklets[ip_key] = booklets


class TableCapStore:
    def __init__(self, table: TableClient) -> None:
        self._table = table

    async def register_chunk(self, ip: str, document_id: str, pages: list[int]) -> None:
        today = utc_today()
        ip_key, doc_key = _digest("ip", ip), _digest("doc", document_id)

        def booklets(entity: Mapping[str, Any]) -> dict[str, Any]:
            current = json.loads(entity.get("documents", "[]"))
            return {"documents": json.dumps(_booklet_check(current, doc_key))}

        def page_counts(entity: Mapping[str, Any]) -> dict[str, Any]:
            seen = set(json.loads(entity.get("pages", "[]")))
            merged, total = _page_check(seen, int(entity.get("analyzed", 0)), pages)
            return {"pages": json.dumps(sorted(merged)), "analyzed": total}

        # Page check first: a rejected chunk must not consume one of the caller's booklet slots.
        await update_entity_with_retry(self._table, f"pages-{today}", doc_key, page_counts)
        await update_entity_with_retry(self._table, f"booklets-{today}", ip_key, booklets)


@lru_cache
def get_cap_store() -> CapStore:
    table = get_table_client()
    return TableCapStore(table) if table is not None else InMemoryCapStore()


def reset_limits() -> None:
    get_rate_limiter.cache_clear()
    get_cap_store.cache_clear()
