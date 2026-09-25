"""Runs datasets through the API into evaluator rows, logging every payload so the PII scan sees what was sent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness.browser_tools import BrowserToolExecutor, load_plan
from evals.harness.client import BenefuraClient
from evals.harness.conversation import Conversation, TurnLog
from evals.harness.datasets import (
    KnowledgeQuestion,
    RetrievalQuery,
    Scenario,
    load_sources,
    load_tool_definitions,
    write_jsonl,
)

JSON = dict[str, Any]
REGION_PLANS = {"CA": "samples/fixtures/ca-northwind.plan.json", "AU": "samples/fixtures/au-wattle.plan.json"}


@dataclass
class PayloadLog:
    entries: list[JSON] = field(default_factory=list)

    def add(self, dataset: str, item_id: str, direction: str, payload: Any) -> None:
        self.entries.append({"dataset": dataset, "id": item_id, "direction": direction, "payload": payload})

    def add_turn(self, dataset: str, item_id: str, turn: TurnLog) -> None:
        for request, response in zip(turn.requests, turn.responses, strict=False):
            self.add(dataset, item_id, "request", request)
            self.add(dataset, item_id, "response", response)


def build_response_messages(tool_calls: list[JSON], outputs: dict[str, Any], final_text: str) -> list[JSON]:
    messages: list[JSON] = []
    for call in tool_calls:
        messages.append({"role": "assistant", "content": [call]})
        if call["tool_call_id"] in outputs:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["tool_call_id"],
                    "content": [{"type": "tool_result", "tool_result": outputs[call["tool_call_id"]]}],
                }
            )
    messages.append({"role": "assistant", "content": final_text})
    return messages


def _merge_turns(turns: list[TurnLog]) -> TurnLog:
    merged = TurnLog()
    for t in turns:
        merged.rounds += t.rounds
        merged.texts += t.texts
        merged.tool_calls += t.tool_calls
        merged.outputs.update(t.outputs)
        merged.approvals += t.approvals
        merged.errors += t.errors
        merged.trace_ids += t.trace_ids
        merged.data_types |= t.data_types
        merged.citations += t.citations
        merged.denied += t.denied
        merged.agent = t.agent or merged.agent
        merged.blocked = merged.blocked or t.blocked
    return merged


async def run_scenario(client: BenefuraClient, scenario: Scenario, log: PayloadLog) -> JSON:
    executor = BrowserToolExecutor.for_scenario(scenario.model_dump())
    conversation = Conversation(client, scenario.context, executor)
    turns: list[TurnLog] = []
    for turn in scenario.turns:

        def policy(name: str, _input: Any, approvals: dict = turn.approvals) -> tuple[bool, str | None]:
            choice = approvals.get(name)
            return (choice is not None and choice.decision == "approve"), (choice.reason if choice else None)

        result = await conversation.send(turn.user, policy)
        log.add_turn("plan_claims", scenario.id, result)
        turns.append(result)
    merged = _merge_turns(turns)
    final_text = "\n\n".join(t for t in merged.texts if t)
    return {
        "query": "\n".join(t.user for t in scenario.turns),
        "response": final_text,
        "context": json.dumps(
            [{"tool": c["name"], "output": merged.outputs.get(c["tool_call_id"])} for c in merged.tool_calls],
            ensure_ascii=False,
            default=str,
        ),
        "tool_calls": merged.tool_calls,
        "tool_definitions": load_tool_definitions("plan_claims"),
        "response_messages": build_response_messages(merged.tool_calls, merged.outputs, final_text),
        "meta": {
            "dataset": "plan_claims",
            "id": scenario.id,
            "region": scenario.region,
            "traceIds": merged.trace_ids,
            "rounds": merged.rounds,
            "approvals": merged.approvals,
            "denied": merged.denied,
            "dataParts": sorted(merged.data_types),
            "citations": merged.citations,
            "errors": merged.errors,
            "toolOutputs": merged.outputs,
            "activeAgent": merged.agent,
            "blocked": merged.blocked,
        },
    }


def source_for_url(url: str | None, sources: dict[str, JSON]) -> str | None:
    if not url:
        return None
    norm = url.split("#")[0].rstrip("/")
    for sid, s in sources.items():
        if str(s["url"]).rstrip("/") == norm:
            return sid
    return None


def source_for_chunk_id(chunk_id: str, sources: dict[str, JSON]) -> str | None:
    """Index ids are ``<source-id>-<20 hex>``; a bare source id also resolves."""
    if chunk_id in sources:
        return chunk_id
    prefix = chunk_id.rsplit("-", 1)[0]
    return prefix if prefix in sources else None


def resolve_source_id(item: JSON, sources: dict[str, JSON]) -> str | None:
    return source_for_url(item.get("url"), sources) or source_for_chunk_id(str(item.get("sourceId") or ""), sources)


def knowledge_context(outputs: dict[str, Any]) -> str:
    """Search snippets, else the nested agent's answer."""
    snippets: list[str] = []
    answers: list[str] = []
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for result in output.get("results") or []:
            if isinstance(result, dict) and result.get("snippet"):
                snippets.append(f"[{result.get('title', '')}] {result['snippet']}")
        if isinstance(output.get("answer"), str):
            answers.append(output["answer"])
    return "\n\n".join(snippets or answers)


async def run_knowledge_question(
    client: BenefuraClient, question: KnowledgeQuestion, log: PayloadLog, context: JSON
) -> JSON:
    sources = load_sources()
    ctx = {**context, "region": question.region, "currency": "CAD" if question.region == "CA" else "AUD"}
    executor = BrowserToolExecutor(plan=load_plan(REGION_PLANS[question.region]), today=ctx["today"])
    turn = await Conversation(client, ctx, executor).send(question.question)
    log.add_turn("knowledge", question.id, turn)
    citations = [{**c, "resolvedSourceId": resolve_source_id(c, sources)} for c in turn.citations]
    retrieved = sorted(
        {
            sid
            for output in turn.outputs.values()
            if isinstance(output, dict)
            for r in output.get("results") or []
            if isinstance(r, dict)
            if (sid := resolve_source_id(r, sources))
        }
    )
    text = "\n\n".join(t for t in turn.texts if t)
    return {
        "query": question.question,
        "response": text,
        "context": knowledge_context(turn.outputs),
        "tool_calls": turn.tool_calls,
        "tool_definitions": load_tool_definitions("knowledge" if turn.agent == "knowledge" else "plan_claims"),
        "response_messages": build_response_messages(turn.tool_calls, turn.outputs, text),
        "meta": {
            "dataset": "knowledge",
            "id": question.id,
            "region": question.region,
            "traceIds": turn.trace_ids,
            "citations": citations,
            "expectedSources": question.expected_sources,
            "retrievedSourceIds": retrieved,
            "referenceAnswer": question.reference_answer,
            "dataParts": sorted(turn.data_types),
            "activeAgent": turn.agent,
            "errors": turn.errors,
        },
    }


SearchFn = Callable[[str, str, int], Awaitable[Any]]


async def run_retrieval_query(query: RetrievalQuery, search: SearchFn, top_k: int = 5) -> JSON:
    """Source-level row for ``builtin.document_retrieval``, keeping the best score per source."""
    sources = load_sources()
    result = await search(query.query, query.region, top_k)
    best: dict[str, float] = {}
    hits_meta = []
    for hit in getattr(result, "hits", []):
        sid = source_for_url(getattr(hit, "url", None), sources) or source_for_chunk_id(hit.id, sources) or hit.id
        score = hit.reranker_score if getattr(hit, "reranker_score", None) is not None else hit.score
        best[sid] = max(best.get(sid, float("-inf")), float(score))
        hits_meta.append({"id": hit.id, "sourceId": sid, "section": getattr(hit, "section", None), "score": score})
    return {
        "query": query.query,
        "retrieval_ground_truth": [
            {"document_id": r.sourceId, "query_relevance_label": r.label} for r in query.relevant
        ],
        "retrieved_documents": [
            {"document_id": sid, "relevance_score": s} for sid, s in sorted(best.items(), key=lambda kv: -kv[1])
        ],
        "meta": {
            "dataset": "retrieval",
            "id": query.id,
            "region": query.region,
            "hits": hits_meta,
            "latencyMs": getattr(result, "latency_ms", None),
        },
    }


def write_outputs(out_dir: Path, rows_by_dataset: dict[str, list[JSON]], log: PayloadLog) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, rows in rows_by_dataset.items():
        paths[name] = out_dir / f"{name}.jsonl"
        write_jsonl(paths[name], rows)
    paths["payloads"] = out_dir / "payloads.jsonl"
    write_jsonl(paths["payloads"], log.entries)
    return paths
