from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.fake_hooks import current_hooks

CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")


@dataclass
class ImageSafetyResult:
    severities: dict[str, int] = field(default_factory=dict)
    records: int = 1

    @property
    def max_severity(self) -> int:
        return max(self.severities.values(), default=0)

    @property
    def unsafe(self) -> bool:
        return self.max_severity >= get_settings().content_safety_reject_severity

    @property
    def flagged_categories(self) -> list[str]:
        threshold = get_settings().content_safety_reject_severity
        return sorted(c for c, s in self.severities.items() if s >= threshold)


def parse_response(body: dict[str, Any]) -> ImageSafetyResult:
    return ImageSafetyResult(
        severities={str(a.get("category")): int(a.get("severity") or 0) for a in body.get("categoriesAnalysis", [])}
    )


async def analyze_image(image: bytes) -> ImageSafetyResult:
    settings = get_settings()
    if settings.ai_mode != "live":
        severity = 6 if current_hooks().unsafe_image else 0
        return ImageSafetyResult(severities=dict.fromkeys(CATEGORIES, 0) | ({"Violence": severity}))

    from app.services.rest import post_json

    payload = {
        "image": {"content": base64.b64encode(image).decode("ascii")},
        "categories": list(CATEGORIES),
        "outputType": "FourSeverityLevels",
    }
    # M0-verify: image:analyze and text:shieldPrompt 2024-09-01 on the AIServices host with an Entra token.
    path = f"contentsafety/image:analyze?api-version={settings.content_safety_api_version}"
    return parse_response(await post_json(path, payload, service="Content Safety"))
