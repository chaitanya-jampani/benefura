"""Citation checks against the licence and reproduction rules in ``knowledge/sources.yaml``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evals.harness.runner import resolve_source_id

JSON = dict[str, Any]
EXCERPT_QUOTE_MAX_WORDS = 50
MIN_QUOTE_WORDS = 6
_QUOTED = re.compile(r"[\"“]([^\"”]{10,})[\"”]")
_WORDS = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'")).strip()


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(0, max(0, len(words) - n + 1))}


def longest_shared_run_exceeds(response: str, context: str, limit: int) -> bool:
    n = limit + 1
    resp = _WORDS.findall(response.lower())
    if len(resp) < n:
        return False
    return bool(_ngrams(resp, n) & _ngrams(_WORDS.findall(context.lower()), n))


@dataclass
class CitationResult:
    id: str
    passed: bool
    present: bool
    unknown_sources: list[str] = field(default_factory=list)
    link_only_cited: list[str] = field(default_factory=list)
    licence_mismatch: list[str] = field(default_factory=list)
    missing_attribution: list[str] = field(default_factory=list)
    excerpt_overrun: list[str] = field(default_factory=list)
    altered_verbatim_quotes: int = 0
    expected_source_hit: bool = False
    unverifiable_reproduction: bool = False


def evaluate_row(row: JSON, sources: dict[str, JSON]) -> CitationResult:
    meta = row.get("meta", {})
    citations = [c for c in meta.get("citations", []) if isinstance(c, dict)]
    response = row.get("response", "") or ""
    context = row.get("context", "") or ""
    result = CitationResult(meta.get("id", "?"), passed=False, present=bool(citations))
    cited: list[str] = []
    for c in citations:
        sid = c.get("resolvedSourceId") or resolve_source_id(c, sources)
        if not sid or sid not in sources:
            result.unknown_sources.append(str(c.get("url") or c.get("sourceId")))
            continue
        cited.append(sid)
        src = sources[sid]
        if not src.get("index"):
            result.link_only_cited.append(sid)
        licence = c.get("license") or c.get("licence")
        if licence is not None and _norm(str(licence)) != _norm(str(src["licence"])):
            result.licence_mismatch.append(sid)
        if not (c.get("attribution") or "").strip():
            result.missing_attribution.append(sid)

    if not context.strip():
        result.unverifiable_reproduction = True
    else:
        norm_context = _norm(context)
        for sid in set(cited):
            if sources[sid]["reproduction"] == "excerpt" and longest_shared_run_exceeds(
                response, context, EXCERPT_QUOTE_MAX_WORDS
            ):
                result.excerpt_overrun.append(sid)
        if any(sources[sid]["reproduction"] == "verbatim" for sid in cited):
            for quote in _QUOTED.findall(response):
                if len(_WORDS.findall(quote.lower())) >= MIN_QUOTE_WORDS and _norm(quote) not in norm_context:
                    result.altered_verbatim_quotes += 1

    expected = set(meta.get("expectedSources", []))
    result.expected_source_hit = bool(expected & set(cited))
    result.passed = (
        result.present
        and not result.unknown_sources
        and not result.link_only_cited
        and not result.licence_mismatch
        and not result.missing_attribution
        and not result.excerpt_overrun
        and result.altered_verbatim_quotes == 0
    )
    return result


def score_citations(rows: list[JSON], sources: dict[str, JSON]) -> JSON:
    results = [evaluate_row(r, sources) for r in rows]
    total = len(results)
    return {
        "rows": total,
        "citation_licence_pass_rate": (sum(r.passed for r in results) / total) if total else None,
        "citation_expected_source_rate": (sum(r.expected_source_hit for r in results) / total) if total else None,
        "unverifiable_reproduction": sum(r.unverifiable_reproduction for r in results),
        "details": [r.__dict__ for r in results],
    }
