"""Hybrid search over public reference content; not sensitive, but query text is still never logged."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.errors import ApiError
from app.models.plan import Region
from app.telemetry import record_search

if TYPE_CHECKING:
    from azure.search.documents.aio import SearchClient

SELECT_FIELDS = ["id", "content", "title", "url", "region", "publisher", "license", "attribution", "section"]


@dataclass(frozen=True)
class SearchHit:
    id: str
    content: str
    title: str
    url: str
    region: str
    publisher: str
    license: str
    attribution: str
    section: str | None = None
    score: float = 0.0
    reranker_score: float | None = None


@dataclass
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    latency_ms: int = 0
    embedding_tokens: int = 0


@lru_cache
def get_search_client() -> SearchClient:
    from azure.search.documents.aio import SearchClient

    from app.services.foundry import get_credential

    settings = get_settings()
    if not settings.search_endpoint:
        raise RuntimeError("SEARCH_ENDPOINT is not set")
    return SearchClient(settings.search_endpoint, settings.search_index, get_credential())


async def close_search_client() -> None:
    if get_search_client.cache_info().currsize:
        await get_search_client().close()
    get_search_client.cache_clear()


def region_filter(region: Region) -> str:
    # Region is a validated literal, so the quotes are all the OData escaping it needs.
    return f"region eq '{region}'"


def to_hit(doc: dict[str, Any]) -> SearchHit:
    return SearchHit(
        id=str(doc.get("id", "")),
        content=str(doc.get("content", "")),
        title=str(doc.get("title", "")),
        url=str(doc.get("url", "")),
        region=str(doc.get("region", "")),
        publisher=str(doc.get("publisher", "")),
        license=str(doc.get("license", "")),
        attribution=str(doc.get("attribution", "")),
        section=doc.get("section"),
        score=float(doc.get("@search.score") or 0.0),
        reranker_score=(float(r) if (r := doc.get("@search.reranker_score")) is not None else None),
    )


async def embed_query(query: str) -> tuple[list[float], int]:
    from app.services.foundry import get_openai_client

    settings = get_settings()
    response = await get_openai_client().embeddings.create(
        model=settings.embedding_model, input=query, dimensions=settings.embedding_dimensions
    )
    tokens = response.usage.total_tokens if response.usage else 0
    return list(response.data[0].embedding), tokens


async def hybrid_search(query: str, region: Region, top_k: int = 5) -> SearchResult:
    settings = get_settings()
    top_k = max(1, min(top_k, 10))
    started = perf_counter()
    if settings.ai_mode != "live":
        result = SearchResult(hits=fake_search(query, region, top_k))
    else:
        result = await _live_search(query, region, top_k)
    result.latency_ms = int((perf_counter() - started) * 1000)
    top = result.hits[0].reranker_score if result.hits and result.hits[0].reranker_score is not None else None
    record_search(
        result.latency_ms, len(result.hits), top if top is not None else (result.hits[0].score if result.hits else None)
    )
    return result


async def _live_search(query: str, region: Region, top_k: int) -> SearchResult:
    from azure.core.exceptions import HttpResponseError
    from azure.search.documents.models import VectorizedQuery

    settings = get_settings()
    vector, tokens = await embed_query(query)
    try:
        results = await get_search_client().search(
            search_text=query,
            vector_queries=[
                VectorizedQuery(
                    vector=vector, k_nearest_neighbors=max(top_k * 5, 25), fields=settings.search_vector_field
                )
            ],
            filter=region_filter(region),
            query_type="semantic",
            semantic_configuration_name=settings.search_semantic_config,
            select=SELECT_FIELDS,
            top=top_k,
        )
        hits = [to_hit(doc) async for doc in results]
    except HttpResponseError as exc:
        raise ApiError("upstream_error", "Knowledge search is unavailable; try again shortly.") from exc
    return SearchResult(hits=hits, embedding_tokens=tokens)


_FAKE_CORPUS: tuple[dict[str, Any], ...] = (
    {
        "id": "ca-cra-medical-expenses-1",
        "region": "CA",
        "title": "Lines 33099 and 33199 – Eligible medical expenses you can claim on your tax return",
        "url": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/deductions-credits-expenses/lines-33099-33199-eligible-medical-expenses-you-claim-on-your-tax-return.html",
        "publisher": "Canada Revenue Agency",
        "license": "Open Government Licence – Canada",
        "attribution": "Contains information licensed under the Open Government Licence – Canada.",
        "section": "Massage therapy",
        "content": "Amounts paid to a massage therapist are eligible medical expenses only when the therapist is "
        "a medical practitioner in the province where the service is provided. Premiums and amounts reimbursed "
        "by a private health plan cannot be claimed.",
    },
    {
        "id": "ca-pshcp-paramedical-1",
        "region": "CA",
        "title": "Public Service Health Care Plan – Extended health provision",
        "url": "https://www.canada.ca/en/treasury-board-secretariat/services/benefit-plans/health-care-plan.html",
        "publisher": "Treasury Board of Canada Secretariat",
        "license": "Open Government Licence – Canada",
        "attribution": "Contains information licensed under the Open Government Licence – Canada.",
        "section": "Paramedical services",
        "content": "Paramedical services such as physiotherapy and massage therapy are typically reimbursed at a "
        "percentage of eligible expenses up to an annual maximum per person, and a claim deadline applies.",
    },
    {
        "id": "au-phi-waiting-periods-1",
        "region": "AU",
        "title": "Waiting periods",
        "url": "https://www.privatehealth.gov.au/health_insurance/waiting_periods/",
        "publisher": "Commonwealth Ombudsman (privatehealth.gov.au)",
        "license": "CC BY 3.0 AU",
        "attribution": "Source: privatehealth.gov.au, licensed under CC BY 3.0 AU.",
        "section": "Extras waiting periods",
        "content": "Insurers can apply waiting periods before you can claim benefits. For extras such as "
        "physiotherapy, waiting periods are set by each insurer, commonly two months for general treatment.",
    },
    {
        "id": "au-phi-extras-1",
        "region": "AU",
        "title": "Extras (general treatment) cover",
        "url": "https://www.privatehealth.gov.au/health_insurance/what_is_covered/generaltreatment.htm",
        "publisher": "Commonwealth Ombudsman (privatehealth.gov.au)",
        "license": "CC BY 3.0 AU",
        "attribution": "Source: privatehealth.gov.au, licensed under CC BY 3.0 AU.",
        "section": "Benefit limits",
        "content": "Extras policies pay benefits for services such as dental, optical and physiotherapy up to "
        "annual limits, which may be combined across several services.",
    },
)


def fake_search(query: str, region: Region, top_k: int) -> list[SearchHit]:
    terms = {t for t in re.findall(r"[a-z]{3,}", query.lower())}
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for doc in _FAKE_CORPUS:
        if doc["region"] != region:
            continue
        words = set(re.findall(r"[a-z]{3,}", f"{doc['title']} {doc['section']} {doc['content']}".lower()))
        overlap = len(terms & words)
        scored.append((overlap / max(1, len(terms)), doc["id"], doc))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        to_hit({**doc, "@search.score": round(score, 4), "@search.reranker_score": round(score * 4, 4)})
        for score, _, doc in scored[:top_k]
    ]
