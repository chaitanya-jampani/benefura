from __future__ import annotations

from typing import Protocol

from app.models.api import BudgetNamespace
from app.models.plan import Region
from app.services import language_pii
from app.services.language_pii import PiiCheckResult

PII_WARNING_MESSAGE = (
    "Your message looks like it contains a personal identifier, so it wasn't sent to the assistant. "
    "Remove it and try again. The assistant never needs ID, health card, tax file or bank numbers, or "
    "dates of birth."
)


class Budget(Protocol):
    async def ensure_available(self, namespace: BudgetNamespace) -> None: ...

    async def charge(self, namespace: BudgetNamespace, usd: float) -> None: ...


class PiiChecker(Protocol):
    async def check(self, text: str, region: Region) -> PiiCheckResult: ...


class UnmeteredBudget:
    """Fake mode makes no paid calls, so there is nothing to meter."""

    async def ensure_available(self, namespace: BudgetNamespace) -> None:
        return None

    async def charge(self, namespace: BudgetNamespace, usd: float) -> None:
        return None


class LanguagePiiChecker:
    async def check(self, text: str, region: Region) -> PiiCheckResult:
        return await language_pii.check_pii([text])


def categories(hits: list[language_pii.PiiHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.category] = counts.get(hit.category, 0) + 1
    return counts
