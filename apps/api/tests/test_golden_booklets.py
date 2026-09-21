"""Fake-mode analyze → assemble of the sample booklets must reproduce the fixture plans."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT
from tests.conftest import image_pdf

SAMPLES = Path(os.environ.get("BENEFURA_SAMPLES_DIR", REPO_ROOT / "samples"))
DOCS = [("CA", "ca-northwind"), ("AU", "au-wattle")]


def chunk_windows(page_count: int, size: int = 5) -> list[list[int]]:
    windows, start = [], 1
    while True:
        end = min(start + size - 1, page_count)
        windows.append(list(range(start, end + 1)))
        if end == page_count:
            return windows
        start = end  # 1-page overlap


def by_name(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ids = {b["id"]: b["name"] for c in plan["categories"] for b in c["benefits"]}
    pools = {p["id"]: p["name"] for p in plan["limitPools"]}
    shares = {s["id"]: s["name"] for s in plan["costShares"]}
    view: dict[str, dict[str, Any]] = {}
    for category in plan["categories"]:
        for benefit in category["benefits"]:
            assert ids[benefit["id"]] == benefit["name"]
            view[benefit["name"]] = {
                "category": (category["name"], category["kind"]),
                "coverage": benefit["coverage"],
                "limits": benefit["limits"],
                "frequency": benefit["frequency"],
                "waitingPeriod": benefit["waitingPeriod"],
                "requirements": benefit["requirements"],
                "keywords": benefit["keywords"],
                "itemCodes": benefit["itemCodes"],
                "pool": pools.get(benefit["poolId"]) if benefit["poolId"] else None,
                "costShares": sorted(shares[i] for i in benefit["costShareIds"]),
                "source": {k: benefit["source"][k] for k in ("page", "quote", "verifierVerdict")},
            }
    return view


def without_confidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_confidence(v) for k, v in value.items() if k != "confidence"}
    if isinstance(value, list):
        return [without_confidence(v) for v in value]
    return value


def pools_view(plan: dict[str, Any]) -> list[dict[str, Any]]:
    names = {b["id"]: b["name"] for c in plan["categories"] for b in c["benefits"]}
    return [
        {"name": p["name"], "limit": p["limit"], "benefits": [names[i] for i in p["benefitIds"]]}
        for p in plan["limitPools"]
    ]


def shares_view(plan: dict[str, Any]) -> list[dict[str, Any]]:
    categories = {c["id"]: c["name"] for c in plan["categories"]}
    names = {b["id"]: b["name"] for c in plan["categories"] for b in c["benefits"]}
    return [
        {
            **{k: s[k] for k in ("name", "kind", "amountCents", "percent", "period", "scope")},
            "categories": [categories[i] for i in s["appliesToCategoryIds"]],
            "benefits": [names[i] for i in s["appliesToBenefitIds"]],
        }
        for s in plan["costShares"]
    ]


@pytest.mark.parametrize(("region", "doc"), DOCS)
def test_golden_booklet_round_trip(region: str, doc: str, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_file = SAMPLES / "golden" / f"{doc}.pages.json"
    if not pages_file.exists() or not (SAMPLES / "golden" / f"{doc}.extraction.json").exists():
        pytest.skip(f"golden files for {doc} are not in {SAMPLES}")
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("SAMPLES_DIR", str(SAMPLES))
    get_settings.cache_clear()
    expected = json.loads((SAMPLES / "fixtures" / f"{doc}.plan.json").read_text())
    page_count = len(json.loads(pages_file.read_text()))

    with TestClient(create_app()) as client:
        chunks = []
        for pages in chunk_windows(page_count):
            response = client.post(
                "/api/plan/analyze-chunk",
                data={"documentId": f"golden-{doc}", "region": region, "pages": ",".join(map(str, pages))},
                files={"file": ("chunk.pdf", image_pdf(len(pages)), "application/pdf")},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert not [i for i in body["issues"] if i["code"] in ("ungrounded_quote", "unsupported_row")], body[
                "issues"
            ]
            chunks.append({"pages": body["pages"], "rows": body["rows"]})
        response = client.post(
            "/api/plan/assemble",
            json={"region": region, "documentName": f"{doc}.pdf", "pageCount": page_count, "chunks": chunks},
        )
    assert response.status_code == 200, response.text
    result = response.json()
    plan = result["plan"]
    assert any(i["code"] == "duplicate_removed" for i in result["issues"])  # the overlaps were deduplicated

    for key in ("region", "insurer", "planName", "currency", "effectiveDate", "benefitPeriod"):
        assert plan[key] == expected[key], key
    # Members and identifiers come from the alias header rows (``Spouse | [MEMBER_B]``, ``... | [POLICY_1]``).
    assert sorted((m["alias"], m["relationship"]) for m in plan["members"]) == sorted(
        (m["alias"], m["relationship"]) for m in expected["members"]
    )
    assert sorted(plan["identifiers"]) == sorted(expected["identifiers"])
    assert not [i for i in result["issues"] if "relationships were guessed" in i["message"]]
    assert without_confidence(plan["claimRules"]) == without_confidence(expected["claimRules"])
    assert without_confidence(plan["profile"]) == without_confidence(expected["profile"])
    assert pools_view(plan) == pools_view(expected)
    assert shares_view(plan) == shares_view(expected)

    actual_benefits, expected_benefits = by_name(plan), by_name(expected)
    assert sorted(actual_benefits) == sorted(expected_benefits)
    for name, want in expected_benefits.items():
        assert actual_benefits[name] == want, name
