"""Dataset loading and validation (shared by the harness, the custom evaluators and the tests)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

JSON = dict[str, Any]
EVALS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_DIR.parent
DATASETS_DIR = EVALS_DIR / "datasets"
SOURCES_YAML = REPO_ROOT / "knowledge" / "sources.yaml"


def read_jsonl(path: Path) -> list[JSON]:
    rows: list[JSON] = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path.name}:{n}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[JSON]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def load_sources() -> dict[str, JSON]:
    """Plain YAML, so evals does not depend on the knowledge package."""
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    return {s["id"]: s for s in data["sources"]}


def load_tool_definitions(agent: Literal["plan_claims", "knowledge"]) -> list[JSON]:
    from app.chat.tool_registry import tools_for

    return [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools_for(agent)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeQuestion(Strict):
    id: str
    region: Literal["CA", "AU"]
    question: str = Field(min_length=10)
    expected_sources: list[str] = Field(min_length=1)
    reference_answer: str = Field(min_length=10)


class RelevantSource(Strict):
    sourceId: str
    label: int = Field(ge=0, le=4)
    sectionHints: list[str] = Field(default_factory=list)


class RetrievalQuery(Strict):
    id: str
    region: Literal["CA", "AU"]
    query: str
    relevant: list[RelevantSource] = Field(min_length=1)


class Approval(Strict):
    decision: Literal["approve", "decline"]
    reason: str | None = None


class Turn(Strict):
    user: str
    approvals: dict[str, Approval] = Field(default_factory=dict)


class ExpectedCall(Strict):
    name: str | None = None
    anyOf: list[dict[str, Any]] | None = None
    required: bool = True
    arguments: JSON = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_form(self) -> ExpectedCall:
        if bool(self.name) == bool(self.anyOf):
            raise ValueError("expected call needs exactly one of name / anyOf")
        return self


class ExpectedToolOutput(Strict):
    tool: str
    args: JSON
    expect: JSON


class Fact(Strict):
    kind: Literal["money", "date", "text", "number"]
    cents: int | None = None
    iso: str | None = None
    toleranceDays: int = 0
    anyOf: list[str] | None = None
    value: float | None = None


class Scenario(Strict):
    id: str
    plan: str
    region: Literal["CA", "AU"]
    context: JSON
    executorOptions: JSON = Field(default_factory=dict)
    seededClaims: list[JSON] = Field(default_factory=list)
    turns: list[Turn] = Field(min_length=1)
    expectedToolCalls: list[ExpectedCall]
    forbiddenTools: list[str] = Field(default_factory=list)
    maxCalls: dict[str, int] = Field(default_factory=dict)
    expectedToolOutputs: list[ExpectedToolOutput] = Field(default_factory=list)
    expectedFacts: list[Fact] = Field(default_factory=list)
    expectedSources: list[str] = Field(default_factory=list)
    notes: str = Field(min_length=40)


def load_knowledge_questions(path: Path | None = None) -> list[KnowledgeQuestion]:
    return [KnowledgeQuestion.model_validate(r) for r in read_jsonl(path or DATASETS_DIR / "knowledge_questions.jsonl")]


def load_retrieval_queries(path: Path | None = None) -> list[RetrievalQuery]:
    return [RetrievalQuery.model_validate(r) for r in read_jsonl(path or DATASETS_DIR / "retrieval_queries.jsonl")]


def load_scenarios(path: Path | None = None) -> list[Scenario]:
    return [Scenario.model_validate(r) for r in read_jsonl(path or DATASETS_DIR / "plan_claims_scenarios.jsonl")]


def load_extraction_golden() -> list[JSON]:
    return yaml.safe_load((DATASETS_DIR / "extraction_golden.yaml").read_text(encoding="utf-8"))["documents"]


def get_path(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def path_values(obj: Any, dotted: str) -> list[Any]:
    values = [obj]
    for part in dotted.split("."):
        nxt: list[Any] = []
        for value in values:
            if part == "*" and isinstance(value, list):
                nxt.extend(value)
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                nxt.append(value[int(part)])
            elif isinstance(value, dict) and part in value:
                nxt.append(value[part])
        values = nxt
    return values


def path_matches(obj: Any, dotted: str, expected: Any) -> bool:
    """With ``*`` any element may match, because search ranking is not an engine number."""
    values = path_values(obj, dotted)
    if "*" in dotted.split("."):
        return expected in values
    return len(values) == 1 and values[0] == expected and type(values[0]) is type(expected)
