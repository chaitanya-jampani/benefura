from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import VERSION, Settings


def test_healthz_reports_mode_and_version(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["aiMode"] == "fake" and body["aiEnabled"] is True
    assert body["version"] == VERSION


def test_ai_off_keeps_healthz_working(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AI_MODE", "off")
    get_settings.cache_clear()
    with TestClient(create_app()) as off_client:
        body = off_client.get("/healthz").json()
    assert body["aiEnabled"] is False and body["aiMode"] == "off"


def test_cors_allows_configured_origin(client: TestClient) -> None:
    preflight = client.options(
        "/healthz",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.options(
        "/healthz",
        headers={"Origin": "https://elsewhere.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in other.headers


@pytest.mark.parametrize(
    "raw",
    ['["http://a.test", "http://b.test"]', "http://a.test, http://b.test"],
)
def test_cors_origins_accept_json_or_commas(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_fake_mode_relaxes_caps_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().rate_limit_per_minute == 600
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    assert Settings().rate_limit_per_minute == 5
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE")
    assert Settings().rate_limit_per_minute == 30


def test_not_found_uses_error_body(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_request"


def test_openapi_is_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import export_openapi

    monkeypatch.setattr("sys.argv", ["export_openapi", "--check"])
    assert export_openapi.main() == 0, "run: uv run python -m scripts.export_openapi"
