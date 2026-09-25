"""Harness against ``BENEFURA_API_URL``. ``retrieval`` calls ``hybrid_search`` in-process on live Azure AI Search."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from evals.harness.client import BenefuraClient
from evals.harness.datasets import (
    EVALS_DIR,
    load_extraction_golden,
    load_knowledge_questions,
    load_retrieval_queries,
    load_scenarios,
)
from evals.harness.extraction import run_document
from evals.harness.runner import (
    PayloadLog,
    run_knowledge_question,
    run_retrieval_query,
    run_scenario,
    write_outputs,
)

REQUIRED_ROW_KEYS = ("query", "response", "context", "tool_calls", "tool_definitions")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.harness")
    p.add_argument("--datasets", default="plan_claims,knowledge", help="plan_claims,knowledge,retrieval,extraction")
    p.add_argument("--smoke", action="store_true", help="2 scenarios + 2 questions, shape checks only")
    p.add_argument("--only", action="append", default=[], metavar="ID", help="limit to these item ids")
    p.add_argument("--out", type=Path, default=EVALS_DIR / "reports" / "harness")
    p.add_argument("--api-url", default=None)
    return p.parse_args(argv)


async def main_async(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None) -> int:
    wanted = {d.strip() for d in args.datasets.split(",") if d.strip()}
    log = PayloadLog()
    rows: dict[str, list[dict]] = {}
    client = BenefuraClient(args.api_url, transport=transport)
    try:
        health = await client.healthz()
        print(f"API {client.base_url}: aiMode={health.get('aiMode')} version={health.get('version')}")
        if "plan_claims" in wanted:
            scenarios = [s for s in load_scenarios() if not args.only or s.id in args.only]
            scenarios = scenarios[:2] if args.smoke else scenarios
            rows["plan_claims"] = [await run_scenario(client, s, log) for s in scenarios]
        if "knowledge" in wanted:
            questions = [q for q in load_knowledge_questions() if not args.only or q.id in args.only]
            questions = questions[:2] if args.smoke else questions
            base = {
                "today": "2026-06-15",
                "tz": "UTC",
                "planName": None,
                "memberAliases": ["[MEMBER_A]"],
                "categories": [],
            }
            rows["knowledge"] = [await run_knowledge_question(client, q, log, base) for q in questions]
        if "extraction" in wanted and not args.smoke:
            for doc in load_extraction_golden():
                extracted = await run_document(client, doc)
                if extracted is None:
                    print(f"extraction: no redacted chunks for {doc['doc']}, skipped")
                    continue
                target = args.out / f"extraction-{doc['doc']}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(extracted, indent=2))
                print(f"wrote {target}")
    finally:
        await client.close()
    if "retrieval" in wanted and not args.smoke:
        from app.services.search import hybrid_search

        rows["retrieval"] = [await run_retrieval_query(q, hybrid_search) for q in load_retrieval_queries()]

    paths = write_outputs(args.out, rows, log)
    problems = [
        f"{name}:{row['meta']['id']} missing {key}"
        for name, items in rows.items()
        if name != "retrieval"
        for row in items
        for key in REQUIRED_ROW_KEYS
        if key not in row
    ]
    errors = [
        f"{name}:{row['meta']['id']} {row['meta']['errors']}"
        for name, items in rows.items()
        for row in items
        if row["meta"].get("errors")
    ]
    for path in paths.values():
        print(f"wrote {path}")
    for line in problems + errors:
        print(line, file=sys.stderr)
    return 1 if problems or (args.smoke and errors) else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
