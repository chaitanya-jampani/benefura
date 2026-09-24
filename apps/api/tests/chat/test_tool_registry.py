from __future__ import annotations

from typing import Any

import pytest

from app.chat.tool_registry import (
    AGENT_TOOLS,
    APPROVAL_TOOLS,
    BROWSER_TOOLS,
    TOOLS,
    StepBudget,
    ToolRejected,
    resolve_tool,
)


def _assert_strict(schema: dict[str, Any], path: str) -> None:
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "object" in kinds:
        assert schema.get("additionalProperties") is False, path
        assert sorted(schema["required"]) == sorted(schema["properties"]), path
        for name, prop in schema["properties"].items():
            _assert_strict(prop, f"{path}.{name}")
    if "array" in kinds:
        _assert_strict(schema["items"], f"{path}[]")


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_tool_schema_is_strict(name: str) -> None:
    spec = TOOLS[name]
    _assert_strict(spec.parameters, name)
    assert spec.function_tool()["strict"] is True


def test_executor_and_approval_flags() -> None:
    assert APPROVAL_TOOLS == {"draft_claim", "update_claim"}
    assert BROWSER_TOOLS == {
        "get_plan_overview",
        "find_benefits",
        "search_plan_document",
        "get_usage",
        "estimate_reimbursement",
        "list_claims",
        "draft_claim",
        "update_claim",
    }
    assert TOOLS["ask_knowledge_agent"].executor == "server"
    assert TOOLS["search_public_knowledge"].executor == "server"


def test_allowlists_are_disjoint_where_it_matters() -> None:
    assert "search_public_knowledge" not in AGENT_TOOLS["plan_claims"]
    assert set(AGENT_TOOLS["knowledge"]) == {"search_public_knowledge"}


@pytest.mark.parametrize(
    ("agent", "tool"),
    [
        ("knowledge", "draft_claim"),
        ("knowledge", "ask_knowledge_agent"),
        ("plan_claims", "search_public_knowledge"),
        ("plan_claims", "delete_everything"),
    ],
)
def test_non_allowlisted_calls_are_rejected(agent: Any, tool: str) -> None:
    with pytest.raises(ToolRejected) as err:
        resolve_tool(agent, tool, depth=0)
    assert err.value.code == "tool_not_allowed"


def test_nesting_depth_is_limited_to_one() -> None:
    assert resolve_tool("knowledge", "search_public_knowledge", depth=1).executor == "server"
    with pytest.raises(ToolRejected) as err:
        resolve_tool("plan_claims", "find_benefits", depth=1)
    assert err.value.code == "nesting_too_deep"
    with pytest.raises(ToolRejected) as err:
        resolve_tool("plan_claims", "ask_knowledge_agent", depth=1)
    assert err.value.code == "nesting_too_deep"
    with pytest.raises(ToolRejected) as err:
        resolve_tool("knowledge", "search_public_knowledge", depth=2)
    assert err.value.code == "nesting_too_deep"


def test_step_budget() -> None:
    budget = StepBudget(max_steps=6, used=4)
    budget.consume()
    budget.consume()
    assert budget.exhausted and budget.remaining == 0
    with pytest.raises(ToolRejected) as err:
        budget.consume()
    assert err.value.code == "step_limit"
    assert err.value.model_output()["error"] == "step_limit"
