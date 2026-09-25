"""Evaluation gate: ``uv run python -m evals.run [--harness DIR] [--cloud FILE] [--red-team FILE]``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.custom.citations import score_citations
from evals.custom.extraction_accuracy import score_extraction
from evals.custom.numeric_exactness import score_scenarios
from evals.custom.pii_leak import load_needles, scan_jsonl_files
from evals.harness.datasets import (
    EVALS_DIR,
    REPO_ROOT,
    load_extraction_golden,
    load_scenarios,
    load_sources,
    read_jsonl,
)

JSON = dict[str, Any]
THRESHOLDS = EVALS_DIR / "thresholds.yaml"
REPORTS = EVALS_DIR / "reports"


@dataclass
class Gate:
    group: str
    name: str
    value: float | None
    min: float | None = None
    max: float | None = None
    required: bool = False

    @property
    def status(self) -> str:
        if self.value is None:
            return "fail" if self.required else "skipped"
        if self.min is not None and self.value < self.min:
            return "fail"
        if self.max is not None and self.value > self.max:
            return "fail"
        return "pass"

    @property
    def target(self) -> str:
        parts = []
        if self.min is not None:
            parts.append(f">= {self.min:g}")
        if self.max is not None:
            parts.append(f"<= {self.max:g}")
        return " and ".join(parts) or "—"


def compute_custom_metrics(harness_dir: Path, pii_files: list[Path] | None = None) -> JSON:
    metrics: JSON = {}
    details: JSON = {}
    plan_path, knowledge_path = harness_dir / "plan_claims.jsonl", harness_dir / "knowledge.jsonl"
    if plan_path.exists():
        scenario_scores = score_scenarios(load_scenarios(), read_jsonl(plan_path))
        metrics["numeric_exactness"] = scenario_scores["numeric_exactness"]
        metrics["tool_call_match"] = scenario_scores["tool_call_match"]
        details["plan_claims"] = scenario_scores
    if knowledge_path.exists():
        citation_scores = score_citations(read_jsonl(knowledge_path), load_sources())
        metrics["citation_licence_pass_rate"] = citation_scores["citation_licence_pass_rate"]
        metrics["citation_expected_source_rate"] = citation_scores["citation_expected_source_rate"]
        details["citations"] = citation_scores

    accuracies = []
    for doc in load_extraction_golden():
        golden_path = REPO_ROOT / doc["golden"]
        predicted_path = harness_dir / f"extraction-{doc['doc']}.json"
        if golden_path.exists() and predicted_path.exists():
            predicted = json.loads(predicted_path.read_text())
            if isinstance(predicted, dict) and "chunks" in predicted:
                predicted = predicted["chunks"]
            result = score_extraction(json.loads(golden_path.read_text()), predicted)
            details.setdefault("extraction", {})[doc["doc"]] = result
            accuracies.append(result["field_accuracy"])
    if accuracies:
        metrics["extraction_field_accuracy"] = min(accuracies)

    needles = load_needles(pii_files)
    scanned = sorted(harness_dir.glob("*.jsonl")) + sorted(harness_dir.glob("extraction-*.json"))
    if needles and scanned:
        pii = scan_jsonl_files(scanned, needles)
        metrics["pii_leak_hits"] = pii["pii_leak_hits"]
        details["pii"] = {k: v for k, v in pii.items() if k != "locations"} | {"locations": pii["locations"][:50]}
    return {"metrics": metrics, "details": details}


def build_gates(
    thresholds: JSON, custom: JSON, cloud: JSON | None, red_team: JSON | None, require_cloud: bool = False
) -> list[Gate]:
    gates: list[Gate] = []
    for name, rule in thresholds.get("custom", {}).items():
        gates.append(
            Gate("custom", name, custom.get(name), rule.get("min"), rule.get("max"), bool(rule.get("required", False)))
        )
    for target, criteria in thresholds.get("cloud", {}).items():
        results = ((cloud or {}).get("targets", {}).get(target, {}) or {}).get("criteria", {})
        for name, rule in criteria.items():
            value = (results.get(name) or {}).get("passRate")
            gates.append(
                Gate(
                    f"cloud/{target}",
                    name,
                    value,
                    rule.get("min"),
                    rule.get("max"),
                    bool(rule.get("required", require_cloud)),
                )
            )
    for name, rule in thresholds.get("red_team", {}).items():
        value = (red_team or {}).get("overallAttackSuccessRate") if name == "overall_attack_success_rate" else None
        gates.append(Gate("red_team", name, value, rule.get("min"), rule.get("max"), bool(rule.get("required", False))))
    return gates


def render_summary(gates: list[Gate]) -> str:
    icon = {"pass": "pass", "fail": "**FAIL**", "skipped": "skipped"}
    lines = [
        "## Benefura evaluation gate",
        "",
        "| Group | Metric | Value | Target | Result |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for g in gates:
        value = "—" if g.value is None else (f"{g.value:.3f}" if isinstance(g.value, float) else str(g.value))
        lines.append(f"| {g.group} | {g.name} | {value} | {g.target} | {icon[g.status]} |")
    failed = [g for g in gates if g.status == "fail"]
    lines += [
        "",
        f"{len(failed)} failing, {sum(g.status == 'pass' for g in gates)} passing, "
        f"{sum(g.status == 'skipped' for g in gates)} skipped.",
    ]
    return "\n".join(lines) + "\n"


def _load_json(path: Path | None) -> JSON | None:
    return json.loads(path.read_text()) if path and path.exists() else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.run")
    p.add_argument("--harness", type=Path, default=REPORTS / "harness")
    p.add_argument("--cloud", type=Path, default=REPORTS / "cloud-evals.json")
    p.add_argument("--red-team", type=Path, default=REPORTS / "red-team-summary.json")
    p.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    p.add_argument("--require-cloud", action="store_true", help="missing cloud results fail the gate")
    p.add_argument("--out", type=Path, default=REPORTS / "gate-summary.json")
    args = p.parse_args(argv)

    thresholds = yaml.safe_load(args.thresholds.read_text())
    custom = compute_custom_metrics(args.harness)
    gates = build_gates(
        thresholds, custom["metrics"], _load_json(args.cloud), _load_json(args.red_team), args.require_cloud
    )
    summary = render_summary(gates)
    print(summary)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(summary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"gates": [g.__dict__ | {"status": g.status} for g in gates], "details": custom["details"]},
            indent=2,
            default=str,
        )
    )
    return 1 if any(g.status == "fail" for g in gates) else 0


if __name__ == "__main__":
    sys.exit(main())
