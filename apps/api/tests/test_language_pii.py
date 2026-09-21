from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.services import language_pii
from app.services.language_pii import (
    check_pii,
    find_alias_tokens,
    normalize_aliases,
    normalize_aliases_mapped,
    split_text,
)

ENDPOINT = "https://benefura-test.cognitiveservices.azure.com"
PII_URL = f"{ENDPOINT}/language/:analyze-text"


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("AI_SERVICES_ENDPOINT", ENDPOINT + "/")
    get_settings.cache_clear()

    async def token() -> str:
        return "test-token"

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.rest.get_cognitive_token", token)
    monkeypatch.setattr("app.services.rest.asyncio.sleep", no_sleep)


@pytest.mark.parametrize(
    ("damaged", "expected"),
    [
        ("IMEMBER_A]", "[MEMBER_A]"),
        ("(MEMBER_A)", "[MEMBER_A]"),
        ("{MEMBER_B}", "[MEMBER_B]"),
        ("[MEMBER A]", "[MEMBER_A]"),
        ("lMEMBER_Cl", "[MEMBER_C]"),
        ("[MEMBERA]", "[MEMBER_A]"),
        ("|POLICY_1|", "[POLICY_1]"),
        ("[POLICY_12]", "[POLICY_12]"),
        ("MEMBER_D", "[MEMBER_D]"),
        ("[EMPLOYER-A]", "[EMPLOYER_A]"),
        ("[CUSTOM_TOKEN_9]", "[CUSTOM_TOKEN_9]"),
    ],
)
def test_normalizer_repairs_ocr_damage(damaged: str, expected: str) -> None:
    assert normalize_aliases(f"Paid to {damaged} on file.") == f"Paid to {expected} on file."


@pytest.mark.parametrize("text", ["MEMBERSHIP card", "GROUP 1 benefits", "POLICY 1", "Member A", "COVERAGE TABLE"])
def test_normalizer_leaves_ordinary_text(text: str) -> None:
    assert normalize_aliases(text) == text
    assert find_alias_tokens(text) == []


def test_normalizer_maps_offsets_back_to_original() -> None:
    original = "Spouse: MEMBER_B and (POLICY 1). SIN 046 454 286"
    normalized = normalize_aliases_mapped(original)
    assert normalized.text == "Spouse: [MEMBER_B] and [POLICY_1]. SIN 046 454 286"
    sin = normalized.text.index("046")
    assert original[normalized.to_original(sin) : normalized.to_original(sin + 11)] == "046 454 286"
    assert normalized.alias_overlap(8, 18) == 1.0
    assert find_alias_tokens(original) == ["[MEMBER_B]", "[POLICY_1]"]


def test_split_text_prefers_whitespace_and_keeps_offsets() -> None:
    text = ("word " * 2000).strip()
    pieces = split_text(text, 5120)
    assert all(len(piece) <= 5120 for _, piece in pieces)
    assert "".join(piece for _, piece in pieces) == text
    assert all(text[offset : offset + len(piece)] == piece for offset, piece in pieces)
    assert all(piece.endswith(" ") for _, piece in pieces[:-1])


def entity(text: str, needle: str, category: str, score: float, subcategory: str | None = None) -> dict[str, Any]:
    offset = text.index(needle)
    data: dict[str, Any] = {
        "text": needle,
        "category": category,
        "offset": offset,
        "length": len(needle),
        "confidenceScore": score,
    }
    if subcategory:
        data["subcategory"] = subcategory
    return data


@respx.mock
async def test_two_tier_mapping_against_recorded_response(live: None) -> None:
    original = (
        "Plan member: MEMBER_A\nSIN: 046 454 286\nNorthwind Life & Health, 100 King St W, Toronto\n"
        "Date of birth: 1980-02-03\nPhone 555-0100"
    )
    sent = normalize_aliases(original)
    recorded = {
        "kind": "PiiEntityRecognitionResults",
        "results": {
            "documents": [
                {
                    "id": "0",
                    "entities": [
                        entity(sent, "[MEMBER_A]", "Person", 0.81),
                        entity(sent, "046 454 286", "CASocialInsuranceNumber", 0.95),
                        entity(sent, "Northwind Life & Health", "Organization", 0.92),
                        entity(sent, "100 King St W, Toronto", "Address", 0.7),
                        entity(sent, "1980-02-03", "DateTime", 0.62, subcategory="DateOfBirth"),
                        entity(sent, "555-0100", "PhoneNumber", 0.3),
                    ],
                    "warnings": [],
                }
            ],
            "errors": [],
            "modelVersion": "2026-04-01",
        },
    }
    route = respx.post(PII_URL).mock(return_value=httpx.Response(200, json=recorded))

    result = await check_pii([original], page_numbers=[7])

    request = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.url.params["api-version"] == "2026-05-01"
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert request["kind"] == "PiiEntityRecognition"
    assert request["parameters"]["stringIndexType"] == "UnicodeCodePoint"
    assert "CASocialInsuranceNumber" in request["parameters"]["piiCategories"]
    assert request["analysisInput"]["documents"][0]["text"] == sent

    assert [(h.category, h.tier) for h in result.hard] == [("CASocialInsuranceNumber", "hard")]
    hard = result.hard[0]
    assert original[hard.offset : hard.offset + hard.length] == "046 454 286"
    assert sorted((h.category, h.tier) for h in result.advisory) == [
        ("Address", "advisory"),
        ("DateOfBirth", "advisory"),  # below the hard-tier confidence: reported, never blocking
        ("Organization", "advisory"),
    ]
    assert result.records == 1
    assert result.categories("advisory") == {"Organization": 1, "Address": 1, "DateOfBirth": 1}


@respx.mock
async def test_batches_five_documents_and_splits_long_ones(live: None) -> None:
    long_text = ("coverage " * 700) + "TFN 123 456 782"  # ~6.3K chars → 2 pieces
    texts = [f"page {i}" for i in range(6)] + [long_text, ""]
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        documents = []
        for doc in body["analysisInput"]["documents"]:
            entities = []
            if "TFN" in doc["text"]:
                entities.append(entity(doc["text"], "123 456 782", "AUTaxFileNumber", 0.9))
            documents.append({"id": doc["id"], "entities": entities, "warnings": []})
        return httpx.Response(200, json={"results": {"documents": documents, "errors": []}})

    respx.post(PII_URL).mock(side_effect=respond)
    result = await check_pii(texts)

    assert [len(r["analysisInput"]["documents"]) for r in requests] == [5, 3]
    assert all(len(d["text"]) <= language_pii.MAX_DOC_CHARS for r in requests for d in r["analysisInput"]["documents"])
    assert len(result.hard) == 1 and result.hard[0].doc_index == 6
    assert long_text[result.hard[0].offset : result.hard[0].offset + result.hard[0].length] == "123 456 782"
    long_pieces = [piece for _, piece in split_text(long_text)]
    assert result.records == 6 + sum(language_pii.text_records(piece) for piece in long_pieces)


@respx.mock
async def test_retries_429_then_fails_closed_on_document_errors(live: None) -> None:
    route = respx.post(PII_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json={"results": {"documents": [], "errors": [{"id": "0", "error": {}}]}}),
        ]
    )
    from app.errors import ApiError

    with pytest.raises(ApiError) as caught:
        await check_pii(["some page text"])
    assert caught.value.code == "upstream_error"
    assert route.call_count == 2


@respx.mock
async def test_http_failure_is_upstream_error(live: None) -> None:
    respx.post(PII_URL).mock(return_value=httpx.Response(400, json={"error": {"message": "echo of input"}}))
    from app.errors import ApiError

    with pytest.raises(ApiError) as caught:
        await check_pii(["text"])
    assert caught.value.code == "upstream_error" and "echo" not in caught.value.message


async def test_fake_mode_hook_targets_page() -> None:
    from app.fake_hooks import FakeHooks, reset_hooks, use_hooks

    token = use_hooks(FakeHooks(pii=(5, "CAHealthServiceNumber")))
    try:
        result = await check_pii(["a", "b"], page_numbers=[4, 5])
        missed = await check_pii(["a"], page_numbers=[1])
    finally:
        reset_hooks(token)
    assert [(h.doc_index, h.tier) for h in result.hard] == [(1, "hard")]
    assert missed.hard == [] and missed.advisory == []
