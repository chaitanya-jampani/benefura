"""Receipt / EOB fields returned by the ``benefura-receipt`` Content Understanding analyzer."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReceiptLine(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    serviceDate: date | None = None
    description: str | None = None
    itemCode: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    amountCents: int | None = Field(default=None, ge=0)


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    providerName: str | None = None
    providerType: str | None = None
    providerRegistrationNo: str | None = None
    serviceLines: list[ReceiptLine] = Field(default_factory=list)
    totalCents: int | None = Field(default=None, ge=0)
    insurerPaidCents: int | None = Field(default=None, ge=0)
    currency: Literal["CAD", "AUD"] | None = None


class FieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    confidence: float = Field(ge=0, le=1)
    source: str | None = Field(default=None, description="CU source string, e.g. D(1,0.5,1.2,...).")
