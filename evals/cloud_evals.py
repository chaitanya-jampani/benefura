"""Foundry cloud evaluations: ``uv run python -m evals.cloud_evals [--targets knowledge,plan_claims,safety]``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.harness.datasets import EVALS_DIR, read_jsonl

JSON = dict[str, Any]
REPORTS = EVALS_DIR / "reports"
TERMINAL = {"completed", "failed", "canceled", "cancelled"}

# M0-verify: array-typed item_schema fields mapped via {{item.*}} into agent and document_retrieval evaluators.
STRING = {"type": "string"}
ARRAY = {"type": "array"}
SCHEMAS: dict[str, JSON] = {
    "knowledge": {"query": STRING, "response": STRING, "context": STRING},
    "retrieval": {"retrieval_ground_truth": ARRAY, "retrieved_documents": ARRAY},
    "plan_claims": {"query": STRING, "response_messages": ARRAY, "tool_calls": ARRAY, "tool_definitions": ARRAY},
    "safety": {"query": STRING, "response": STRING, "context": STRING},
}


@dataclass(frozen=True)
class Criterion:
    name: str
    evaluator: str
    mapping: dict[str, str]
    needs_model: bool = True
    init: dict[str, Any] = field(default_factory=dict)


CRITERIA: dict[str, list[Criterion]] = {
    "knowledge": [
        Criterion(
            "groundedness",
            "builtin.groundedness",
            {"query": "{{item.query}}", "response": "{{item.response}}", "context": "{{item.context}}"},
        ),
        Criterion("relevance", "builtin.relevance", {"query": "{{item.query}}", "response": "{{item.response}}"}),
    ],
    "retrieval": [
        Criterion(
            "document_retrieval",
            "builtin.document_retrieval",
            {
                "retrieval_ground_truth": "{{item.retrieval_ground_truth}}",
                "retrieved_documents": "{{item.retrieved_documents}}",
            },
            needs_model=False,
            init={"ground_truth_label_min": 0, "ground_truth_label_max": 4},
        ),
    ],
    "plan_claims": [
        Criterion(
            "intent_resolution",
            "builtin.intent_resolution",
            {"query": "{{item.query}}", "response": "{{item.response_messages}}"},
        ),
        Criterion(
            "task_adherence",
            "builtin.task_adherence",
            {
                "query": "{{item.query}}",
                "response": "{{item.response_messages}}",
                "tool_definitions": "{{item.tool_definitions}}",
            },
        ),
        Criterion(
            "tool_call_accuracy",
            "builtin.tool_call_accuracy",
            {
                "query": "{{item.query}}",
                "tool_calls": "{{item.tool_calls}}",
                "tool_definitions": "{{item.tool_definitions}}",
            },
        ),
    ],
    "safety": [
        *(
            Criterion(
                n, f"builtin.{n}", {"query": "{{item.query}}", "response": "{{item.response}}"}, needs_model=False
            )
            for n in ("violence", "self_harm", "hate_unfairness", "sexual", "protected_material", "indirect_attack")
        ),
        Criterion(
            "ungrounded_attributes",
            "builtin.ungrounded_attributes",
            {"query": "{{item.query}}", "response": "{{item.response}}", "context": "{{item.context}}"},
            needs_model=False,
        ),
    ],
}
PLAN: dict[str, list[tuple[str, list[str]]]] = {
    "knowledge": [("knowledge", ["knowledge.jsonl"]), ("retrieval", ["retrieval.jsonl"])],
    "plan_claims": [("plan_claims", ["plan_claims.jsonl"])],
    "safety": [("safety", ["knowledge.jsonl", "plan_claims.jsonl"])],
}


def testing_criteria(kind: str, model: str) -> list[JSON]:
    out = []
    for c in CRITERIA[kind]:
        item: JSON = {
            "type": "azure_ai_evaluator",
            "name": c.name,
            "evaluator_name": c.evaluator,
            "data_mapping": dict(c.mapping),
        }
        init = dict(c.init)
        if c.needs_model:
            # M0-verify: evaluator reference pages use "deployment_name"; the 2026-08 dataset how-to uses "model".
            init["deployment_name"] = model
        if init:
            item["initialization_parameters"] = init
        out.append(item)
    return out


def data_source_config(kind: str) -> JSON:
    props = SCHEMAS[kind]
    return {
        "type": "custom",
        "item_schema": {"type": "object", "properties": props, "required": list(props)},
        "include_sample_schema": False,
    }


def items_for(kind: str, rows: list[JSON]) -> list[JSON]:
    keys = list(SCHEMAS[kind])
    items = []
    for row in rows:
        if all(k in row for k in keys) and not row.get("meta", {}).get("errors"):
            items.append({"item": {k: row[k] for k in keys}})
    return items


def eval_definition(kind: str, model: str) -> JSON:
    return {
        "name": f"benefura-{kind}",
        "data_source_config": data_source_config(kind),
        "testing_criteria": testing_criteria(kind, model),
    }


def run_eval(
    openai_client: Any, kind: str, model: str, items: list[JSON], *, poll_s: float = 10.0, timeout_s: float = 1800.0
) -> JSON:
    definition = eval_definition(kind, model)
    evaluation = openai_client.evals.create(**definition)
    run = openai_client.evals.runs.create(
        eval_id=evaluation.id,
        name=f"benefura-{kind}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
        data_source={"type": "jsonl", "source": {"type": "file_content", "content": items}},
    )
    deadline = time.monotonic() + timeout_s
    while getattr(run, "status", None) not in TERMINAL and time.monotonic() < deadline:
        time.sleep(poll_s)
        run = openai_client.evals.runs.retrieve(run.id, eval_id=evaluation.id)
    criteria: JSON = {}
    for result in getattr(run, "per_testing_criteria_results", None) or []:
        name = str(result.testing_criteria)
        key = next((c.name for c in CRITERIA[kind] if name == c.name or name.startswith(c.name)), name)
        total = result.passed + result.failed
        criteria[key] = {
            "passed": result.passed,
            "failed": result.failed,
            "passRate": (result.passed / total) if total else None,
        }
    counts = getattr(run, "result_counts", None)
    return {
        "kind": kind,
        "evalId": evaluation.id,
        "runId": run.id,
        "status": getattr(run, "status", None),
        "reportUrl": getattr(run, "report_url", None),
        "resultCounts": counts.model_dump() if hasattr(counts, "model_dump") else counts,
        "items": len(items),
        "criteria": criteria,
    }


def get_openai_client() -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    from app.config import get_settings

    endpoint = get_settings().foundry_project_endpoint
    if not endpoint:
        raise SystemExit("FOUNDRY_PROJECT_ENDPOINT is not set")
    project = AIProjectClient(
        endpoint=endpoint, credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)
    )
    return project.get_openai_client()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.cloud_evals")
    p.add_argument("--targets", default="knowledge,plan_claims,safety")
    p.add_argument("--harness", type=Path, default=REPORTS / "harness")
    p.add_argument("--out", type=Path, default=REPORTS / "cloud-evals.json")
    p.add_argument("--model", default=None, help="judge deployment (default: settings.chat_model)")
    p.add_argument("--dry-run", action="store_true", help="print eval definitions and item counts only")
    args = p.parse_args(argv)

    from app.config import get_settings

    model = args.model or get_settings().chat_model
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    client = None if args.dry_run else get_openai_client()
    report: JSON = {"generatedAt": datetime.now(UTC).isoformat(), "model": model, "targets": {}}
    for target in targets:
        entry: JSON = {"evals": [], "criteria": {}}
        for kind, files in PLAN[target]:
            rows = [r for f in files if (args.harness / f).exists() for r in read_jsonl(args.harness / f)]
            items = items_for(kind, rows)
            if not items:
                entry["evals"].append({"kind": kind, "skipped": "no rows"})
                continue
            if args.dry_run:
                print(json.dumps(eval_definition(kind, model), indent=2))
                entry["evals"].append({"kind": kind, "items": len(items), "dryRun": True})
                continue
            result = run_eval(client, kind, model, items)
            entry["evals"].append({k: v for k, v in result.items() if k != "criteria"})
            entry["criteria"].update(result["criteria"])
        report["targets"][target] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {args.out}")
    failed = [e for t in report["targets"].values() for e in t["evals"] if e.get("status") in ("failed", "canceled")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
