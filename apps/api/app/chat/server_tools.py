"""Server-executed chat tools: async generators yielding stream chunks, then one ``ServerToolResult``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from html import escape
from typing import Any

from app.chat import ui_stream
from app.models.plan import Region
from app.services.search import SearchHit, SearchResult

SNIPPET_CHARS = 480
MAX_TOP_K = 8
KNOWLEDGE_STATUS_RUNNING = "Asking the knowledge agent…"
KNOWLEDGE_STATUS_DONE = "Checked public reference material"

SearchFn = Callable[[str, Region, int], Awaitable[SearchResult]]


@dataclass
class ServerToolResult:
    model_output: str
    ui_output: dict[str, Any]
    nested_tool_calls: int = 0


@dataclass
class NestedAgentResult:
    text: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    content_filtered: bool = False


# Yields chunks, then one NestedAgentResult.
NestedRunner = Callable[[str, Region], AsyncIterator["ui_stream.Chunk | NestedAgentResult"]]


@dataclass
class ServerToolContext:
    tool_call_id: str
    region: Region
    search: SearchFn
    run_nested_knowledge: NestedRunner
    # Read back by the nested runner.
    citations: list[dict[str, Any]] = field(default_factory=list)


def citation_for(hit: SearchHit) -> dict[str, Any]:
    return {
        "sourceId": hit.id,
        "title": hit.title,
        "url": hit.url,
        "publisher": hit.publisher,
        "license": hit.license,
        "attribution": hit.attribution,
        "section": hit.section,
        "region": hit.region,
    }


def _snippet(content: str) -> str:
    content = " ".join(content.split())
    return content if len(content) <= SNIPPET_CHARS else content[: SNIPPET_CHARS - 1].rstrip() + "…"


def format_documents(documents: list[dict[str, Any]]) -> str:
    """``<documents>`` wrapping lets the guardrail's indirect-attack detection treat results as untrusted."""
    if not documents:
        return "<documents></documents>"
    rows = []
    for doc in documents:
        attrs = " ".join(
            f'{key}="{escape(str(doc.get(field_name) or ""), quote=True)}"'
            for key, field_name in (
                ("source", "sourceId"),
                ("title", "title"),
                ("publisher", "publisher"),
                ("license", "license"),
                ("url", "url"),
            )
        )
        rows.append(f"<document {attrs}>\n{escape(str(doc.get('content') or ''), quote=False)}\n</document>")
    return "<documents>\n" + "\n".join(rows) + "\n</documents>"


def replay_server_tool_output(name: str, output: Any) -> str | dict[str, Any] | None:
    """Rebuilds what the model originally saw from the compact output the browser stored."""
    if name == "search_public_knowledge" and isinstance(output, dict):
        results = output.get("results")
        if isinstance(results, list):
            docs = [{**r, "content": r.get("snippet", "")} for r in results if isinstance(r, dict)]
            return format_documents(docs)
    if name == "ask_knowledge_agent" and isinstance(output, dict):
        return {"answer": output.get("answer"), "sources": output.get("sources", [])}
    return None


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def clamp_top_k(value: Any) -> int:
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = 5
    return max(1, min(MAX_TOP_K, top_k))


def _region(value: Any, fallback: Region) -> Region:
    return value if value in ("CA", "AU") else fallback


async def search_public_knowledge(
    args: dict[str, Any], ctx: ServerToolContext
) -> AsyncIterator[ui_stream.Chunk | ServerToolResult]:
    query = str(args.get("query") or "").strip()
    region = _region(args.get("region"), ctx.region)
    top_k = clamp_top_k(args.get("top_k"))
    if not query:
        yield ServerToolResult(
            model_output=format_documents([]),
            ui_output={"query": query, "region": region, "results": []},
        )
        return

    result = await ctx.search(query, region, top_k)
    hits = result.hits[:top_k]
    documents = [{**citation_for(h), "content": h.content} for h in hits]
    citations = [citation_for(h) for h in hits]
    ctx.citations.extend(citations)
    if citations:
        yield ui_stream.data("citations", {"citations": citations}, part_id=f"citations-{ctx.tool_call_id}")
    yield ServerToolResult(
        model_output=format_documents(documents),
        ui_output={
            "query": query,
            "region": region,
            "results": [{**citation_for(h), "snippet": _snippet(h.content)} for h in hits],
        },
    )


async def ask_knowledge_agent(
    args: dict[str, Any], ctx: ServerToolContext
) -> AsyncIterator[ui_stream.Chunk | ServerToolResult]:
    question = str(args.get("question") or "").strip()
    region = _region(args.get("region"), ctx.region)
    status_id = f"status-{ctx.tool_call_id}"
    yield ui_stream.data("status", {"message": KNOWLEDGE_STATUS_RUNNING, "state": "running"}, part_id=status_id)

    nested: NestedAgentResult | None = None
    async for event in ctx.run_nested_knowledge(question, region):
        if isinstance(event, NestedAgentResult):
            nested = event
        else:
            yield event
    if nested is None:  # pragma: no cover - the runner always ends with a result
        nested = NestedAgentResult(text="")

    yield ui_stream.data("status", {"message": KNOWLEDGE_STATUS_DONE, "state": "done"}, part_id=status_id)
    sources = [
        {"sourceId": c["sourceId"], "title": c["title"], "publisher": c["publisher"], "license": c["license"]}
        for c in nested.citations
    ]
    answer = nested.text or "The knowledge agent could not answer that."
    if nested.content_filtered:
        answer = "The knowledge agent could not answer that question."
    yield ServerToolResult(
        model_output=compact_json({"answer": answer, "sources": sources}),
        ui_output={
            "question": question,
            "region": region,
            "answer": answer,
            "sources": sources,
            "nestedToolCalls": nested.tool_calls,
        },
        nested_tool_calls=nested.tool_calls,
    )


ServerTool = Callable[[dict[str, Any], ServerToolContext], AsyncIterator["ui_stream.Chunk | ServerToolResult"]]

SERVER_TOOLS: dict[str, ServerTool] = {
    "search_public_knowledge": search_public_knowledge,
    "ask_knowledge_agent": ask_knowledge_agent,
}
