"""Spike 5: Agent Framework Extractor/Verifier workflow with structured output, traced without content."""

# No `from __future__ import annotations`: @handler resolves the WorkflowContext type hints at runtime.
import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal, Never

from _common import check, require, settings, timer, write_result
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel, Field

SAMPLE_PAGES = [
    {"page": 3, "markdown": "| Benefit | Coverage | Maximum |\n|---|---|---|\n| Massage therapy | 80% | $500 per benefit year |\n"
     "| Physiotherapy | 80% | $750 per benefit year |\n\nA doctor's referral is required for massage therapy."},
    {"page": 4, "markdown": "Vision care: eye exams once every 24 months for [MEMBER_A] and dependants. Glasses or contact "
     "lenses up to $200 every 24 months."},
]


class Row(BaseModel):
    row_id: str
    benefit: str
    coverage_percent: int | None = None
    max_cents: int | None = None
    period: str | None = None
    page: int
    quote: str = Field(description="Verbatim supporting text from the page")


class Extraction(BaseModel):
    rows: list[Row]


class Verdict(BaseModel):
    row_id: str
    verdict: Literal["supported", "corrected", "unsupported"]
    correction: str | None = None


class Verification(BaseModel):
    verdicts: list[Verdict]


def build_workflow(chat_client: Any, max_rounds: int, threshold: float) -> tuple[Any, dict[str, Any]]:
    stats: dict[str, Any] = {"rounds": 0, "usage": []}
    extractor = chat_client.as_agent(
        name="Extractor",
        instructions="Extract benefit rows from the page markdown. Every row needs page and a verbatim quote. "
        "Money in integer cents. Do not invent values.",
    )
    verifier = chat_client.as_agent(
        name="Verifier",
        instructions="For each row, check the quote exists on the page and supports every value. Return supported, "
        "corrected (with correction) or unsupported.",
    )
    options = {"reasoning": {"effort": settings().reasoning_effort}, "store": False}

    class ExtractExecutor(Executor):
        @handler
        async def run(self, pages: str, ctx: WorkflowContext[dict[str, Any]]) -> None:
            with timer() as t:
                resp = await extractor.run(f"Pages:\n{pages}", options={**options, "response_format": Extraction})
            stats["rounds"] += 1
            stats["usage"].append({"step": "extractor", "ms": t["ms"], "usage": dict(resp.usage_details or {})})
            await ctx.send_message({"pages": pages, "extraction": resp.value})

    class VerifyExecutor(Executor):
        @handler
        async def run(self, payload: dict[str, Any], ctx: WorkflowContext[Never, dict[str, Any]]) -> None:
            pages: str = payload["pages"]
            extraction: Extraction = payload["extraction"]
            for round_no in range(1, max_rounds + 1):
                with timer() as t:
                    resp = await verifier.run(
                        f"Pages:\n{pages}\n\nRows:\n{extraction.model_dump_json()}",
                        options={**options, "response_format": Verification},
                    )
                stats["usage"].append({"step": "verifier", "round": round_no, "ms": t["ms"], "usage": dict(resp.usage_details or {})})
                verification: Verification = resp.value
                unsupported = sum(v.verdict == "unsupported" for v in verification.verdicts)
                ratio = unsupported / max(1, len(verification.verdicts))
                if ratio <= threshold or round_no == max_rounds:
                    await ctx.yield_output({"extraction": extraction, "verification": verification,
                                            "unsupportedRatio": ratio, "verifierRounds": round_no})
                    return
                with timer() as t2:
                    again = await extractor.run(
                        f"Pages:\n{pages}\n\nThese rows were unsupported, re-extract carefully:\n{verification.model_dump_json()}",
                        options={**options, "response_format": Extraction},
                    )
                stats["rounds"] += 1
                stats["usage"].append({"step": "extractor", "ms": t2["ms"], "usage": dict(again.usage_details or {})})
                extraction = again.value

    extract = ExtractExecutor(id="extractor")
    verify = VerifyExecutor(id="verifier")
    return WorkflowBuilder(start_executor=extract).add_edge(extract, verify).build(), stats


async def main_async(pages_path: Path | None) -> None:
    from agent_framework.foundry import FoundryChatClient
    from agent_framework.observability import enable_instrumentation
    from azure.identity.aio import DefaultAzureCredential
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace

    s = settings()
    results: dict[str, bool] = {}
    if s.applicationinsights_connection_string:
        configure_azure_monitor(connection_string=s.applicationinsights_connection_string)
    enable_instrumentation(enable_sensitive_data=False)

    pages = json.loads(pages_path.read_text())[:5] if pages_path else SAMPLE_PAGES
    page_text = "\n\n".join(f"--- page {p['page']} ---\n{p['markdown']}" for p in pages)

    async with DefaultAzureCredential(exclude_managed_identity_credential=True) as cred:
        client = FoundryChatClient(
            project_endpoint=require(s.foundry_project_endpoint, "FOUNDRY_PROJECT_ENDPOINT"),
            model=s.chat_model,
            credential=cred,
        )
        workflow, stats = build_workflow(client, s.verifier_max_rounds, s.verifier_unsupported_threshold)
        tracer = trace.get_tracer("benefura.spikes")
        with tracer.start_as_current_span("spike.s05.workflow") as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            with timer() as t:
                events = await workflow.run(page_text)
        outputs = events.get_outputs()

    output = outputs[0] if outputs else None
    data: dict[str, Any] = {"traceId": trace_id, "wallMs": t["ms"], "pages": len(pages), "stats": stats}
    check(results, "workflow produced an output", output is not None)
    if output:
        rows = output["extraction"].rows
        data |= {"rows": len(rows), "unsupportedRatio": output["unsupportedRatio"], "verifierRounds": output["verifierRounds"]}
        check(results, "structured rows with page + quote", bool(rows) and all(r.page and r.quote for r in rows))
        check(results, "verifier rounds capped", output["verifierRounds"] <= s.verifier_max_rounds)
    check(results, "extractor passes capped at 2", stats["rounds"] <= 2)
    print(f"\nTrace id: {trace_id}. In App Insights: dependencies | where operation_Id == '{trace_id}' -- confirm "
          "gen_ai spans exist and have no gen_ai.input.messages / gen_ai.output.messages attributes.")
    data["checks"] = results
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
    write_result("s05", data, all(results.values()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=Path)
    args = parser.parse_args()
    asyncio.run(main_async(args.pages))


if __name__ == "__main__":
    main()
