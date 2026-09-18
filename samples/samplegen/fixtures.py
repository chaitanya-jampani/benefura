"""Access to the canonical fixture plans (``samples/fixtures/*.plan.json``), which are read-only here."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

SAMPLES = Path(__file__).resolve().parent.parent
FIXTURES = SAMPLES / "fixtures"


@cache
def plan(doc: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{doc}.plan.json").read_text())


def quotes(doc: str) -> dict[str, dict[str, Any]]:
    """Fixture ``source`` quotes keyed by benefit/pool/cost-share id, ``claimRules`` or ``hospital:<name>``."""

    p = plan(doc)
    out: dict[str, dict[str, Any]] = {}
    for cat in p["categories"]:
        for b in cat["benefits"]:
            out[b["id"]] = b["source"]
    for pool in p["limitPools"]:
        out[pool["id"]] = pool["source"]
    for cs in p["costShares"]:
        out[cs["id"]] = cs["source"]
    out["claimRules"] = p["claimRules"]["source"]
    hospital = (p["profile"].get("hospital") or {}) if p["profile"]["kind"] == "AU" else {}
    for hc in hospital.get("categories", []):
        out[f"hospital:{hc['name']}"] = hc["source"]
    return out


def q(doc: str, key: str) -> str:
    return quotes(doc)[key]["quote"]


def lead(quote: str, tail: str = "") -> str:
    """``"Label: rest"`` → ``"**Label:** rest."`` plus an optional follow-on sentence."""

    head, rest = quote.split(": ", 1)
    text = f"**{head}:** {rest}."
    return f"{text} {tail}" if tail else text


def sentence(quote: str, tail: str = "", bold: bool = False) -> str:
    text = f"**{quote}.**" if bold else f"{quote}."
    return f"{text} {tail}" if tail else text
