"""Plan schema source of truth. Money is integer cents; camelCase because the browser engine reads it as-is."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal[1] = 1

Region = Literal["CA", "AU"]
Currency = Literal["CAD", "AUD"]
Scope = Literal["per_person", "per_family", "per_policy"]
VerifierVerdict = Literal["supported", "corrected", "unsupported"]

PeriodKind = Literal[
    "benefit_year",
    "policy_anniversary",
    "rolling_months",
    "consecutive_benefit_years",
    "lifetime",
    "per_visit",
    "per_admission",
]

CategoryKind = Literal[
    "paramedical",
    "vision",
    "dental",
    "drugs",
    "hospital",
    "extras",
    "medical_equipment",
    "ambulance",
    "travel",
    "hsa",
    "other",
]

CA_PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")
Province = Literal["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, json_schema_serialization_defaults_required=True)


class SourceRef(Model):
    """Where a value came from in the (redacted) booklet."""

    page: int = Field(ge=1)
    quote: str = Field(max_length=600)
    confidence: float = Field(ge=0, le=1)
    verifierVerdict: VerifierVerdict | None = None


class Period(Model):
    """A window that limits, frequencies and deductibles reset on.

    - ``benefit_year``: calendar-style year starting ``startMonth``/``startDay`` (Jan 1 when omitted).
    - ``policy_anniversary``: year starting on the plan's ``effectiveDate`` anniversary.
    - ``rolling_months``: any ``months``-long window ending on the service date.
    - ``consecutive_benefit_years``: ``years`` benefit years in a row (e.g. glasses every 2 years).
    - ``lifetime``: never resets.
    - ``per_visit`` / ``per_admission``: applies to each visit or hospital admission.
    """

    kind: PeriodKind
    startMonth: int | None = Field(default=None, ge=1, le=12)
    startDay: int | None = Field(default=None, ge=1, le=31)
    months: int | None = Field(default=None, ge=1, le=120)
    years: int | None = Field(default=None, ge=1, le=10)


class ScheduleItem(Model):
    itemCode: str
    description: str
    benefitCents: int = Field(ge=0)


class Coverage(Model):
    """How much of an eligible charge the plan pays.

    - ``percent``: ``percent`` of the charge.
    - ``percent_capped``: ``percent`` of the charge, at most ``capCents`` per service.
    - ``fixed_per_service``: ``amountCents`` per service (never more than the charge).
    - ``per_diem``: ``amountCents`` per day (quantity = days).
    - ``schedule``: benefit looked up by item code in ``scheduleItems`` (AU extras), else ``percent``
      as a fallback when set.
    """

    kind: Literal["percent", "percent_capped", "fixed_per_service", "per_diem", "schedule"]
    percent: float | None = Field(default=None, ge=0, le=100)
    capCents: int | None = Field(default=None, ge=0)
    amountCents: int | None = Field(default=None, ge=0)
    scheduleNote: str | None = None
    scheduleItems: list[ScheduleItem] = Field(default_factory=list)


class Limit(Model):
    """A maximum. ``unit == "cents"`` means ``value`` is money in cents; otherwise a count."""

    unit: Literal["cents", "visits", "items", "days", "hours"]
    value: int = Field(ge=0)
    period: Period
    scope: Scope = "per_person"


class Frequency(Model):
    """At most ``count`` services per ``period`` (e.g. one eye exam every 24 months)."""

    count: int = Field(ge=1)
    period: Period
    scope: Scope = "per_person"


class WaitingPeriod(Model):
    months: int = Field(ge=0, le=60)
    note: str | None = None


class Benefit(Model):
    id: str
    categoryId: str
    name: str
    keywords: list[str] = Field(default_factory=list)
    itemCodes: list[str] = Field(default_factory=list)
    coverage: Coverage
    limits: list[Limit] = Field(default_factory=list)
    frequency: Frequency | None = None
    waitingPeriod: WaitingPeriod | None = None
    requirements: list[str] = Field(default_factory=list)
    poolId: str | None = None
    costShareIds: list[str] = Field(default_factory=list)
    notes: str | None = None
    source: SourceRef | None = None


class Category(Model):
    id: str
    name: str
    kind: CategoryKind
    benefits: list[Benefit] = Field(default_factory=list)


class LimitPool(Model):
    """A combined maximum shared by several benefits (e.g. paramedical combined $1,500)."""

    id: str
    name: str
    limit: Limit
    benefitIds: list[str] = Field(default_factory=list)
    source: SourceRef | None = None


class CostShare(Model):
    """Something the member pays before or alongside the plan.

    ``deductible`` (CA) and ``excess`` (AU hospital) are amounts per ``period``; ``copay`` is a fixed
    amount per service; ``coinsurance`` is informational because coverage percent already encodes it.
    """

    id: str
    name: str
    kind: Literal["deductible", "excess", "copay", "coinsurance"]
    amountCents: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    period: Period | None = None
    scope: Scope = "per_person"
    appliesToCategoryIds: list[str] = Field(default_factory=list)
    appliesToBenefitIds: list[str] = Field(default_factory=list)
    source: SourceRef | None = None


class ClaimRules(Model):
    """Deadline: ``submissionDays`` after the service date, or ``daysAfterPeriodEnd`` after the end
    of the benefit period the service fell in. When both are set, the later date applies."""

    submissionDays: int | None = Field(default=None, ge=1, le=1095)
    daysAfterPeriodEnd: int | None = Field(default=None, ge=0, le=730)
    receiptsRequired: bool = True
    coordinationOfBenefits: bool = False
    notes: list[str] = Field(default_factory=list)
    source: SourceRef | None = None


class Member(Model):
    id: str
    alias: str = Field(description="Alias token such as [MEMBER_A]; the raw name never leaves the browser.")
    relationship: Literal["self", "spouse", "child", "dependent"]


class HealthSpendingAccount(Model):
    annualCreditCents: int = Field(ge=0)
    carryForwardYears: int = Field(default=0, ge=0, le=2)


class CAProfile(Model):
    kind: Literal["CA"] = "CA"
    province: Province
    hsa: HealthSpendingAccount | None = None
    spousePlan: bool = False


class HospitalCategory(Model):
    name: str
    status: Literal["covered", "restricted", "excluded"]
    source: SourceRef | None = None


class HospitalExcess(Model):
    amountCents: int = Field(ge=0)
    per: Literal["admission", "year"]
    maxPerYearCents: int | None = Field(default=None, ge=0)


class HospitalCover(Model):
    tier: Literal["gold", "silver", "bronze", "basic"]
    plus: bool = False
    categories: list[HospitalCategory] = Field(default_factory=list)
    excess: HospitalExcess | None = None


class AUProfile(Model):
    kind: Literal["AU"] = "AU"
    coverType: Literal["hospital", "extras", "combined"]
    hospital: HospitalCover | None = None


Profile = Annotated[CAProfile | AUProfile, Field(discriminator="kind")]


class PlanDocument(Model):
    name: str
    pageCount: int = Field(ge=0)
    extractedAt: str | None = None
    isDemo: bool = False


class Plan(Model):
    schemaVersion: Literal[1] = SCHEMA_VERSION
    id: str
    region: Region
    insurer: str
    planName: str
    currency: Currency
    identifiers: list[str] = Field(
        default_factory=list, description="Alias tokens for policy/group/certificate numbers, e.g. [POLICY_1]."
    )
    members: list[Member] = Field(default_factory=list)
    effectiveDate: date | None = None
    benefitPeriod: Period
    categories: list[Category] = Field(default_factory=list)
    limitPools: list[LimitPool] = Field(default_factory=list)
    costShares: list[CostShare] = Field(default_factory=list)
    claimRules: ClaimRules = Field(default_factory=ClaimRules)
    profile: Profile
    document: PlanDocument | None = None
