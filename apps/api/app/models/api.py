from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.extraction import (
    ExBenefit,
    ExCostShare,
    ExHeader,
    ExHospitalCategory,
    ExPool,
    ExRule,
    RowMeta,
)
from app.models.plan import Plan, Region
from app.models.receipt import FieldConfidence, Receipt

AiMode = Literal["live", "fake", "off"]
BudgetNamespace = Literal["demo-extraction", "demo-chat", "evals"]
AgentName = Literal["plan_claims", "knowledge"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IssueCode = Literal[
    "pii_advisory",
    "prompt_injection",
    "page_excluded",
    "page_irrelevant",
    "unsupported_row",
    "low_confidence",
    "ungrounded_quote",
    "verifier_rounds_exhausted",
    "duplicate_removed",
    "unlinked_pool",
    "unlinked_cost_share",
    "ocr_empty",
    "field_missing",
    "content_filtered",
    "assumed_default",
]


class Issue(ApiModel):
    code: IssueCode
    severity: Literal["info", "warning", "error"]
    message: str
    page: int | None = None
    category: str | None = Field(default=None, description="PII category, never the matched value.")
    rowId: str | None = None


class Usage(ApiModel):
    inputTokens: int = 0
    outputTokens: int = 0
    cuPages: int = 0
    languageRecords: int = 0
    contentSafetyRecords: int = 0
    estimatedCostUsd: float = 0.0
    byStepMs: dict[str, int] = Field(default_factory=dict)


class HealthResponse(ApiModel):
    ok: bool
    aiEnabled: bool
    aiMode: AiMode
    budgetRemainingPct: float = Field(ge=0, le=100, description="Lowest remaining share across demo namespaces.")
    budgets: dict[str, float] = Field(default_factory=dict)
    agentVersions: dict[str, str] = Field(default_factory=dict)
    version: str


class ExHeaderRow(ExHeader):
    meta: RowMeta


class ExBenefitRow(ExBenefit):
    meta: RowMeta


class ExPoolRow(ExPool):
    meta: RowMeta


class ExCostShareRow(ExCostShare):
    meta: RowMeta


class ExRuleRow(ExRule):
    meta: RowMeta


class ExHospitalCategoryRow(ExHospitalCategory):
    meta: RowMeta


class ExtractedRows(ApiModel):
    header: list[ExHeaderRow] = Field(default_factory=list)
    benefits: list[ExBenefitRow] = Field(default_factory=list)
    pools: list[ExPoolRow] = Field(default_factory=list)
    cost_shares: list[ExCostShareRow] = Field(default_factory=list)
    rules: list[ExRuleRow] = Field(default_factory=list)
    hospital_categories: list[ExHospitalCategoryRow] = Field(default_factory=list)


class AnalyzeChunkResponse(ApiModel):
    pages: list[int] = Field(description="1-based booklet page numbers covered by this chunk.")
    rows: ExtractedRows
    issues: list[Issue] = Field(default_factory=list)
    usage: Usage
    traceId: str


class AssembleChunk(ApiModel):
    pages: list[int]
    rows: ExtractedRows


class AssembleRequest(ApiModel):
    region: Region
    documentName: str = "Uploaded booklet"
    pageCount: int = Field(ge=1, le=120)
    chunks: list[AssembleChunk] = Field(max_length=40)


class AssembleResponse(ApiModel):
    plan: Plan
    issues: list[Issue] = Field(default_factory=list)


class ReceiptAnalyzeResponse(ApiModel):
    receipt: Receipt
    fieldConfidence: dict[str, FieldConfidence] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
    usage: Usage
    traceId: str


class ChatContext(ApiModel):
    """Small, aliased context the browser sends with each chat turn (≤2 KB serialized)."""

    region: Region
    today: str = Field(description="ISO date in the user's time zone.")
    tz: str
    currency: Literal["CAD", "AUD"]
    planName: str | None = None
    memberAliases: list[str] = Field(default_factory=list, max_length=12)
    categories: list[str] = Field(default_factory=list, max_length=40)


class ChatRequest(ApiModel):
    messages: list[dict[str, Any]] = Field(max_length=80, description="AI SDK UIMessage objects (id, role, parts).")
    context: ChatContext
    activeAgent: AgentName | None = None


ErrorCode = Literal[
    "payload_too_large",
    "pii_detected",
    "unsafe_image",
    "rate_limited",
    "budget_exhausted",
    "ai_disabled",
    "invalid_request",
    "unsupported_media_type",
    "page_cap_exceeded",
    "booklet_cap_exceeded",
    "upstream_error",
    "content_filtered",
    "prompt_injection",
    "internal_error",
]


class PiiDetectedDetail(ApiModel):
    page: int | None = None
    category: str
    polygon: list[float] | None = Field(
        default=None, description="Page-relative polygon in inches (CU word polygon), if available."
    )


class ErrorBody(ApiModel):
    code: ErrorCode
    message: str
    details: list[PiiDetectedDetail] | None = None
    retryAfterSeconds: int | None = None
    traceId: str | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody
