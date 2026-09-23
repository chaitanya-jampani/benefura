"""Idempotent Content Understanding setup (azd postprovision): model deployment defaults and the receipt analyzer."""

from __future__ import annotations

import json
import sys
from typing import Any

from app.config import Settings, get_settings

RECEIPT_DESCRIPTION = "Benefura receipts and explanations of benefits (redacted images; aliases are opaque tokens)"


def model_deployments(settings: Settings) -> dict[str, str]:
    # M0-verify: CU supports the gpt-5 family as completion models.
    return {
        "gpt-5-mini": settings.chat_model,  # completion
        "gpt-5-nano": settings.nano_model,  # mini
        "text-embedding-3-small": settings.embedding_model,  # embedding
    }


def _field(kind: str, description: str, *, method: str = "extract", **extra: Any) -> dict[str, Any]:
    return {"type": kind, "method": method, "description": description, **extra}


def receipt_analyzer(settings: Settings) -> dict[str, Any]:
    """camelCase, as the service stores it."""
    service_line = {
        "type": "object",
        "description": "One billed service on the receipt.",
        "properties": {
            "serviceDate": _field("date", "Date the service was provided."),
            "description": _field("string", "Service or item description as printed."),
            "itemCode": _field("string", "Item, procedure or billing code (e.g. Australian item number)."),
            "quantity": _field("number", "Number of units, visits or items; 1 when not printed."),
            "amount": _field("number", "Amount charged for this line, in dollars, without currency symbols."),
        },
    }
    return {
        "description": RECEIPT_DESCRIPTION,
        "baseAnalyzerId": "prebuilt-document",
        "config": {
            "returnDetails": True,
            "enableOcr": True,
            "enableLayout": True,
            "tableFormat": "markdown",
            "estimateFieldSourceAndConfidence": True,
        },
        "fieldSchema": {
            "name": "BenefuraReceipt",
            "description": "Health provider receipt or insurer explanation of benefits.",
            "fields": {
                "providerName": _field("string", "Clinic, practitioner business or pharmacy name."),
                "providerType": _field(
                    "string",
                    "Kind of provider, e.g. registered massage therapist, physiotherapist, dentist, pharmacy.",
                    method="generate",
                ),
                "providerRegistrationNo": _field("string", "Provider registration, licence or provider number."),
                "serviceLines": {"type": "array", "description": "Billed services.", "items": service_line},
                "total": _field("number", "Total amount charged, in dollars."),
                "insurerPaid": _field("number", "Amount the insurer paid, if shown (EOBs), in dollars."),
                "currency": _field("string", "Currency of the amounts.", method="classify", enum=["CAD", "AUD"]),
            },
        },
        "models": {"completion": "gpt-5-mini", "embedding": "text-embedding-3-small"},  # M0-verify: role keys
    }


def _comparable(definition: dict[str, Any]) -> dict[str, Any]:
    return {k: definition.get(k) for k in ("description", "baseAnalyzerId", "config", "fieldSchema", "models")}


def matches(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Keys the service adds are ignored."""

    def covered(want: Any, have: Any) -> bool:
        if isinstance(want, dict):
            return isinstance(have, dict) and all(covered(v, have.get(k)) for k, v in want.items())
        return want == have

    return covered(_comparable(desired), _comparable(existing))


def main(argv: list[str]) -> int:
    settings = get_settings()
    defaults = model_deployments(settings)
    analyzer = receipt_analyzer(settings)
    if "--dry-run" in argv:
        print(
            json.dumps(
                {"defaults": {"modelDeployments": defaults}, settings.cu_receipt_analyzer_id: analyzer}, indent=2
            )
        )
        return 0
    if not settings.ai_services_endpoint:
        print("AI_SERVICES_ENDPOINT is not set", file=sys.stderr)
        return 2

    from azure.ai.contentunderstanding import ContentUnderstandingClient
    from azure.ai.contentunderstanding.models import ContentAnalyzer
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id)
    client = ContentUnderstandingClient(
        endpoint=settings.ai_services_endpoint.rstrip("/"), credential=credential, api_version=settings.cu_api_version
    )
    with client:
        current = client.get_defaults().as_dict().get("modelDeployments") or {}
        if all(current.get(k) == v for k, v in defaults.items()):
            print("CU defaults already up to date")
        else:
            client.update_defaults(model_deployments=defaults)  # merge-patch: other mappings are kept
            print("CU defaults updated")

        analyzer_id = settings.cu_receipt_analyzer_id
        try:
            existing = client.get_analyzer(analyzer_id).as_dict()
        except ResourceNotFoundError:
            existing = None
        if existing is not None and matches(existing, analyzer):
            print(f"{analyzer_id} already up to date")
            return 0
        poller = client.begin_create_analyzer(analyzer_id, resource=ContentAnalyzer(analyzer), allow_replace=True)
        poller.result()
        print(f"{analyzer_id} {'replaced' if existing else 'created'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
