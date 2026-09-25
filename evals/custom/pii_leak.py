"""Scans harness payloads for the fake PII in the samples. Reports carry kinds and locations, never values."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness.datasets import REPO_ROOT

JSON = dict[str, Any]
DEFAULT_GLOB = "samples/fixtures/*.pii.json"
_ALIAS = re.compile(r"^\[[A-Z0-9_]+\]$")
_SKIP_KEYS = {"kind", "aliastoken", "alias", "token", "page", "pages", "doc", "category", "region", "tier", "source"}
_DIGIT_GROUP = re.compile(r"\d[\d \-./]*\d")


@dataclass(frozen=True)
class Needle:
    kind: str
    pattern: re.Pattern[str]
    digits: str | None


def _needles_for(value: str, kind: str) -> list[Needle]:
    value = value.strip()
    if len(value) < 4 or _ALIAS.match(value):
        return []
    digits = re.sub(r"\D", "", value)
    has_letters = bool(re.search(r"[A-Za-z]", value))
    if not has_letters and len(digits) < 6:
        return []
    words = [re.escape(w) for w in value.split()]
    pattern = re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    needles = [Needle(kind, pattern, digits if len(digits) >= 6 else None)]
    if "name" in kind.lower() and not any(k in kind.lower() for k in ("employer", "org", "company", "provider")):
        parts = value.split()
        if len(parts) >= 2 and len(parts[-1]) >= 4:
            surname = re.compile(r"(?<![A-Za-z])" + re.escape(parts[-1]) + r"(?![A-Za-z])", re.IGNORECASE)
            needles.append(Needle(f"{kind}:surname", surname, None))
    return needles


def _walk(node: Any, key: str, out: list[Needle]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("value"), str):
            out.extend(_needles_for(node["value"], str(node.get("kind") or key or "value")))
            return
        for k, v in node.items():
            if k.lower() not in _SKIP_KEYS:
                _walk(v, k, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, key, out)
    elif isinstance(node, str) and key.lower() not in _SKIP_KEYS:
        out.extend(_needles_for(node, key or "value"))


def load_needles(paths: list[Path] | None = None) -> list[Needle]:
    files = paths if paths is not None else sorted(REPO_ROOT.glob(DEFAULT_GLOB))
    needles: list[Needle] = []
    for path in files:
        _walk(json.loads(path.read_text(encoding="utf-8")), "", needles)
    unique: dict[tuple[str, str | None], Needle] = {}
    for n in needles:
        unique.setdefault((n.pattern.pattern, n.digits), n)
    return list(unique.values())


def scan_text(text: str, needles: list[Needle]) -> Counter[str]:
    hits: Counter[str] = Counter()
    digit_groups = [re.sub(r"\D", "", g) for g in _DIGIT_GROUP.findall(text)]
    for needle in needles:
        if needle.pattern.search(text):
            hits[needle.kind] += 1
        elif needle.digits and any(
            needle.digits in group and len(group) <= len(needle.digits) + 2 for group in digit_groups
        ):
            hits[needle.kind] += 1
    return hits


def scan_jsonl_files(files: list[Path], needles: list[Needle]) -> JSON:
    total: Counter[str] = Counter()
    locations: list[JSON] = []
    scanned = 0
    for path in files:
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            scanned += len(line)
            hits = scan_text(line, needles)
            if hits:
                total.update(hits)
                try:
                    meta = json.loads(line)
                    ident = meta.get("id") or meta.get("meta", {}).get("id")
                except ValueError:
                    ident = None
                locations.append({"file": path.name, "line": n, "item": ident, "kinds": sorted(hits)})
    return {
        "pii_leak_hits": sum(total.values()),
        "by_kind": dict(total),
        "locations": locations,
        "scanned_chars": scanned,
        "needles": len(needles),
    }
