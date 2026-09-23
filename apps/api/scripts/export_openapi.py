"""Writes ``openapi.json`` plus the browser-only models no route references; ``--check`` fails on drift."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from app.main import app
from app.models.api import ChatContext, ChatRequest, ErrorResponse
from app.models.claim import Claim
from app.models.plan import Plan
from app.models.receipt import Receipt

OUT = Path(__file__).resolve().parents[1] / "openapi.json"
EXTRA_MODELS = [Plan, Claim, Receipt, ChatRequest, ChatContext, ErrorResponse]


def build() -> str:
    spec = app.openapi()
    _, defs = models_json_schema(
        [(m, "serialization") for m in EXTRA_MODELS], ref_template="#/components/schemas/{model}"
    )
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in defs.get("$defs", {}).items():
        schemas.setdefault(name, schema)
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("openapi.json is out of date; run: uv run python -m scripts.export_openapi", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
