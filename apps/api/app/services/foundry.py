"""Keyless Entra ID clients; in Container Apps ``AZURE_CLIENT_ID`` selects the managed identity."""

from __future__ import annotations

from functools import lru_cache

from azure.ai.projects.aio import AIProjectClient
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from openai import AsyncOpenAI

from app.config import get_settings

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"


@lru_cache
def get_credential() -> AsyncTokenCredential:
    settings = get_settings()
    return DefaultAzureCredential(
        managed_identity_client_id=settings.azure_client_id,
        exclude_interactive_browser_credential=True,
    )


@lru_cache
def get_project_client() -> AIProjectClient:
    settings = get_settings()
    if not settings.foundry_project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not set")
    return AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=get_credential())


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    return get_project_client().get_openai_client()


async def get_cognitive_token() -> str:
    token = await get_credential().get_token(COGNITIVE_SCOPE)
    return token.token


def ai_services_url(path: str) -> str:
    base = get_settings().ai_services_endpoint.rstrip("/")
    if not base:
        raise RuntimeError("AI_SERVICES_ENDPOINT is not set")
    return f"{base}/{path.lstrip('/')}"


async def close_clients() -> None:
    if get_openai_client.cache_info().currsize:
        await get_openai_client().close()
    if get_project_client.cache_info().currsize:
        await get_project_client().close()
    if get_credential.cache_info().currsize:
        await get_credential().close()  # type: ignore[attr-defined]
