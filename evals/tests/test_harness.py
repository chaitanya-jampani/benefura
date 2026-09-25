from __future__ import annotations

import json

import httpx
import pytest

from evals.harness import __main__ as cli
from evals.harness.browser_tools import BrowserToolExecutor, load_plan
from evals.harness.client import BenefuraClient, last_agent, outgoing_messages
from evals.harness.conversation import Conversation
from evals.harness.datasets import load_knowledge_questions, load_scenarios, load_tool_definitions
from evals.harness.runner import PayloadLog, run_knowledge_question, run_retrieval_query, run_scenario, write_outputs
from evals.tests.conftest import GOLDEN_CONTEXT, chat_route_app

SCENARIOS = {s.id: s for s in load_scenarios()}
ROW_KEYS = {"query", "response", "context", "tool_calls", "tool_definitions", "response_messages", "meta"}
CA_PLAN = "samples/fixtures/ca-northwind.plan.json"


@pytest.fixture
async def route_client(fake_ai_mode):
    client = BenefuraClient("http://api.test", evals_secret="k", transport=httpx.ASGITransport(app=chat_route_app()))
    yield client
    await client.close()


def test_request_helpers_follow_the_browser_transport():
    assistant = {
        "id": "a",
        "role": "assistant",
        "metadata": {"agent": "knowledge"},
        "parts": [{"type": "text", "text": "x"}, {"type": "data-status", "id": "s", "data": {}}],
    }
    user = {"id": "u", "role": "user", "parts": [{"type": "text", "text": "q"}]}
    assert last_agent([user, assistant]) == "knowledge"
    assert last_agent([user, assistant, user]) is None
    assert outgoing_messages([assistant])[0]["parts"] == [{"type": "text", "text": "x"}]
    assert assistant["parts"][1]["type"] == "data-status"  # the stored message is untouched


def test_tool_definitions_come_from_the_api_registry():
    from app.chat.tool_registry import AGENT_TOOLS, TOOLS

    plan_tools = load_tool_definitions("plan_claims")
    assert [t["name"] for t in plan_tools] == list(AGENT_TOOLS["plan_claims"])
    assert plan_tools[4]["parameters"] == TOOLS["estimate_reimbursement"].parameters
    assert [t["name"] for t in load_tool_definitions("knowledge")] == ["search_public_knowledge"]


async def test_approved_draft_claim_round_trip_on_the_real_route(route_client):
    executor = BrowserToolExecutor(plan=load_plan(CA_PLAN), today="2026-09-16")
    conversation = Conversation(route_client, GOLDEN_CONTEXT, executor)
    turn = await conversation.send("Draft a claim for my massage, $120", lambda _n, _i: (True, None))
    assert turn.errors == []
    assert [c["name"] for c in turn.tool_calls] == ["find_benefits", "draft_claim"]
    assert turn.approvals == [{"tool": "draft_claim", "decision": "approve"}]
    assert turn.rounds == 3 and turn.agent == "plan_claims"
    assert "$120.00" in "".join(turn.texts)
    assert executor.claims[-1]["status"] == "draft" and executor.claims[-1]["lines"][0]["chargedCents"] == 12000
    second, third = turn.requests[1], turn.requests[2]
    assert second["activeAgent"] == third["activeAgent"] == "plan_claims"
    assert set(second) == {"messages", "context", "activeAgent"}
    draft = third["messages"][-1]["parts"][-1]
    assert draft["state"] == "output-available" and draft["approval"]["approved"] is True


async def test_declined_draft_claim_matches_the_golden_decline_shape(route_client):
    from evals.tests.conftest import golden_request

    executor = BrowserToolExecutor(plan=load_plan(CA_PLAN), today="2026-09-16")
    conversation = Conversation(route_client, GOLDEN_CONTEXT, executor)
    turn = await conversation.send("Draft a claim for my massage, $120", lambda _n, _i: (False, "Wrong amount"))
    assert turn.errors == [] and executor.claims == []
    assert turn.denied == [turn.tool_calls[-1]["tool_call_id"]]
    assert "won't create that claim" in "".join(turn.texts)
    sent = turn.requests[2]["messages"]
    golden = golden_request("approval-declined")["messages"]

    def shape(messages):
        return [
            [
                (
                    p["type"],
                    p.get("state"),
                    json.dumps(p.get("input"), sort_keys=True),
                    json.dumps(p.get("approval"), sort_keys=True) if p.get("approval") else None,
                )
                for p in m["parts"]
            ]
            for m in messages
        ]

    assert shape(sent) == shape(golden)
    assert {k: v for k, v in sent[-1]["metadata"].items() if k != "traceId"} == {
        k: v for k, v in golden[-1]["metadata"].items() if k != "traceId"
    }


async def test_scenario_rows_through_the_real_route(route_client):
    log = PayloadLog()
    row = await run_scenario(route_client, SCENARIOS["ca-01-massage-estimate-per-visit-cap"], log)
    assert ROW_KEYS <= set(row)
    assert row["meta"]["errors"] == []
    assert [c["name"] for c in row["tool_calls"]] == ["find_benefits", "get_usage"]
    assert all(c["type"] == "tool_call" for c in row["tool_calls"])
    assert {t["name"] for t in row["tool_definitions"]} >= {"estimate_reimbursement", "draft_claim"}
    assert json.loads(row["context"])[1]["output"]["usage"][0]["remainingCents"] == 42000
    assert row["response_messages"][1]["role"] == "tool"
    assert "$420.00" in row["response"]
    assert [e["direction"] for e in log.entries] == ["request", "response"] * 3


async def test_knowledge_rows_resolve_citations_to_manifest_sources(route_client):
    question = next(q for q in load_knowledge_questions() if q.id == "kq-ca-01")
    base = {"today": "2026-06-15", "tz": "UTC", "planName": None, "memberAliases": ["[MEMBER_A]"], "categories": []}
    row = await run_knowledge_question(route_client, question, PayloadLog(), base)
    assert ROW_KEYS <= set(row)
    assert row["meta"]["errors"] == []
    assert row["meta"]["citations"], "the nested knowledge agent's citations reach the row"
    resolved = {c["resolvedSourceId"] for c in row["meta"]["citations"]}
    assert "cra-medical-expenses-lines-33099-33199" in resolved  # matched by URL
    assert row["context"]


async def test_http_errors_are_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "budget_exhausted", "message": "Daily budget used"}})

    client = BenefuraClient("http://api.test", transport=httpx.MockTransport(handler))
    row = await run_scenario(client, SCENARIOS["au-10-physio-schedule-item"], PayloadLog())
    await client.close()
    assert row["meta"]["errors"] == ["http_429:budget_exhausted"]
    assert ROW_KEYS <= set(row) and row["response"] == ""


async def test_evals_headers_are_sent():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, json={"error": {"code": "ai_disabled", "message": "off"}})

    client = BenefuraClient("http://api.test", evals_secret="s3cret", transport=httpx.MockTransport(handler))
    await Conversation(client, GOLDEN_CONTEXT, BrowserToolExecutor(plan=load_plan(CA_PLAN))).send("hi")
    await client.close()
    assert seen[0].headers["x-benefura-budget-namespace"] == "evals"
    assert seen[0].headers["x-benefura-evals-key"] == "s3cret"
    assert json.loads(seen[0].content) == {
        "messages": [{"id": "user-1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        "context": GOLDEN_CONTEXT,
        "activeAgent": None,
    }


async def test_retrieval_rows_are_source_level():
    from dataclasses import dataclass, field

    from evals.harness.datasets import load_retrieval_queries

    @dataclass
    class Hit:
        id: str
        url: str
        score: float
        reranker_score: float | None = None
        section: str | None = None

    @dataclass
    class Result:
        hits: list = field(default_factory=list)
        latency_ms: int = 12

    waiting = "https://www.privatehealth.gov.au/health_insurance/howitworks/waiting_periods.htm"

    async def search(query, region, top_k):
        return Result(
            [
                Hit("phio-waiting-periods-aaaaaaaaaaaaaaaaaaaa", waiting, 0.9, 3.1),
                Hit("phio-waiting-periods-bbbbbbbbbbbbbbbbbbbb", waiting, 0.7, 2.0),
                Hit(
                    "phio-glossary-cccccccccccccccccccc",
                    "https://www.privatehealth.gov.au/footer/glossary.htm",
                    0.5,
                    1.2,
                ),
            ]
        )

    row = await run_retrieval_query(load_retrieval_queries()[0], search)
    assert row["retrieved_documents"] == [
        {"document_id": "phio-waiting-periods", "relevance_score": 3.1},
        {"document_id": "phio-glossary", "relevance_score": 1.2},
    ]
    assert row["retrieval_ground_truth"][0] == {"document_id": "phio-waiting-periods", "query_relevance_label": 4}


def test_write_outputs(tmp_path):
    log = PayloadLog()
    log.add("plan_claims", "x", "request", {"a": 1})
    paths = write_outputs(tmp_path, {"plan_claims": [{"query": "q", "meta": {"id": "x"}}]}, log)
    assert paths["plan_claims"].read_text().count("\n") == 1
    assert json.loads(paths["payloads"].read_text())["direction"] == "request"


async def test_cli_smoke_through_the_real_route(tmp_path, fake_ai_mode):
    args = cli.parse_args(["--smoke", "--out", str(tmp_path)])
    code = await cli.main_async(args, transport=httpx.ASGITransport(app=chat_route_app()))
    assert code == 0
    for name in ("plan_claims", "knowledge"):
        rows = [json.loads(line) for line in (tmp_path / f"{name}.jsonl").read_text().splitlines()]
        assert len(rows) == 2 and all(ROW_KEYS <= set(r) and r["meta"]["errors"] == [] for r in rows)
    assert (tmp_path / "payloads.jsonl").read_text()


async def test_cli_smoke_against_the_fake_api_app(tmp_path, fake_ai_mode):
    from app.main import create_app

    app = create_app()
    code = await cli.main_async(
        cli.parse_args(["--smoke", "--out", str(tmp_path)]), transport=httpx.ASGITransport(app=app)
    )
    assert code == 0
