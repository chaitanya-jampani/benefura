"""Shared spike helpers. Settings come from app.config, so run spikes inside the API environment."""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SPIKES_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKES_DIR.parent
API_DIR = REPO_ROOT / "apps" / "api"
RESULTS_DIR = SPIKES_DIR / "results"

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.com/.default"


def settings() -> Any:
    from app.config import get_settings

    return get_settings()


def require(value: str, name: str) -> str:
    if not value:
        sys.exit(f"{name} is not set. Run `azd env get-values > apps/api/.env` first (see spikes/README.md).")
    return value


def credential() -> Any:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential(exclude_interactive_browser_credential=True, exclude_managed_identity_credential=True)


def bearer(scope: str = COGNITIVE_SCOPE) -> str:
    return credential().get_token(scope).token


def project_client() -> Any:
    from azure.ai.projects import AIProjectClient

    s = settings()
    return AIProjectClient(endpoint=require(s.foundry_project_endpoint, "FOUNDRY_PROJECT_ENDPOINT"), credential=credential())


def openai_client() -> Any:
    return project_client().get_openai_client()


def ai_services_url(path: str) -> str:
    base = require(settings().ai_services_endpoint, "AI_SERVICES_ENDPOINT").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


@contextmanager
def timer() -> Iterator[dict[str, float]]:
    box: dict[str, float] = {}
    start = time.perf_counter()
    try:
        yield box
    finally:
        box["ms"] = round((time.perf_counter() - start) * 1000, 1)


def approx_tokens(text: str) -> int:
    """o200k token count when tiktoken is installed, else the ~4 chars/token heuristic."""
    try:
        import tiktoken  # type: ignore[import-not-found]

        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:  # noqa: BLE001
        return max(1, round(len(text) / 4))


def write_result(spike: str, data: dict[str, Any], passed: bool | None) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    record = {"spike": spike, "at": stamp, "passed": passed, **data}
    path = RESULTS_DIR / f"{spike}-{stamp}.json"
    path.write_text(json.dumps(record, indent=2, default=str) + "\n")
    verdict = {True: "PASS", False: "FAIL", None: "RECORDED"}[passed]
    print(f"\n{verdict}: {spike} -> {path.relative_to(REPO_ROOT)}")
    return path


def check(results: dict[str, bool], name: str, ok: bool, detail: str = "") -> None:
    results[name] = bool(ok)
    mark = "ok  " if ok else "FAIL"
    print(f"[{mark}] {name}{': ' + detail if detail else ''}")
