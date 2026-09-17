from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


def clear_caches() -> None:
    from app.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts in fake mode with fresh settings."""
    monkeypatch.setenv("AI_MODE", "fake")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
