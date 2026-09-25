"""Spike 7: Foundry IQ knowledge base MCP tool and continuous evaluation, both with store=False.

M0-verify: everything here; preview REST versions and request shapes change.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import httpx
from _common import SEARCH_SCOPE, bearer, check, openai_client, project_client, require, settings, write_result

SEARCH_PREVIEW_API = "2026-04-01"  # azure-search-documents 12.0 default. M0-verify: MCP path
KS_NAME = "benefura-spike-ks"
KB_NAME = "benefura-spike-kb"
AGENT_NAME = "benefura-spike-s07"


def search_put(client: httpx.Client, path: str, body: dict[str, Any]) -> httpx.Response:
    base = require(settings().search_endpoint, "SEARCH_ENDPOINT").rstrip("/")
    return client.put(f"{base}/{path}?api-version={SEARCH_PREVIEW_API}", json=body,
                      headers={"Authorization": f"Bearer {bearer(SEARCH_SCOPE)}"})


def search_delete(client: httpx.Client, path: str) -> None:
    base = settings().search_endpoint.rstrip("/")
    client.delete(f"{base}/{path}?api-version={SEARCH_PREVIEW_API}", headers={"Authorization": f"Bearer {bearer(SEARCH_SCOPE)}"})


def foundry_iq(mcp_connection_id: str | None, keep: bool) -> dict[str, Any]:
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition

    out: dict[str, Any] = {}
    s = settings()
    with httpx.Client(timeout=60) as http:
        ks = search_put(http, f"knowledgesources/{KS_NAME}", {
            "name": KS_NAME,
            "kind": "searchIndex",
            "searchIndexParameters": {"searchIndexName": s.search_index,
                                      "sourceDataFields": [{"name": "title"}, {"name": "url"}, {"name": "license"}]},
        })
        out["knowledgeSource"] = {"status": ks.status_code, "body": ks.text[:300]}
        kb = search_put(http, f"knowledgebases/{KB_NAME}", {
            "name": KB_NAME,
            "description": "Spike 7: public CA/AU benefits knowledge (no models: Free tier has no managed identity)",
            "knowledgeSources": [{"name": KS_NAME}],
        })
        out["knowledgeBase"] = {"status": kb.status_code, "body": kb.text[:300]}
        mcp_url = f"{s.search_endpoint.rstrip('/')}/knowledgebases/{KB_NAME}/mcp?api-version={SEARCH_PREVIEW_API}"
        out["mcpUrl"] = mcp_url

        project = project_client()
        tool = MCPTool(server_label="public_knowledge", server_url=mcp_url, require_approval="never",
                       allowed_tools=["knowledge_base_retrieve"], project_connection_id=mcp_connection_id)
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(model=s.chat_model, tools=[tool],
                                             instructions="Answer from the knowledge base only and cite the source title."),
        )
        try:
            resp = project.get_openai_client().responses.create(
                input="Can massage therapy count as a medical expense for the Canada tax credit?",
                store=False,
                extra_body={"agent_reference": {"name": AGENT_NAME, "version": str(agent.version), "type": "agent_reference"}},
            )
            out["answer"] = resp.output_text[:500]
            out["outputTypes"] = [o.type for o in resp.output]
        except Exception as exc:  # noqa: BLE001
            out["error"] = repr(exc)[:500]
        finally:
            if not keep:
                project.agents.delete_version(agent_name=AGENT_NAME, agent_version=str(agent.version))
                search_delete(http, f"knowledgebases/{KB_NAME}")
                search_delete(http, f"knowledgesources/{KS_NAME}")
    return out


def continuous_eval(wait_minutes: int, keep: bool) -> dict[str, Any]:
    from azure.ai.projects.models import (
        ContinuousEvaluationRuleAction,
        EvaluationRule,
        EvaluationRuleFilter,
        PromptAgentDefinition,
    )

    s = settings()
    out: dict[str, Any] = {}
    project = project_client()
    oai = openai_client()
    agent = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(model=s.chat_model, instructions="Answer benefits questions in one sentence."),
    )
    evaluation = oai.evals.create(  # M0-verify: data source config for agent response events
        name="benefura-spike-continuous",
        data_source_config={"type": "azure_ai_source", "scenario": "responses"},
        testing_criteria=[{"type": "azure_ai_evaluator", "name": "violence", "evaluator_name": "builtin.violence"}],
    )
    rule_id = "benefura-spike-continuous"
    try:
        project.evaluation_rules.create_or_update(
            id=rule_id,
            evaluation_rule=EvaluationRule(
                display_name="Spike 7 continuous eval",
                action=ContinuousEvaluationRuleAction(eval_id=evaluation.id, max_hourly_runs=10),
                filter=EvaluationRuleFilter(agent_name=AGENT_NAME),
                event_type="responseCompleted",
                enabled=True,
            ),
        )
        client = project.get_openai_client()
        for q in ["What is a benefit year?", "What does coinsurance mean?", "Is dental covered by provincial plans?"]:
            client.responses.create(input=q, store=False,
                                    extra_body={"agent_reference": {"name": AGENT_NAME, "version": str(agent.version), "type": "agent_reference"}})
        deadline = time.time() + wait_minutes * 60
        runs: list[Any] = []
        while time.time() < deadline:
            runs = list(oai.evals.runs.list(eval_id=evaluation.id))
            if runs:
                break
            time.sleep(30)
        out["evalId"] = evaluation.id
        out["runs"] = [{"id": r.id, "status": r.status} for r in runs]
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)[:500]
    finally:
        if not keep:
            try:
                project.evaluation_rules.delete(id=rule_id)
            except Exception as exc:  # noqa: BLE001
                out["cleanupError"] = repr(exc)[:200]
            project.agents.delete_version(agent_name=AGENT_NAME, agent_version=str(agent.version))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-iq", action="store_true")
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--mcp-connection-id")
    parser.add_argument("--wait-minutes", type=int, default=10)
    parser.add_argument("--keep", action="store_true", help="Keep the throwaway agent/knowledge base/rule")
    args = parser.parse_args()

    results: dict[str, bool] = {}
    data: dict[str, Any] = {}
    if not args.skip_iq:
        data["foundryIq"] = iq = foundry_iq(args.mcp_connection_id, args.keep)
        check(results, "knowledge base created", iq["knowledgeBase"]["status"] in (200, 201, 204), iq["knowledgeBase"]["body"][:120])
        check(results, "MCP tool answered with store=False", "answer" in iq and "mcp_call" in iq.get("outputTypes", []),
              iq.get("error", ""))
    if not args.skip_eval:
        data["continuousEval"] = ce = continuous_eval(args.wait_minutes, args.keep)
        check(results, "continuous eval produced a run with store=False", bool(ce.get("runs")), ce.get("error", ""))
    data["checks"] = results
    data["decision"] = {
        "adoptFoundryIq": results.get("MCP tool answered with store=False", False),
        "adoptContinuousEval": results.get("continuous eval produced a run with store=False", False),
    }
    # Recorded, not pass/fail: either outcome is a valid decision.
    write_result("s07", data, None)


if __name__ == "__main__":
    main()
