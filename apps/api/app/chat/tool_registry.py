"""Chat tool schemas and allowlists; browser tools run client-side, so the API only streams their input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.models.api import AgentName

Executor = Literal["server", "browser"]

MAX_NESTING_DEPTH = 1
FORCED_FINAL_MAX_OUTPUT_TOKENS = 1200


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """Strict mode needs every property required and no extras; optional fields use a null union."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    kind = schema["type"]
    return {**schema, "type": [kind, "null"] if isinstance(kind, str) else [*kind, "null"]}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD."}
_REGION = {"type": "string", "enum": ["CA", "AU"]}
_CLAIM_STATUS = ["draft", "submitted", "paid", "partially_paid", "rejected"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    executor: Executor
    requires_approval: bool = False

    def function_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="get_plan_overview",
            description=(
                "Summary of the user's active plan: insurer, plan name, region, currency, current benefit period, "
                "member aliases and the categories with benefit ids and names."
            ),
            parameters=_object({}),
            executor="browser",
        ),
        ToolSpec(
            name="find_benefits",
            description=(
                "Find benefits in the user's plan by name, keyword, provider type or item code (for example "
                "'RMT', 'glasses', 'item 505'). Returns benefit ids, coverage, limits and the booklet page."
            ),
            parameters=_object({"query": {**_STR, "description": "What to look for."}}),
            executor="browser",
        ),
        ToolSpec(
            name="search_plan_document",
            description=(
                "Full-text search over quotes from the user's (redacted) plan booklet. Returns page numbers and "
                "quotes to cite."
            ),
            parameters=_object({"query": _STR}),
            executor="browser",
        ),
        ToolSpec(
            name="get_usage",
            description=(
                "Used and remaining amounts for a benefit (limits, shared pool, frequency, waiting period) in "
                "the current period. Omit benefit_id for every benefit; omit member_alias for every member."
            ),
            parameters=_object(
                {
                    "benefit_id": _nullable({**_STR, "description": "Benefit id from find_benefits."}),
                    "member_alias": _nullable({**_STR, "description": "Member alias such as [MEMBER_A]."}),
                }
            ),
            executor="browser",
        ),
        ToolSpec(
            name="estimate_reimbursement",
            description=(
                "Engine-exact estimate of what the plan pays for a service, after coverage, caps, deductibles, "
                "frequency, limits, pools and waiting periods. Money is integer cents."
            ),
            parameters=_object(
                {
                    "benefit_id": _STR,
                    "member_alias": _STR,
                    "service_date": _DATE,
                    "charged_cents": {**_INT, "description": "Amount charged in cents, e.g. 12000 for $120.00."},
                    "quantity": _nullable(_NUM),
                    "item_code": _nullable(_STR),
                    "other_plan_paid_cents": _nullable(
                        {**_INT, "description": "Amount another plan (e.g. a spouse's plan) already paid, in cents."}
                    ),
                }
            ),
            executor="browser",
        ),
        ToolSpec(
            name="list_claims",
            description="The user's claims with status, member alias, service dates, amounts and deadline.",
            parameters=_object(
                {
                    "status": _nullable({"type": "string", "enum": _CLAIM_STATUS}),
                    "benefit_id": _nullable(_STR),
                }
            ),
            executor="browser",
        ),
        ToolSpec(
            name="draft_claim",
            description=(
                "Create a draft claim in the user's claim tracker. The user must approve it first; if they "
                "decline, acknowledge and do not call it again with the same details."
            ),
            parameters=_object(
                {
                    "member_alias": _STR,
                    "provider": _nullable(_STR),
                    "lines": {
                        "type": "array",
                        "minItems": 1,
                        "items": _object(
                            {
                                "benefit_id": _STR,
                                "service_date": _DATE,
                                "charged_cents": _INT,
                                "quantity": _nullable(_NUM),
                                "item_code": _nullable(_STR),
                                "description": _nullable(_STR),
                                "other_plan_paid_cents": _nullable(_INT),
                            }
                        ),
                    },
                }
            ),
            executor="browser",
            requires_approval=True,
        ),
        ToolSpec(
            name="update_claim",
            description=(
                "Update an existing claim (status, amount paid, provider or a note). The user must approve it "
                "first; if they decline, acknowledge and do not retry."
            ),
            parameters=_object(
                {
                    "claim_id": _STR,
                    "status": _nullable({"type": "string", "enum": _CLAIM_STATUS}),
                    "paid_cents": _nullable(_INT),
                    "provider": _nullable(_STR),
                    "note": _nullable(_STR),
                }
            ),
            executor="browser",
            requires_approval=True,
        ),
        ToolSpec(
            name="ask_knowledge_agent",
            description=(
                "Ask the Benefura knowledge agent a question about public CA/AU reference material (tax "
                "treatment of medical expenses, public health coverage, private health insurance rules). "
                "Returns an answer with cited sources."
            ),
            parameters=_object({"question": _STR, "region": _REGION}),
            executor="server",
        ),
        ToolSpec(
            name="search_public_knowledge",
            description=(
                "Hybrid search over public CA/AU reference pages. Returns documents wrapped in <documents> with "
                "source ids, titles and licences to cite. top_k is 1 to 8."
            ),
            parameters=_object({"query": _STR, "region": _REGION, "top_k": _INT}),
            executor="server",
        ),
    ]
}

AGENT_TOOLS: dict[AgentName, tuple[str, ...]] = {
    "plan_claims": (
        "get_plan_overview",
        "find_benefits",
        "search_plan_document",
        "get_usage",
        "estimate_reimbursement",
        "list_claims",
        "draft_claim",
        "update_claim",
        "ask_knowledge_agent",
    ),
    "knowledge": ("search_public_knowledge",),
}

APPROVAL_TOOLS: frozenset[str] = frozenset(n for n, s in TOOLS.items() if s.requires_approval)
BROWSER_TOOLS: frozenset[str] = frozenset(n for n, s in TOOLS.items() if s.executor == "browser")


class ToolRejected(Exception):
    def __init__(self, code: Literal["tool_not_allowed", "nesting_too_deep", "step_limit"], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def model_output(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


def tools_for(agent: AgentName) -> list[ToolSpec]:
    return [TOOLS[name] for name in AGENT_TOOLS[agent]]


def resolve_tool(agent: AgentName, name: str, *, depth: int) -> ToolSpec:
    if name not in AGENT_TOOLS[agent] or name not in TOOLS:
        raise ToolRejected("tool_not_allowed", f"{name} is not available to this agent.")
    spec = TOOLS[name]
    if depth >= MAX_NESTING_DEPTH and (spec.executor == "browser" or name == "ask_knowledge_agent"):
        raise ToolRejected("nesting_too_deep", f"{name} cannot be called from a nested agent.")
    if depth > MAX_NESTING_DEPTH:
        raise ToolRejected("nesting_too_deep", "Nested agents are limited to one level.")
    return spec


class StepBudget:
    """Tool calls per user turn, shared by every agent in the turn."""

    def __init__(self, max_steps: int, used: int = 0) -> None:
        self.max_steps = max_steps
        self.used = used

    @property
    def remaining(self) -> int:
        return max(0, self.max_steps - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_steps

    def consume(self) -> None:
        if self.exhausted:
            raise ToolRejected("step_limit", "The tool step limit for this question has been reached. Answer now.")
        self.used += 1
