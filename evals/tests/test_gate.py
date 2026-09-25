from __future__ import annotations

import json

import yaml

from evals.harness.datasets import load_scenarios
from evals.run import THRESHOLDS, Gate, build_gates, compute_custom_metrics, main, render_summary


def thresholds() -> dict:
    return yaml.safe_load(THRESHOLDS.read_text())


def test_gate_statuses():
    assert Gate("custom", "x", 0.95, min=0.9).status == "pass"
    assert Gate("custom", "x", 0.85, min=0.9).status == "fail"
    assert Gate("custom", "x", 1, max=0).status == "fail"
    assert Gate("custom", "x", 0, max=0).status == "pass"
    assert Gate("custom", "x", None, min=0.9).status == "skipped"
    assert Gate("custom", "x", None, min=0.9, required=True).status == "fail"


def test_thresholds_file_covers_plan_targets():
    t = thresholds()
    assert t["custom"]["extraction_field_accuracy"]["min"] == 0.90
    assert t["custom"]["numeric_exactness"]["min"] <= 13 / 15
    assert set(t["cloud"]["knowledge"]) == {"groundedness", "relevance", "document_retrieval"}
    assert set(t["cloud"]["plan_claims"]) == {"intent_resolution", "task_adherence", "tool_call_accuracy"}
    assert set(t["cloud"]["safety"]) == {
        "violence",
        "self_harm",
        "hate_unfairness",
        "sexual",
        "protected_material",
        "indirect_attack",
        "ungrounded_attributes",
    }


def test_build_gates_merges_cloud_and_red_team():
    custom = {
        "numeric_exactness": 14 / 15,
        "tool_call_match": 1.0,
        "citation_licence_pass_rate": 1.0,
        "pii_leak_hits": 0,
    }
    cloud = {"targets": {"knowledge": {"criteria": {"groundedness": {"passRate": 0.5}}}}}
    gates = build_gates(thresholds(), custom, cloud, {"overallAttackSuccessRate": 0.02})
    by = {(g.group, g.name): g for g in gates}
    assert by[("custom", "numeric_exactness")].status == "pass"
    assert by[("cloud/knowledge", "groundedness")].status == "fail"
    assert by[("cloud/knowledge", "relevance")].status == "skipped"
    assert by[("red_team", "overall_attack_success_rate")].status == "pass"
    assert by[("custom", "extraction_field_accuracy")].status == "skipped"
    required_cloud = build_gates(thresholds(), custom, None, None, require_cloud=True)
    assert any(g.group.startswith("cloud") and g.status == "fail" for g in required_cloud)


def test_summary_markdown():
    md = render_summary(
        [Gate("custom", "numeric_exactness", 0.8, min=0.866, required=True), Gate("custom", "pii_leak_hits", 0, max=0)]
    )
    assert "| custom | numeric_exactness | 0.800 | >= 0.866 | **FAIL** |" in md
    assert "| custom | pii_leak_hits | 0 | <= 0 | pass |" in md
    assert "1 failing, 1 passing, 0 skipped." in md


def _exact_rows() -> list[dict]:
    answers = {
        "ca-01": ("The plan pays $80.00; you pay $40.00.", "estimate_reimbursement"),
    }
    rows = []
    for s in load_scenarios():
        text, _ = answers.get(s.id[:5], ("", None))
        required = [c for c in s.expectedToolCalls if c.required]
        calls = [
            {
                "type": "tool_call",
                "tool_call_id": f"{s.id}-{i}",
                "name": c.name or c.anyOf[0]["name"],
                "arguments": c.arguments,
            }
            for i, c in enumerate(required)
        ]
        rows.append(
            {
                "query": "q",
                "response": text,
                "context": "",
                "tool_calls": calls,
                "tool_definitions": [],
                "meta": {"id": s.id, "errors": []},
            }
        )
    return rows


def test_main_gate_exit_codes_and_step_summary(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "plan_claims.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _exact_rows()))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "gate.json"
    code = main(
        [
            "--harness",
            str(harness),
            "--cloud",
            str(tmp_path / "none.json"),
            "--red-team",
            str(tmp_path / "none.json"),
            "--out",
            str(out),
        ]
    )
    assert code == 1  # only 1-2 of 15 scenarios have facts in the answer, and knowledge rows are missing
    report = json.loads(out.read_text())
    statuses = {g["name"]: g["status"] for g in report["gates"]}
    assert statuses["numeric_exactness"] == "fail"
    assert statuses["citation_licence_pass_rate"] == "fail"  # required and missing
    assert "Benefura evaluation gate" in summary.read_text()


def test_custom_metrics_with_pii_needles(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "payloads.jsonl").write_text(json.dumps({"id": "x", "payload": "Callum Ashgrove"}) + "\n")
    pii = tmp_path / "au.pii.json"
    pii.write_text(json.dumps([{"value": "Callum Ashgrove", "kind": "person_name"}]))
    metrics = compute_custom_metrics(harness, [pii])["metrics"]
    assert metrics["pii_leak_hits"] == 2
