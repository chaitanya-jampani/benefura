"""Claims live only in the browser; the model is here so TypeScript gets it from the generated schema."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimStatus = Literal["draft", "submitted", "paid", "partially_paid", "rejected"]


class ClaimLine(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    id: str
    serviceDate: date
    benefitId: str
    itemCode: str | None = None
    description: str | None = None
    quantity: float = Field(default=1, gt=0)
    chargedCents: int = Field(ge=0)
    otherPlanPaidCents: int = Field(default=0, ge=0)


class ClaimOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    paidCents: int = Field(ge=0)
    decidedOn: date | None = None
    note: str | None = None


class ClaimAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    receiptId: str
    kind: Literal["receipt", "eob", "referral", "other"] = "receipt"


class ClaimHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    status: ClaimStatus
    at: str = Field(description="ISO timestamp.")
    actor: Literal["user", "agent"]
    agentName: str | None = None
    agentVersion: str | None = None
    traceId: str | None = None
    note: str | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    id: str
    planId: str
    status: ClaimStatus
    patientMemberId: str
    provider: str | None = None
    lines: list[ClaimLine] = Field(default_factory=list)
    outcome: ClaimOutcome | None = None
    attachments: list[ClaimAttachment] = Field(default_factory=list)
    history: list[ClaimHistoryEntry] = Field(default_factory=list)
    deadline: date | None = None
    createdAt: str
    updatedAt: str
