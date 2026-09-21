from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import image_pdf, png_bytes, text_pdf

TRACE = "x-benefura-trace-id"
DOC_ID = "doc-test-0001"


def post_chunk(
    client: TestClient,
    pages: list[int],
    *,
    region: str = "CA",
    document_id: str = DOC_ID,
    pdf: bytes | None = None,
    content_type: str = "application/pdf",
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/api/plan/analyze-chunk",
        data={"documentId": document_id, "region": region, "pages": ",".join(str(p) for p in pages)},
        files={"file": ("chunk.pdf", pdf if pdf is not None else image_pdf(len(pages)), content_type)},
        headers=headers or {},
    )


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert len(response.headers[TRACE]) == 32 and response.headers[TRACE] != "0" * 32
    assert body["error"]["traceId"] == response.headers[TRACE]
    return body


def test_healthz_reports_budget_and_trace(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["aiMode"] == "fake"
    assert body["budgetRemainingPct"] == 100.0
    assert set(body["budgets"]) == {"demo-extraction", "demo-chat", "evals"}
    assert len(response.headers[TRACE]) == 32


def test_cors_exposes_trace_and_retry_after(client: TestClient) -> None:
    preflight = client.options(
        "/api/plan/analyze-chunk",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-benefura-fake-pii",
        },
    )
    assert preflight.status_code == 200
    assert "x-benefura-fake-pii" in preflight.headers["access-control-allow-headers"].lower()
    response = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    exposed = response.headers["access-control-expose-headers"].lower()
    assert TRACE in exposed and "retry-after" in exposed


def test_fake_hook_headers_not_allowed_outside_fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AI_MODE", "off")
    get_settings.cache_clear()
    with TestClient(create_app()) as off_client:
        preflight = off_client.options(
            "/api/plan/analyze-chunk",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-benefura-fake-pii",
            },
        )
        assert preflight.status_code == 400


def test_analyze_chunk_returns_grounded_rows(client: TestClient) -> None:
    response = post_chunk(client, [1, 2, 3, 4, 5])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pages"] == [1, 2, 3, 4, 5]
    assert body["traceId"] == response.headers[TRACE]
    rows = body["rows"]
    assert {b["benefit_name"] for b in rows["benefits"]} >= {"Massage therapy", "Physiotherapy", "Eye exam"}
    assert all(b["page"] in {1, 2, 3, 4, 5} for b in rows["benefits"])
    massage = next(b for b in rows["benefits"] if b["benefit_name"] == "Massage therapy")
    assert massage["meta"]["grounded"] is True
    assert massage["meta"]["verifierVerdict"] == "supported"
    assert massage["meta"]["confidence"] >= 0.9
    usage = body["usage"]
    assert usage["cuPages"] == 5 and usage["languageRecords"] > 0 and usage["contentSafetyRecords"] > 0
    assert usage["inputTokens"] > 0 and usage["estimatedCostUsd"] > 0
    assert {"cu_layout", "pii", "prompt_shields", "triage", "extract", "verify", "merge"} <= set(usage["byStepMs"])


def test_assemble_chunks_into_plan(client: TestClient) -> None:
    first = post_chunk(client, [1, 2, 3, 4, 5]).json()
    second = post_chunk(client, [5, 6]).json()
    response = client.post(
        "/api/plan/assemble",
        json={
            "region": "CA",
            "documentName": "northwind.pdf",
            "pageCount": 6,
            "chunks": [{"pages": c["pages"], "rows": c["rows"]} for c in (first, second)],
        },
    )
    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert plan["insurer"] == "Northwind Life & Health"
    assert plan["profile"] == {
        "kind": "CA",
        "province": "ON",
        "hsa": {"annualCreditCents": 50000, "carryForwardYears": 1},
        "spousePlan": False,
    }
    assert plan["claimRules"]["daysAfterPeriodEnd"] == 90
    assert [m["alias"] for m in plan["members"]] == ["[MEMBER_A]", "[MEMBER_B]"]
    assert plan["identifiers"] == ["[CERT_1]", "[POLICY_1]"]


def test_pii_hook_returns_422_with_page_and_polygon(client: TestClient) -> None:
    response = post_chunk(client, [1, 2, 3], headers={"x-benefura-fake-pii": "2:CASocialInsuranceNumber"})
    body = assert_error(response, 422, "pii_detected")
    assert body["error"]["details"] == [
        {"page": 2, "category": "CASocialInsuranceNumber", "polygon": [1.0, 1.0, 3.5, 1.0, 3.5, 1.4, 1.0, 1.4]}
    ]


def test_advisory_pii_hook_is_an_issue_not_an_error(client: TestClient) -> None:
    response = post_chunk(client, [1, 2], headers={"x-benefura-fake-pii": "1:Organization"})
    assert response.status_code == 200
    issues = [i for i in response.json()["issues"] if i["code"] == "pii_advisory"]
    assert issues == [
        {
            "code": "pii_advisory",
            "severity": "info",
            "message": issues[0]["message"],
            "page": 1,
            "category": "Organization",
            "rowId": None,
        }
    ]


def test_injection_hook_excludes_page(client: TestClient) -> None:
    response = post_chunk(client, [1, 2, 3], headers={"x-benefura-fake-injection": "2"})
    assert response.status_code == 200
    body = response.json()
    codes = {(i["code"], i["page"]) for i in body["issues"]}
    assert ("prompt_injection", 2) in codes and ("page_excluded", 2) in codes
    assert all(row["page"] != 2 for row in body["rows"]["benefits"])
    assert any(row["page"] == 3 for row in body["rows"]["benefits"])


def test_content_filter_on_chunk_is_200_with_issue(client: TestClient) -> None:
    response = post_chunk(client, [1, 2], headers={"x-benefura-fake-content-filter": "1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"]["benefits"] == []
    assert any(i["code"] == "content_filtered" for i in body["issues"])


def test_hooks_ignored_outside_fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.fake_hooks import current_hooks

    seen = []

    async def fake_pipeline(*args: Any, **kwargs: Any) -> Any:
        seen.append(current_hooks())
        raise RuntimeError("stop")

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AI_MODE", "live")
    get_settings.cache_clear()
    monkeypatch.setattr("app.pipelines.chunk.analyze_chunk", fake_pipeline)
    with TestClient(create_app()) as live_client:
        response = post_chunk(live_client, [1], headers={"x-benefura-fake-pii": "1:CASocialInsuranceNumber"})
    assert_error(response, 500, "internal_error")
    assert seen and seen[0].pii is None


def test_validation_error_is_400_without_echo(client: TestClient) -> None:
    response = client.post(
        "/api/plan/analyze-chunk",
        data={"documentId": DOC_ID, "region": "NZ-SECRET-VALUE", "pages": "1"},
        files={"file": ("chunk.pdf", image_pdf(1), "application/pdf")},
    )
    body = assert_error(response, 400, "invalid_request")
    assert "NZ-SECRET-VALUE" not in response.text
    assert "region" in body["error"]["message"]


@pytest.mark.parametrize(
    ("pages", "pdf_pages", "code", "status"),
    [
        ([1, 2, 3], 2, "invalid_request", 400),
        ([1, 2, 3, 4, 5, 6, 7], 7, "invalid_request", 400),
        ([3, 2], 2, "invalid_request", 400),
        ([121], 1, "page_cap_exceeded", 413),
    ],
)
def test_chunk_page_validation(client: TestClient, pages: list[int], pdf_pages: int, code: str, status: int) -> None:
    assert_error(post_chunk(client, pages, pdf=image_pdf(pdf_pages)), status, code)


def test_text_layer_pdf_is_refused(client: TestClient) -> None:
    assert_error(post_chunk(client, [1], pdf=text_pdf()), 400, "invalid_request")


def test_bad_document_id(client: TestClient) -> None:
    assert_error(post_chunk(client, [1], document_id="short"), 400, "invalid_request")


def test_generic_content_type_is_sniffed(client: TestClient) -> None:
    assert post_chunk(client, [1], content_type="application/octet-stream").status_code == 200


def test_fake_mode_relaxes_caps_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    relaxed = Settings()
    assert relaxed.rate_limit_per_minute == 600 and relaxed.max_booklets_per_ip_per_day == 1000
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    assert Settings().rate_limit_per_minute == 5
    monkeypatch.setenv("AI_MODE", "live")
    assert Settings().max_booklets_per_ip_per_day == 3 and Settings().daily_budget_usd_extraction == 3.0


def test_unsupported_media_type(client: TestClient) -> None:
    assert_error(post_chunk(client, [1], pdf=png_bytes(), content_type="image/png"), 415, "unsupported_media_type")
    assert_error(
        post_chunk(client, [1], pdf=png_bytes(), content_type="application/pdf"), 415, "unsupported_media_type"
    )


def test_body_too_large_by_content_length(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CHUNK_BYTES", "2048")
    monkeypatch.setenv("MULTIPART_OVERHEAD_BYTES", "1024")
    from app.config import get_settings

    get_settings.cache_clear()
    response = post_chunk(client, [1], pdf=b"%PDF-" + b"0" * 8000)
    body = assert_error(response, 413, "payload_too_large")
    assert body["error"]["message"] == "Request body too large"


def test_body_too_large_when_streamed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_ASSEMBLE_BODY_BYTES", "1000")
    from app.config import get_settings

    get_settings.cache_clear()

    def chunks() -> Any:
        for _ in range(10):
            yield b'{"padding": "' + b"x" * 400 + b'"}'

    response = client.post("/api/plan/assemble", content=chunks(), headers={"content-type": "application/json"})
    assert_error(response, 413, "payload_too_large")


def test_rate_limit_is_429_with_retry_after(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    from app.config import get_settings
    from app.limits import reset_limits

    get_settings.cache_clear()
    reset_limits()
    headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
    for _ in range(2):
        assert client.post("/api/plan/assemble", json={}, headers=headers).status_code == 400
    response = client.post("/api/plan/assemble", json={}, headers=headers)
    body = assert_error(response, 429, "rate_limited")
    assert int(response.headers["retry-after"]) >= 1
    assert body["error"]["retryAfterSeconds"] == int(response.headers["retry-after"])
    other_ip = client.post("/api/plan/assemble", json={}, headers={"x-forwarded-for": "198.51.100.7"})
    assert other_ip.status_code == 400
    assert client.get("/healthz", headers=headers).status_code == 200


def test_booklet_cap_is_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_BOOKLETS_PER_IP_PER_DAY", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    assert post_chunk(client, [1], document_id="booklet-one").status_code == 200
    assert post_chunk(client, [2], document_id="booklet-one").status_code == 200
    response = post_chunk(client, [1], document_id="booklet-two")
    body = assert_error(response, 429, "booklet_cap_exceeded")
    assert body["error"]["retryAfterSeconds"] >= 1


def test_budget_exhausted_is_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_BUDGET_USD_EXTRACTION", "0.000001")
    from app.config import get_settings

    get_settings.cache_clear()
    assert post_chunk(client, [1, 2]).status_code == 200
    response = post_chunk(client, [3])
    assert_error(response, 429, "budget_exhausted")
    assert client.get("/healthz").json()["budgets"]["demo-extraction"] == 0.0
    assert client.get("/healthz").json()["budgets"]["demo-chat"] == 100.0


def test_ai_off_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AI_MODE", "off")
    get_settings.cache_clear()
    with TestClient(create_app()) as off_client:
        assert_error(post_chunk(off_client, [1]), 503, "ai_disabled")
        health = off_client.get("/healthz").json()
        assert health["aiEnabled"] is False


def test_unhandled_error_is_generic_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret request detail")

    monkeypatch.setattr("app.pipelines.chunk.analyze_chunk", boom)
    response = post_chunk(client, [1])
    assert_error(response, 500, "internal_error")
    assert "secret" not in response.text


def test_not_found_has_trace(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert len(response.headers[TRACE]) == 32
