"""Extractor structured output. Strict mode requires every field, so optional ones are nullable without
defaults, and rows stay flat to keep nesting and property counts low. Money is in dollars because models
read "$1,500.00" more reliably than they convert it; ``assemble.py`` converts to cents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.plan import VerifierVerdict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExHeader(Strict):
    row_id: str
    page: int
    quote: str
    insurer: str | None
    plan_name: str | None
    currency: Literal["CAD", "AUD"] | None
    effective_date: str | None
    benefit_period_kind: Literal["benefit_year", "policy_anniversary"] | None
    benefit_period_start_month: int | None
    province: str | None
    cover_type: Literal["hospital", "extras", "combined"] | None
    hospital_tier: Literal["gold", "silver", "bronze", "basic"] | None
    hospital_plus: bool | None
    hsa_annual_credit: float | None
    hsa_carry_forward_years: int | None


class ExBenefit(Strict):
    row_id: str
    page: int
    quote: str
    category_name: str
    category_kind: str | None
    benefit_name: str
    keywords: str | None = Field(description="Comma-separated synonyms, e.g. 'RMT, massage therapy'.")
    item_codes: str | None = Field(description="Comma-separated item codes, if the table lists them.")
    coverage_kind: Literal["percent", "percent_capped", "fixed_per_service", "per_diem", "schedule"] | None
    coverage_percent: float | None
    coverage_cap_amount: float | None
    coverage_amount: float | None
    limit_unit: Literal["dollars", "visits", "items", "days", "hours"] | None
    limit_value: float | None
    limit_period_kind: str | None
    limit_period_months: int | None
    limit_scope: Literal["per_person", "per_family", "per_policy"] | None
    frequency_count: int | None
    frequency_period_kind: str | None
    frequency_period_months: int | None
    waiting_period_months: int | None
    requirements: str | None
    pool_name: str | None
    notes: str | None


class ExPool(Strict):
    row_id: str
    page: int
    quote: str
    pool_name: str
    limit_unit: Literal["dollars", "visits", "items", "days", "hours"] | None
    limit_value: float | None
    period_kind: str | None
    period_months: int | None
    scope: Literal["per_person", "per_family", "per_policy"] | None
    benefit_names: str | None = Field(description="Comma-separated benefit names sharing this maximum.")


class ExCostShare(Strict):
    row_id: str
    page: int
    quote: str
    name: str
    kind: Literal["deductible", "excess", "copay", "coinsurance"] | None
    amount: float | None
    percent: float | None
    period_kind: str | None
    scope: Literal["per_person", "per_family", "per_policy"] | None
    applies_to: str | None = Field(description="Comma-separated category or benefit names.")


class ExRule(Strict):
    row_id: str
    page: int
    quote: str
    rule_kind: Literal["submission_deadline", "receipts_required", "coordination_of_benefits", "other"]
    deadline_days: int | None
    deadline_basis: Literal["service_date", "period_end"] | None
    text: str | None


class ExHospitalCategory(Strict):
    row_id: str
    page: int
    quote: str
    name: str
    status: Literal["covered", "restricted", "excluded"] | None


class ExtractionChunk(Strict):
    header: list[ExHeader]
    benefits: list[ExBenefit]
    pools: list[ExPool]
    cost_shares: list[ExCostShare]
    rules: list[ExRule]
    hospital_categories: list[ExHospitalCategory]


RowKind = Literal["header", "benefit", "pool", "cost_share", "rule", "hospital_category"]


class PageTriage(Strict):
    page: int
    relevant: bool
    sectionType: Literal[
        "cover",
        "contents",
        "definitions",
        "benefit_table",
        "benefit_detail",
        "claims",
        "exclusions",
        "hospital",
        "general",
        "other",
    ]


class PageTriageResult(Strict):
    pages: list[PageTriage]


class RowVerdict(Strict):
    row_id: str
    verdict: VerifierVerdict
    correction: str | None = Field(description="JSON object of corrected fields when verdict is 'corrected'.")
    reason: str | None


class VerifierResult(Strict):
    verdicts: list[RowVerdict]


class RowMeta(BaseModel):
    """Per-row provenance attached after verification and deterministic grounding."""

    model_config = ConfigDict(extra="forbid")

    rowId: str
    kind: RowKind
    page: int
    confidence: float = Field(ge=0, le=1)
    verifierVerdict: VerifierVerdict | None = None
    grounded: bool = Field(description="True when the quote fuzzy-matches the page markdown.")
