from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.harness.datasets import REPO_ROOT

GOLDEN_DIR = REPO_ROOT / "apps" / "api" / "tests" / "chat" / "golden"
# Must match apps/api/tests/chat/helpers.py, which recorded the golden files.
GOLDEN_CONTEXT: dict[str, Any] = {
    "region": "CA",
    "today": "2026-09-16",
    "tz": "America/Toronto",
    "currency": "CAD",
    "planName": "Group Extended Health and Dental, Class A",
    "memberAliases": ["[MEMBER_A]", "[MEMBER_B]"],
    "categories": ["Paramedical practitioners", "Vision care"],
}


def golden_sse(name: str) -> list[str]:
    return (GOLDEN_DIR / f"{name}.sse").read_text(encoding="utf-8").split("\n")


def golden_request(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{name}.request.json").read_text(encoding="utf-8"))


def chat_route_app():
    from app.config import get_settings
    from app.limits import reset_limits
    from app.main import create_app

    get_settings.cache_clear()
    reset_limits()
    return create_app()


@pytest.fixture
def fake_ai_mode(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AI_MODE", "fake")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN_DIR
