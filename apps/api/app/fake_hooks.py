"""Per-request test hooks, honoured only when ``AI_MODE=fake``."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass

PII_HEADER = "x-benefura-fake-pii"  # <page>:<category>; page 1 for receipts and chat
INJECTION_HEADER = "x-benefura-fake-injection"  # <page>[,<page>…]; 0 flags the chat prompt
CONTENT_FILTER_HEADER = "x-benefura-fake-content-filter"
UNSAFE_IMAGE_HEADER = "x-benefura-fake-unsafe-image"
HOOK_HEADERS = (PII_HEADER, INJECTION_HEADER, CONTENT_FILTER_HEADER, UNSAFE_IMAGE_HEADER)


@dataclass(frozen=True)
class FakeHooks:
    pii: tuple[int, str] | None = None
    injection_pages: frozenset[int] = frozenset()
    content_filter: bool = False
    unsafe_image: bool = False


NO_HOOKS = FakeHooks()
_current: ContextVar[FakeHooks | None] = ContextVar("benefura_fake_hooks", default=None)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def parse_hooks(headers: Mapping[str, str]) -> FakeHooks:
    pii: tuple[int, str] | None = None
    raw_pii = (headers.get(PII_HEADER) or "").strip()
    if ":" in raw_pii:
        page, _, category = raw_pii.partition(":")
        if page.strip().isdigit() and category.strip().isalnum():
            pii = (int(page), category.strip())
    pages = frozenset(
        int(p) for p in (headers.get(INJECTION_HEADER) or "").split(",") if p.strip().isdigit() and len(p.strip()) <= 4
    )
    return FakeHooks(
        pii=pii,
        injection_pages=pages,
        content_filter=_truthy(headers.get(CONTENT_FILTER_HEADER)),
        unsafe_image=_truthy(headers.get(UNSAFE_IMAGE_HEADER)),
    )


def current_hooks() -> FakeHooks:
    return _current.get() or NO_HOOKS


def use_hooks(hooks: FakeHooks) -> Token[FakeHooks | None]:
    return _current.set(hooks)


def reset_hooks(token: Token[FakeHooks | None]) -> None:
    _current.reset(token)
