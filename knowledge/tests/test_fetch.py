from __future__ import annotations

import httpx

from knowledge.ingest.fetch import USER_AGENT, FixtureFetcher, HttpFetcher, sha256
from knowledge.ingest.sources import Source

URL = "https://example.org/page.html"


def source(**overrides) -> Source:
    data = {
        "id": "example-page",
        "url": URL,
        "region": "AU",
        "title": "Example",
        "publisher": "Tests",
        "licence": "Synthetic",
        "licence_note": "Synthetic test content only.",
        "attribution": "Synthetic fixture.",
        "reproduction": "verbatim",
        "index": True,
    }
    data.update(overrides)
    return Source.model_validate(data)


class FakeSite:
    def __init__(self) -> None:
        self.body = b"<html><main><h1>Hello</h1><p>World</p></main></html>"
        self.etag = '"v1"'
        self.status = 200
        self.robots = "User-agent: *\nAllow: /\n"
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        if request.headers.get("if-none-match") == self.etag:
            return httpx.Response(304)
        return httpx.Response(
            200, content=self.body, headers={"etag": self.etag, "content-type": "text/html; charset=utf-8"}
        )


def fetcher(site: FakeSite, tmp_path) -> HttpFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler), headers={"User-Agent": USER_AGENT})
    return HttpFetcher(client, tmp_path, min_host_interval_s=0)


async def test_first_fetch_is_changed_and_cached(tmp_path):
    site = FakeSite()
    result = await fetcher(site, tmp_path).fetch(source())
    assert result.status == "changed" and result.ok
    assert result.content_type == "text/html"
    assert result.content_hash == sha256(site.body)
    assert (tmp_path / "example-page.body").read_bytes() == site.body
    assert site.requests[0].headers["user-agent"].startswith("BenefuraKnowledgeBot/")


async def test_etag_304_is_unchanged_and_served_from_cache(tmp_path):
    site = FakeSite()
    f = fetcher(site, tmp_path)
    await f.fetch(source())
    result = await f.fetch(source())
    assert site.requests[1].headers["if-none-match"] == '"v1"'
    assert result.status == "unchanged" and result.http_status == 304
    assert result.body == site.body


async def test_same_hash_without_validators_is_unchanged(tmp_path):
    site = FakeSite()
    f = fetcher(site, tmp_path)
    await f.fetch(source())
    site.etag = '"v2"'  # server changed its etag but not the bytes
    result = await f.fetch(source())
    assert result.status == "unchanged" and result.http_status == 200


async def test_new_bytes_are_changed(tmp_path):
    site = FakeSite()
    f = fetcher(site, tmp_path)
    first = await f.fetch(source())
    site.body, site.etag = b"<html><main><p>Updated</p></main></html>", '"v2"'
    second = await f.fetch(source())
    assert second.status == "changed" and second.content_hash != first.content_hash


async def test_http_errors_fail_without_raising(tmp_path):
    site = FakeSite()
    site.status = 503
    result = await fetcher(site, tmp_path).fetch(source())
    assert result.status == "failed" and result.error == "http_503" and not result.ok


async def test_network_errors_fail_without_raising(tmp_path):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    result = await HttpFetcher(client, tmp_path, min_host_interval_s=0).fetch(source())
    assert result.status == "failed" and result.error == "ConnectError"


async def test_robots_disallow_is_respected(tmp_path):
    site = FakeSite()
    site.robots = "User-agent: *\nDisallow: /\n"
    result = await fetcher(site, tmp_path).fetch(source())
    assert result.status == "failed" and result.error == "robots_disallowed"
    assert site.requests == []


async def test_fixture_fetcher_reads_local_files(tmp_path):
    (tmp_path / "page.html").write_text("<p>hi</p>")
    ok = await FixtureFetcher(tmp_path).fetch(source(fixture="page.html"))
    assert ok.status == "changed" and ok.body == b"<p>hi</p>"
    missing = await FixtureFetcher(tmp_path).fetch(source(fixture="nope.html"))
    assert missing.status == "failed"
