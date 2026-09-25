from __future__ import annotations

from app.chat.tool_registry import AGENT_TOOLS, TOOLS
from evals.harness.browser_tools import BROWSER_TOOLS, load_plan
from evals.harness.datasets import (
    DATASETS_DIR,
    REPO_ROOT,
    load_extraction_golden,
    load_knowledge_questions,
    load_retrieval_queries,
    load_scenarios,
    load_sources,
    read_jsonl,
)

PLAN_TOOLS = set(AGENT_TOOLS["plan_claims"])


def _benefit_ids(plan: dict) -> set[str]:
    return {b["id"] for c in plan["categories"] for b in c["benefits"]}


def _walk_values(node, key=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_values(v, key)
    else:
        yield key, node


def _check_arguments(schema: dict, value, where: str) -> None:
    """Argument names must exist in the registry schema and enum values must be allowed ones."""
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    if value is None:
        assert "null" in types, f"{where}: null not allowed"
        return
    if "object" in types:
        assert isinstance(value, dict), where
        unknown = set(value) - set(schema["properties"])
        assert not unknown, f"{where}: unknown arguments {sorted(unknown)}"
        for key, item in value.items():
            _check_arguments(schema["properties"][key], item, f"{where}.{key}")
    elif "array" in types:
        assert isinstance(value, list), where
        for i, item in enumerate(value):
            _check_arguments(schema["items"], item, f"{where}[{i}]")
    elif "enum" in schema:
        assert value in schema["enum"], where


def test_sixteen_scenarios_with_unique_ids_and_both_regions():
    scenarios = load_scenarios()
    assert len(scenarios) == 16
    assert len({s.id for s in scenarios}) == 16
    assert {s.region for s in scenarios} == {"CA", "AU"}
    assert all(s.expectedToolOutputs for s in scenarios), "every scenario has engine-exact expected outputs"


def test_scenarios_reference_real_benefits_members_and_claims():
    for s in load_scenarios():
        plan = load_plan(s.plan)
        assert plan["region"] == s.region == s.context["region"]
        benefits = _benefit_ids(plan)
        aliases = {m["alias"] for m in plan["members"]}
        claim_ids = {c["id"] for c in s.seededClaims}
        for claim in s.seededClaims:
            assert claim["planId"] == plan["id"]
            assert claim["patientMemberId"] in {m["id"] for m in plan["members"]}
            for line in claim["lines"]:
                assert line["benefitId"] in benefits, (s.id, line["benefitId"])
        payloads = [c.arguments for c in s.expectedToolCalls] + [o.args for o in s.expectedToolOutputs]
        for payload in payloads:
            for key, value in _walk_values(payload):
                if key == "benefit_id" and value is not None:
                    assert value in benefits, (s.id, value)
                if key == "member_alias":
                    assert value in aliases, (s.id, value)
                if key == "claim_id":
                    assert value in claim_ids, (s.id, value)
        for alias in s.context["memberAliases"]:
            assert alias in aliases
        for turn in s.turns:
            assert set(turn.approvals) <= {"draft_claim", "update_claim"}


def test_scenario_tool_names_match_the_registry():
    assert {n for n, t in TOOLS.items() if t.executor == "browser"} == BROWSER_TOOLS
    for s in load_scenarios():
        for call in s.expectedToolCalls:
            names = [call.name] if call.name else [a["name"] for a in call.anyOf or []]
            assert set(names) <= PLAN_TOOLS, (s.id, names)
        assert set(s.forbiddenTools) <= PLAN_TOOLS
        assert {o.tool for o in s.expectedToolOutputs} <= BROWSER_TOOLS


def test_scenario_arguments_use_the_registry_argument_names():
    for s in load_scenarios():
        for call in s.expectedToolCalls:
            if call.name and call.arguments:
                _check_arguments(TOOLS[call.name].parameters, call.arguments, f"{s.id}:{call.name}")
        for output in s.expectedToolOutputs:
            schema = TOOLS[output.tool].parameters
            _check_arguments(schema, output.args, f"{s.id}:{output.tool}")
            # expected outputs call the tool exactly as a strict-mode model does: every argument present
            assert set(output.args) == set(schema["required"]), (s.id, output.tool)


def test_scenarios_have_auditable_working_notes():
    for s in load_scenarios():
        has_numbers = any(f.kind in ("money", "date") for f in s.expectedFacts) or s.expectedToolOutputs
        assert has_numbers
        assert len(s.notes) > 150, s.id


def test_thirty_knowledge_questions_with_real_indexable_sources():
    questions = load_knowledge_questions()
    sources = load_sources()
    assert len(questions) == 30
    assert len({q.id for q in questions}) == 30
    assert sum(q.region == "CA" for q in questions) == 15 and sum(q.region == "AU" for q in questions) == 15
    for q in questions:
        for sid in q.expected_sources:
            assert sid in sources, (q.id, sid)
            assert sources[sid]["index"] is True, (q.id, sid)
            assert sources[sid]["region"] == q.region, (q.id, sid)


def test_retrieval_queries_reference_real_sources():
    queries = load_retrieval_queries()
    sources = load_sources()
    assert len(queries) >= 20
    for q in queries:
        assert any(r.label >= 3 for r in q.relevant), q.id
        for r in q.relevant:
            assert r.sourceId in sources and sources[r.sourceId]["index"] is True, (q.id, r.sourceId)


def test_scenario_expected_sources_exist():
    sources = load_sources()
    for s in load_scenarios():
        assert all(sid in sources for sid in s.expectedSources)


def test_extraction_golden_manifest_points_at_real_sample_files():
    docs = load_extraction_golden()
    assert {d["doc"] for d in docs} == {"ca-northwind", "au-wattle"}
    for d in docs:
        for key in ("plan_fixture", "golden", "pii", "booklet"):
            assert (REPO_ROOT / d[key]).exists(), (d["doc"], key)


def test_jsonl_files_parse():
    for path in DATASETS_DIR.glob("*.jsonl"):
        assert read_jsonl(path)
