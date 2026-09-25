"""Spike 11: PII false-positive calibration for the two-tier policy. Entity text is never printed or stored."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx
from _common import COGNITIVE_SCOPE, ai_services_url, bearer, check, settings, write_result

HARD = {
    "CASocialInsuranceNumber", "CAHealthServiceNumber", "CAPersonalHealthIdentification", "CABankAccountNumber",
    "AUTaxFileNumber", "AUMedicalAccountNumber", "AUBankAccountNumber", "AUDriversLicenseNumber", "DateOfBirth",
}
ADVISORY = {"Person", "Organization", "Email", "PhoneNumber", "Address"}
ALIAS = re.compile(r"[\[\(\{I|]?\s*(MEMBER|EMPLOYER|POLICY|PROVIDER)_[A-Z0-9]+\s*[\]\)\}|]?")
MAX_CHARS = 5000  # per document for synchronous analyze-text


def documents(pages_files: list[Path], receipt_files: list[Path]) -> list[dict[str, str]]:
    docs = []
    for f in pages_files:
        for p in json.loads(f.read_text()):
            text = p["markdown"]
            for i in range(0, len(text), MAX_CHARS):
                docs.append({"id": f"{f.stem}:p{p['page']}:{i // MAX_CHARS}", "language": "en", "text": text[i : i + MAX_CHARS]})
    for f in receipt_files:
        docs.append({"id": f"receipt:{f.stem}", "language": "en", "text": f.read_text()[:MAX_CHARS]})
    return docs


def analyze(client: httpx.Client, batch: list[dict[str, str]]) -> list[dict[str, Any]]:
    url = ai_services_url(f"language/:analyze-text?api-version={settings().language_api_version}")
    body = {"kind": "PiiEntityRecognition",
            "parameters": {"modelVersion": "latest", "piiCategories": sorted(HARD | ADVISORY)},
            "analysisInput": {"documents": batch}}
    resp = client.post(url, json=body, headers={"Authorization": f"Bearer {bearer(COGNITIVE_SCOPE)}"})
    resp.raise_for_status()
    return resp.json()["results"]["documents"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=Path, nargs="+", required=True)
    parser.add_argument("--receipt-text", type=Path, nargs="*", default=[])
    parser.add_argument("--min-confidence", type=float, default=0.8)
    args = parser.parse_args()

    docs = documents(args.pages, args.receipt_text)
    by_id = {d["id"]: d["text"] for d in docs}
    counts: Counter[str] = Counter()
    confidences: dict[str, list[float]] = defaultdict(list)
    hard_hits: list[dict[str, Any]] = []
    alias_as_person = 0
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(docs), 5):
            for d in analyze(client, docs[i : i + 5]):
                for e in d["entities"]:
                    cat = e["category"]
                    counts[cat] += 1
                    confidences[cat].append(e["confidenceScore"])
                    span = by_id[d["id"]][e["offset"] : e["offset"] + e["length"]]
                    if cat in {"Person", "Organization"} and ALIAS.fullmatch(span.strip()):
                        alias_as_person += 1
                    if cat in HARD and e["confidenceScore"] >= args.min_confidence:
                        hard_hits.append({"doc": d["id"], "category": cat, "confidence": e["confidenceScore"]})

    summary = {
        cat: {"tier": "hard" if cat in HARD else "advisory", "count": n,
              "minConfidence": min(confidences[cat]), "maxConfidence": max(confidences[cat])}
        for cat, n in counts.most_common()
    }
    print(json.dumps(summary, indent=2))
    results: dict[str, bool] = {}
    check(results, f"no hard-tier hits >= {args.min_confidence}", not hard_hits, json.dumps(hard_hits)[:300])
    check(results, "alias tokens never reported as Person/Organization", alias_as_person == 0, str(alias_as_person))
    write_result("s11", {"documents": len(docs), "minConfidence": args.min_confidence, "categories": summary,
                         "hardHits": hard_hits, "aliasAsPerson": alias_as_person, "checks": results},
                 all(results.values()))


if __name__ == "__main__":
    main()
