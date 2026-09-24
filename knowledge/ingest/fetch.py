"""Polite HTTP fetching. The cache is only an optimisation: content-addressed chunk ids already skip re-embedding."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from knowledge.ingest.sources import KNOWLEDGE_DIR, Source

DEFAULT_CACHE_DIR = KNOWLEDGE_DIR / ".cache"
USER_AGENT = "BenefuraKnowledgeBot/0.1 (non-commercial portfolio demo; weekly refresh of public reference pages)"
MAX_BYTES = 25 * 1024 * 1024
MIN_HOST_INTERVAL_S = 2.0

FetchStatus = Literal["changed", "unchanged", "failed"]


@dataclass
class CacheMeta:
    url: str
    etag: str | None
    last_modified: str | None
    content_hash: str
    content_type: str
    fetched_at: str


@dataclass
class FetchResult:
    source_id: str
    url: str
    status: FetchStatus
    body: bytes | None = None
    content_type: str = ""
    content_hash: str | None = None
    fetched_at: str = ""
    http_status: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "failed" and self.body is not None


class Fetcher(Protocol):
    async def fetch(self, source: Source) -> FetchResult: ...


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HttpFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        *,
        min_host_interval_s: float = MIN_HOST_INTERVAL_S,
        respect_robots: bool = True,
    ) -> None:
        self._client = client
        self._cache_dir = cache_dir
        self._min_interval = min_host_interval_s
        self._respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def new_client(timeout_s: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"},
            timeout=timeout_s,
            follow_redirects=True,
            http2=False,
        )

    def _paths(self, source_id: str) -> tuple[Path, Path]:
        return self._cache_dir / f"{source_id}.body", self._cache_dir / f"{source_id}.meta.json"

    def load_meta(self, source_id: str) -> CacheMeta | None:
        _, meta_path = self._paths(source_id)
        if not meta_path.exists():
            return None
        try:
            return CacheMeta(**json.loads(meta_path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None

    def _save(self, source_id: str, body: bytes, meta: CacheMeta) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        body_path, meta_path = self._paths(source_id)
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

    async def _robots_allows(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                res = await self._client.get(f"{origin}/robots.txt")
                if res.status_code == 200:
                    assert parser is not None
                    parser.parse(res.text.splitlines())
                else:
                    parser = None
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    async def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        async with self._lock:
            wait = self._last_hit.get(host, 0.0) + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def fetch(self, source: Source) -> FetchResult:
        url = str(source.url)
        now = utc_now()
        try:
            if not await self._robots_allows(url):
                return FetchResult(source.id, url, "failed", fetched_at=now, error="robots_disallowed")
            meta = self.load_meta(source.id)
            body_path, _ = self._paths(source.id)
            headers: dict[str, str] = {}
            if meta and meta.url == url and body_path.exists():
                if meta.etag:
                    headers["If-None-Match"] = meta.etag
                if meta.last_modified:
                    headers["If-Modified-Since"] = meta.last_modified
            await self._throttle(url)
            res = await self._client.get(url, headers=headers)
            if res.status_code == 304 and meta is not None:
                return FetchResult(
                    source.id, url, "unchanged", body_path.read_bytes(), meta.content_type, meta.content_hash, now, 304
                )
            if res.status_code != 200:
                return FetchResult(
                    source.id,
                    url,
                    "failed",
                    fetched_at=now,
                    http_status=res.status_code,
                    error=f"http_{res.status_code}",
                )
            body = res.content
            if len(body) > MAX_BYTES:
                return FetchResult(source.id, url, "failed", fetched_at=now, http_status=200, error="too_large")
            content_type = res.headers.get("content-type", "").split(";")[0].strip().lower()
            digest = sha256(body)
            status: FetchStatus = "unchanged" if meta is not None and meta.content_hash == digest else "changed"
            self._save(
                source.id,
                body,
                CacheMeta(url, res.headers.get("etag"), res.headers.get("last-modified"), digest, content_type, now),
            )
            return FetchResult(source.id, url, status, body, content_type, digest, now, 200)
        except httpx.HTTPError as exc:
            return FetchResult(source.id, url, "failed", fetched_at=now, error=type(exc).__name__)


class FixtureFetcher:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    async def fetch(self, source: Source) -> FetchResult:
        now = utc_now()
        if not source.fixture:
            return FetchResult(source.id, str(source.url), "failed", fetched_at=now, error="no_fixture")
        path = self._dir / source.fixture
        if not path.exists():
            return FetchResult(source.id, str(source.url), "failed", fetched_at=now, error="fixture_missing")
        body = path.read_bytes()
        content_type = "application/pdf" if path.suffix == ".pdf" else "text/html"
        return FetchResult(source.id, str(source.url), "changed", body, content_type, sha256(body), now, 200)
