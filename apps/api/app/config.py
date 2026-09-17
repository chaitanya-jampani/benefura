"""Runtime settings, read from environment variables (see ``infra/modules/aca.bicep``)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

VERSION = "0.1.0"
_PARENTS = Path(__file__).resolve().parents
REPO_ROOT = _PARENTS[3] if len(_PARENTS) > 3 else _PARENTS[-1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "off" answers every AI endpoint with 503 ai_disabled; /healthz keeps working.
    ai_mode: Literal["live", "fake", "off"] = "fake"

    foundry_project_endpoint: str = ""  # https://<account>.services.ai.azure.com/api/projects/<project>
    ai_services_endpoint: str = ""  # https://<account>.cognitiveservices.azure.com/
    azure_client_id: str | None = None  # user-assigned managed identity in Container Apps

    chat_model: str = "gpt-5-mini"
    nano_model: str = "gpt-5-nano"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 512
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"

    cu_api_version: str = "2025-11-01"
    cu_receipt_analyzer_id: str = "benefura-receipt"
    language_api_version: str = "2026-05-01"
    content_safety_api_version: str = "2024-09-01"

    search_endpoint: str = ""
    search_index: str = "public-knowledge-v1"
    search_semantic_config: str = "default"  # M0-verify: must match knowledge/index_schema.json
    search_vector_field: str = "contentVector"

    storage_table_endpoint: str = ""  # https://<account>.table.core.windows.net
    budget_table: str = "budgets"

    applicationinsights_connection_string: str | None = None

    # NoDecode: comma-separated or a JSON list, parsed below.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Fake mode reads samples/golden/<doc>.pages.json and <doc>.extraction.json from here.
    samples_dir: Path = REPO_ROOT / "samples"
    # Unlocks the evals budget namespace through `x-benefura-evals-key`.
    evals_shared_secret: str = ""

    daily_budget_usd_extraction: float = 3.0
    daily_budget_usd_chat: float = 2.0
    daily_budget_usd_evals: float = 5.0

    rate_limit_per_minute: int = 30
    # Spoofing caveat: see client_ip in app/limits.py.
    client_ip_hop: Literal["first", "last"] = "first"
    max_booklet_pages: int = 120
    max_booklets_per_ip_per_day: int = 3
    max_chunk_pages: int = 6
    max_receipt_pages: int = 3
    max_chunk_bytes: int = 8 * 1024 * 1024
    max_receipt_bytes: int = 4 * 1024 * 1024
    max_chat_body_bytes: int = 256 * 1024
    max_assemble_body_bytes: int = 2 * 1024 * 1024
    max_default_body_bytes: int = 64 * 1024
    multipart_overhead_bytes: int = 64 * 1024
    require_image_only_pdf: bool = True

    # M0-verify: calibrate the PII confidence thresholds on the sample booklets.
    pii_hard_min_confidence: float = 0.8
    pii_advisory_min_confidence: float = 0.5
    content_safety_reject_severity: int = 4
    max_tool_steps: int = 6
    verifier_max_rounds: int = 2
    verifier_unsupported_threshold: float = 0.15

    agent_versions: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            if v.strip().startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("agent_versions", mode="before")
    @classmethod
    def _parse_versions(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v

    @model_validator(mode="after")
    def _relax_caps_in_fake_mode(self) -> Settings:
        """Fake mode serves tests from one IP with no real spend, so caps relax unless set explicitly."""
        if self.ai_mode == "fake":
            relaxed = {
                "rate_limit_per_minute": 600,
                "max_booklets_per_ip_per_day": 1000,
                "daily_budget_usd_extraction": 1000.0,
                "daily_budget_usd_chat": 1000.0,
            }
            for name, value in relaxed.items():
                if name not in self.model_fields_set:
                    setattr(self, name, value)
        return self

    @property
    def ai_enabled(self) -> bool:
        return self.ai_mode != "off"


@lru_cache
def get_settings() -> Settings:
    return Settings()
