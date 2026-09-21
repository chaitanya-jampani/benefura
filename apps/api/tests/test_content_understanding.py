from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.budget import UsageTracker
from app.errors import ApiError, ContentFilteredError
from app.services import content_understanding as cu
from tests.conftest import RECORDED, image_pdf

ENDPOINT = "https://benefura-test.cognitiveservices.azure.com"


def recorded(name: str) -> dict[str, Any]:
    return json.loads((RECORDED / name).read_text())["result"]


class FakePoller:
    def __init__(self, result: dict[str, Any] | Exception, operation_id: str = "op-123") -> None:
        self._result = result
        self.operation_id = operation_id

    async def result(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return SimpleNamespace(as_dict=lambda: self._result)


class FakeCuClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.analyzed: list[tuple[str, str, int]] = []
        self.deleted: list[str] = []

    async def begin_analyze_binary(self, analyzer_id: str, data: bytes, *, content_type: str) -> FakePoller:
        self.analyzed.append((analyzer_id, content_type, len(data)))
        return FakePoller(self.result)

    async def delete_result(self, operation_id: str) -> None:
        self.deleted.append(operation_id)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("AI_SERVICES_ENDPOINT", ENDPOINT)
    get_settings.cache_clear()

    async def token() -> str:
        return "test-token"

    monkeypatch.setattr("app.services.rest.get_cognitive_token", token)


def install(monkeypatch: pytest.MonkeyPatch, client: FakeCuClient) -> None:
    monkeypatch.setattr(cu, "get_cu_client", lambda: client)


def test_source_polygon_parsing() -> None:
    assert cu.parse_source_polygon("D(2,1,2,3,2,3,4,1,4)") == (2, (1.0, 2.0, 3.0, 2.0, 3.0, 4.0, 1.0, 4.0))
    assert cu.parse_source_polygon("D(1,1,1,2,0.5)") == (1, (1.0, 1.0, 3.0, 1.0, 3.0, 1.5, 1.0, 1.5))
    assert cu.parse_source_polygon("garbage") is None
    assert cu.union_polygon([(1, 1, 2, 1, 2, 2, 1, 2), (3, 0.5, 4, 0.5, 4, 1, 3, 1)]) == [1, 0.5, 4, 0.5, 4, 2, 1, 2]


def test_parse_layout_splits_pages_and_maps_word_offsets() -> None:
    pages = cu.parse_layout(recorded("cu_layout_result.json"), booklet_pages=[9, 10])
    assert [p.page for p in pages] == [9, 10]
    assert pages[0].markdown.endswith("SIN 046 454 286")
    assert "PageBreak" not in pages[0].markdown and "PageBreak" not in pages[1].markdown
    assert pages[1].markdown.startswith("# Vision care")
    for page in pages:
        for word in page.words:
            assert page.markdown[word.offset : word.offset + word.length] == word.content
    sin = pages[0].markdown.index("046 454 286")
    polygon = cu.words_polygon(pages[0], sin, len("046 454 286"))
    assert polygon == [1.5, 3.0, 2.86, 3.0, 2.86, 3.2, 1.5, 3.2]


async def test_analyze_layout_deletes_result(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeCuClient(recorded("cu_layout_result.json"))
    install(monkeypatch, client)
    result = await cu.analyze_layout(b"%PDF-", booklet_pages=[3, 4], region="CA")
    assert client.analyzed == [("prebuilt-layout", "application/pdf", 5)]
    assert client.deleted == ["op-123"]
    assert [p.page for p in result.pages] == [3, 4] and result.cu_pages == 2


async def test_failed_analysis_still_deletes_and_maps_errors(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from azure.core.exceptions import HttpResponseError

    failure = HttpResponseError(message="analysis failed")
    client = FakeCuClient(failure)
    install(monkeypatch, client)
    with pytest.raises(ApiError) as caught:
        await cu.analyze_layout(b"%PDF-", booklet_pages=[1], region="CA")
    assert caught.value.code == "upstream_error"
    assert client.deleted == ["op-123"]

    blocked = HttpResponseError(message="blocked")
    blocked.error = SimpleNamespace(code="ResponsibleAIPolicyViolation")  # type: ignore[assignment]
    install(monkeypatch, FakeCuClient(blocked))
    with pytest.raises(ContentFilteredError):
        await cu.analyze_receipt(b"\xff\xd8\xff", "image/jpeg", "AU")


def test_parse_receipt_fields_confidence_and_cents() -> None:
    extraction = cu.parse_receipt(recorded("cu_receipt_result.json"), "AU")
    receipt = extraction.receipt
    assert receipt.providerName == "Riverbend Physiotherapy"
    assert receipt.totalCents == 17050 and receipt.currency == "AUD"
    assert [(line.itemCode, line.amountCents, str(line.serviceDate)) for line in receipt.serviceLines] == [
        ("500", 9500, "2026-08-04"),
        ("505", 7550, "2026-08-11"),
    ]
    assert receipt.serviceLines[1].quantity == 1
    assert extraction.field_confidence["serviceLines[1].itemCode"].confidence == 0.41
    assert extraction.field_confidence["totalCents"].source == "D(1,900,700,1060,700,1060,740,900,740)"
    assert set(extraction.missing) == {"providerRegistrationNo", "currency"}
    assert extraction.cu_pages == 1 and extraction.pages[0].markdown.startswith("# Riverbend")


@respx.mock
async def test_chunk_pipeline_maps_hard_hit_to_page_and_polygon(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipelines.chunk import analyze_chunk

    install(monkeypatch, FakeCuClient(recorded("cu_layout_result.json")))

    def respond(request: httpx.Request) -> httpx.Response:
        documents = []
        for doc in json.loads(request.content)["analysisInput"]["documents"]:
            entities = []
            if "046 454 286" in doc["text"]:
                offset = doc["text"].index("046 454 286")
                entities.append(
                    {"category": "CASocialInsuranceNumber", "offset": offset, "length": 11, "confidenceScore": 0.97}
                )
            documents.append({"id": doc["id"], "entities": entities})
        return httpx.Response(200, json={"results": {"documents": documents, "errors": []}})

    respx.post(f"{ENDPOINT}/language/:analyze-text").mock(side_effect=respond)
    shield = respx.post(f"{ENDPOINT}/contentsafety/text:shieldPrompt")
    tracker = UsageTracker()
    with pytest.raises(ApiError) as caught:
        await analyze_chunk(image_pdf(2), region="CA", pages=[9, 10], tracker=tracker)
    err = caught.value
    assert err.code == "pii_detected" and err.status == 422
    assert err.details is not None
    assert [(d.page, d.category, d.polygon) for d in err.details] == [
        (9, "CASocialInsuranceNumber", [1.5, 3.0, 2.86, 3.0, 2.86, 3.2, 1.5, 3.2])
    ]
    assert not shield.called  # blocked before Prompt Shields and any model call
    assert tracker.cu_pages == 2 and tracker.language_records == 2
