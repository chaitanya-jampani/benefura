from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

import httpx

from app.errors import ApiError
from app.services.foundry import ai_services_url, get_cognitive_token

logger = logging.getLogger("benefura.rest")

RETRYABLE = {408, 429, 500, 502, 503, 504}


@lru_cache
def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))


async def close_http_client() -> None:
    if get_http_client.cache_info().currsize:
        await get_http_client().aclose()
    get_http_client.cache_clear()


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    header = response.headers.get("retry-after") if response is not None else None
    if header and header.replace(".", "", 1).isdigit():
        return min(float(header), 10.0)
    return min(0.5 * 2**attempt, 8.0)


async def post_json(path: str, payload: dict[str, Any], *, service: str, max_attempts: int = 4) -> dict[str, Any]:
    """Only service and status are surfaced: response bodies can echo the submitted text."""
    url = ai_services_url(path)
    client = get_http_client()
    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        token = await get_cognitive_token()
        try:
            response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        except httpx.TransportError as exc:
            logger.warning("%s transport error (%s), attempt %d", service, type(exc).__name__, attempt + 1)
            response = None
            if attempt + 1 < max_attempts:
                await asyncio.sleep(_retry_delay(None, attempt))
                continue
            raise ApiError("upstream_error", f"{service} is unreachable; try again shortly.") from exc
        if response.status_code < 300:
            return response.json()
        if response.status_code in RETRYABLE and attempt + 1 < max_attempts:
            await asyncio.sleep(_retry_delay(response, attempt))
            continue
        break
    status = response.status_code if response is not None else 0
    logger.error("%s returned HTTP %d", service, status)
    retry_after = 10 if status == 429 else None
    raise ApiError("upstream_error", f"{service} request failed (HTTP {status}).", retry_after=retry_after)
