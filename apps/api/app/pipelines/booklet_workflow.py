"""Booklet extraction as an Agent Framework workflow; fake mode runs the same graph with a fake chat client."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, Never, cast

from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Executor,
    FunctionInvocationLayer,
    Message,
    SupportsChatGetResponse,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.exceptions import ChatClientContentFilterException
from agent_framework.observability import ChatTelemetryLayer
from pydantic import BaseModel, ValidationError

from app.budget import UsageTracker
from app.config import get_settings
from app.errors import ApiError
from app.fake_hooks import current_hooks
from app.models.api import ExtractedRows, Issue
from app.models.extraction import (
    ExtractionChunk,
    PageTriage,
    PageTriageResult,
    RowVerdict,
    VerifierResult,
)
from app.models.plan import Region
from app.pipelines.grounding import ROW_FIELDS, merge_extraction
from app.telemetry import record_verifier_verdicts

PROMPTS_DIR = Path(__file__).parent / "prompts"
WORKFLOW_NAME = "benefura-booklet-extraction"


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


@dataclass
class PageText:
    page: int
    markdown: str


@dataclass
class ChunkJob:
    region: Region
    pages: list[PageText]
    tracker: UsageTracker
    issues: list[Issue] = field(default_factory=list)
    extract_rounds: int = 0


@dataclass
class ExtractRequest:
    job: ChunkJob
    pages: list[PageText]
    round: int
    previous: ExtractionChunk | None = None
    feedback: list[RowVerdict] = field(default_factory=list)


@dataclass
class VerifyRequest:
    job: ChunkJob
    pages: list[PageText]
    round: int
    chunk: ExtractionChunk


@dataclass
class MergeRequest:
    job: ChunkJob
    pages: list[PageText]
    chunk: ExtractionChunk | None
    verdicts: list[RowVerdict] = field(default_factory=list)
    rounds_exhausted: bool = False


@dataclass
class ChunkExtraction:
    rows: ExtractedRows
    issues: list[Issue]
    extract_rounds: int


def _escape(markdown: str) -> str:
    # Keep page text from opening or closing our prompt tags.
    return re.sub(
        r"</?\s*(pages?|rows|verifier_feedback|region)\b", lambda m: m.group(0).replace("<", "&lt;"), markdown
    )


def render_pages(pages: Sequence[PageText]) -> str:
    body = "\n".join(f'<page number="{p.page}">\n{_escape(p.markdown)}\n</page>' for p in pages)
    return f"<pages>\n{body}\n</pages>"


def _compact(chunk: ExtractionChunk) -> str:
    data = {
        name: [{k: v for k, v in row.model_dump().items() if v is not None} for row in getattr(chunk, name)]
        for name in ROW_FIELDS
    }
    return json.dumps({k: v for k, v in data.items() if v}, ensure_ascii=False)


def triage_prompt(job: ChunkJob) -> str:
    numbers = ", ".join(str(p.page) for p in job.pages)
    return f"<region>{job.region}</region>\nTriage pages {numbers}.\n{render_pages(job.pages)}"


def extract_prompt(request: ExtractRequest) -> str:
    parts = [f"<region>{request.job.region}</region>", render_pages(request.pages)]
    if request.previous is not None and request.feedback:
        feedback = [{"row_id": v.row_id, "reason": v.reason} for v in request.feedback]
        parts.append(
            "<verifier_feedback>\n"
            f"previous_rows: {_escape(_compact(request.previous))}\n"
            f"unsupported: {_escape(json.dumps(feedback, ensure_ascii=False))}\n"
            "</verifier_feedback>"
        )
    return "\n".join(parts)


def verify_prompt(request: VerifyRequest) -> str:
    rows = _escape(_compact(request.chunk))
    return f"<region>{request.job.region}</region>\n{render_pages(request.pages)}\n<rows>\n{rows}\n</rows>"


def total_rows(chunk: ExtractionChunk) -> int:
    return sum(len(getattr(chunk, name)) for name in ROW_FIELDS)


def row_ids(chunk: ExtractionChunk) -> list[str]:
    return [row.row_id for name in ROW_FIELDS for row in getattr(chunk, name)]


async def run_structured[ModelT: BaseModel](
    agent: Agent[Any],
    prompt: str,
    response_format: type[ModelT],
    *,
    job: ChunkJob,
    step: str,
    max_tokens: int,
) -> ModelT:
    settings = get_settings()
    options: dict[str, Any] = {
        "response_format": response_format,
        "store": False,
        "reasoning": {"effort": settings.reasoning_effort},  # M0-verify: effort values per model
        "max_tokens": max_tokens,  # includes reasoning tokens
    }
    started = perf_counter()
    with job.tracker.step(step, agent=agent.name or step):
        response = await agent.run(prompt, options=cast(Any, options))
    usage = response.usage_details or {}
    model = str(getattr(agent.client, "model", None) or settings.chat_model)
    job.tracker.add_tokens(
        model,
        int(usage.get("input_token_count") or 0),
        int(usage.get("output_token_count") or 0),
        agent=agent.name,
        ms=int((perf_counter() - started) * 1000),
    )
    value = response.value
    if not isinstance(value, response_format):
        raise ValueError(f"{step} returned no structured output")
    return value


class PageTriageExecutor(Executor):
    def __init__(self, agent: Agent[Any]) -> None:
        super().__init__(id="page_triage")
        self.agent = agent

    @handler
    async def triage(self, job: ChunkJob, ctx: WorkflowContext[ExtractRequest | MergeRequest]) -> None:
        try:
            result = await run_structured(
                self.agent, triage_prompt(job), PageTriageResult, job=job, step="triage", max_tokens=2000
            )
            verdicts: dict[int, PageTriage] = {t.page: t for t in result.pages}
        except (ValueError, ValidationError):
            verdicts = {}  # fail open: extraction completeness beats a cheaper call
        relevant = [p for p in job.pages if verdicts.get(p.page) is None or verdicts[p.page].relevant]
        for page in job.pages:
            if page not in relevant:
                job.issues.append(
                    Issue(code="page_irrelevant", severity="info", message="Page skipped by triage.", page=page.page)
                )
        if not relevant:
            await ctx.send_message(MergeRequest(job=job, pages=job.pages, chunk=None))
            return
        await ctx.send_message(ExtractRequest(job=job, pages=relevant, round=1))


class ExtractorExecutor(Executor):
    def __init__(self, agent: Agent[Any]) -> None:
        super().__init__(id="extractor")
        self.agent = agent

    @handler
    async def extract(self, request: ExtractRequest, ctx: WorkflowContext[VerifyRequest]) -> None:
        request.job.extract_rounds = request.round
        try:
            chunk = await run_structured(
                self.agent,
                extract_prompt(request),
                ExtractionChunk,
                job=request.job,
                step="extract",
                max_tokens=16000,
            )
        except (ValueError, ValidationError) as exc:
            if request.previous is None:
                raise ApiError("upstream_error", "Extraction returned an unreadable answer; retry this chunk.") from exc
            chunk = request.previous  # keep the first round's rows rather than failing the chunk
        await ctx.send_message(VerifyRequest(job=request.job, pages=request.pages, round=request.round, chunk=chunk))


class VerifierExecutor(Executor):
    def __init__(self, agent: Agent[Any], *, max_rounds: int, unsupported_threshold: float) -> None:
        super().__init__(id="verifier")
        self.agent = agent
        self.max_rounds = max_rounds
        self.unsupported_threshold = unsupported_threshold

    @handler
    async def verify(self, request: VerifyRequest, ctx: WorkflowContext[ExtractRequest | MergeRequest]) -> None:
        total = total_rows(request.chunk)
        if total == 0:
            await ctx.send_message(MergeRequest(job=request.job, pages=request.pages, chunk=request.chunk))
            return
        try:
            result = await run_structured(
                self.agent, verify_prompt(request), VerifierResult, job=request.job, step="verify", max_tokens=8000
            )
            known = set(row_ids(request.chunk))
            verdicts = [v for v in result.verdicts if v.row_id in known]
        except (ValueError, ValidationError):
            verdicts = []
            request.job.issues.append(
                Issue(code="low_confidence", severity="warning", message="Verifier unavailable for this chunk.")
            )
        counts = {k: sum(1 for v in verdicts if v.verdict == k) for k in ("supported", "corrected", "unsupported")}
        record_verifier_verdicts(counts)
        unsupported = [v for v in verdicts if v.verdict == "unsupported"]
        too_many = len(unsupported) / total > self.unsupported_threshold
        if too_many and request.round < self.max_rounds:
            await ctx.send_message(
                ExtractRequest(
                    job=request.job,
                    pages=request.pages,
                    round=request.round + 1,
                    previous=request.chunk,
                    feedback=unsupported,
                )
            )
            return
        if too_many:
            request.job.issues.append(
                Issue(
                    code="verifier_rounds_exhausted",
                    severity="warning",
                    message=f"{len(unsupported)} of {total} rows stayed unsupported after {request.round} rounds.",
                )
            )
        await ctx.send_message(
            MergeRequest(
                job=request.job, pages=request.pages, chunk=request.chunk, verdicts=verdicts, rounds_exhausted=too_many
            )
        )


class MergeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="merge")

    @handler
    async def merge(self, request: MergeRequest, ctx: WorkflowContext[Never, ChunkExtraction]) -> None:
        job = request.job
        if request.chunk is None:
            await ctx.yield_output(ChunkExtraction(rows=ExtractedRows(), issues=job.issues, extract_rounds=0))
            return
        with job.tracker.step("merge"):
            rows, issues = merge_extraction(
                request.chunk,
                request.verdicts,
                {p.page: p.markdown for p in request.pages},
                rounds_exhausted=request.rounds_exhausted,
            )
        await ctx.yield_output(
            ChunkExtraction(rows=rows, issues=[*job.issues, *issues], extract_rounds=job.extract_rounds)
        )


@dataclass(frozen=True)
class WorkflowClients:
    triage: SupportsChatGetResponse[Any]
    extractor: SupportsChatGetResponse[Any]
    verifier: SupportsChatGetResponse[Any]


def _is(kind: type) -> Any:
    return lambda message: isinstance(message, kind)


def build_workflow(clients: WorkflowClients) -> Workflow:
    """A fresh graph per chunk: a ``Workflow`` instance does not allow concurrent runs."""
    settings = get_settings()
    triage = PageTriageExecutor(
        Agent(clients.triage, load_prompt("page_triage"), name="PageTriage", description="Flags pages worth extracting")
    )
    extractor = ExtractorExecutor(
        Agent(clients.extractor, load_prompt("extractor"), name="Extractor", description="Extracts plan rows")
    )
    verifier = VerifierExecutor(
        Agent(clients.verifier, load_prompt("verifier"), name="Verifier", description="Checks rows against pages"),
        max_rounds=settings.verifier_max_rounds,
        unsupported_threshold=settings.verifier_unsupported_threshold,
    )
    merge = MergeExecutor()
    return (
        WorkflowBuilder(
            start_executor=triage,
            name=WORKFLOW_NAME,
            description="Triage → extract ⇄ verify → deterministic merge for one booklet chunk",
            max_iterations=4 + 2 * settings.verifier_max_rounds,
        )
        .add_edge(triage, extractor, condition=_is(ExtractRequest))
        .add_edge(triage, merge, condition=_is(MergeRequest))
        .add_edge(extractor, verifier)
        .add_edge(verifier, extractor, condition=_is(ExtractRequest))
        .add_edge(verifier, merge, condition=_is(MergeRequest))
        .build()
    )


async def run_extraction(
    region: Region, pages: Sequence[PageText], tracker: UsageTracker, *, clients: WorkflowClients | None = None
) -> ChunkExtraction:
    """Content-filter errors propagate to the caller."""
    job = ChunkJob(region=region, pages=list(pages), tracker=tracker)
    workflow = build_workflow(clients or get_workflow_clients())
    result = await workflow.run(job)
    outputs = [o for o in result.get_outputs() if isinstance(o, ChunkExtraction)]
    if not outputs:
        raise ApiError("upstream_error", "Extraction did not complete; retry this chunk.")
    return outputs[-1]


@lru_cache
def get_workflow_clients() -> WorkflowClients:
    settings = get_settings()
    if settings.ai_mode != "live":
        nano = FakeBookletChatClient(model=settings.nano_model)
        mini = FakeBookletChatClient(model=settings.chat_model)
        return WorkflowClients(triage=nano, extractor=mini, verifier=mini)

    from agent_framework_foundry import FoundryChatClient

    from app.services.foundry import get_project_client

    def foundry(model: str) -> FoundryChatClient[Any]:
        client: FoundryChatClient[Any] = FoundryChatClient(project_client=get_project_client(), model=model)
        # 429s from the shared TPM quota: let the OpenAI SDK back off (honours retry-after) a few more times.
        client.client = client.client.with_options(max_retries=5)
        return client

    nano, mini = foundry(settings.nano_model), foundry(settings.chat_model)
    return WorkflowClients(triage=nano, extractor=mini, verifier=mini)


async def close_workflow_clients() -> None:
    if get_workflow_clients.cache_info().currsize:
        clients = get_workflow_clients()
        for client in {id(c): c for c in (clients.triage, clients.extractor, clients.verifier)}.values():
            openai_client = getattr(client, "client", None)
            if openai_client is not None and hasattr(openai_client, "close"):
                await openai_client.close()
    get_workflow_clients.cache_clear()


def golden_extraction(region: Region) -> ExtractionChunk:
    from app.services.content_understanding import GOLDEN_DOC_BY_REGION

    path = get_settings().samples_dir / "golden" / f"{GOLDEN_DOC_BY_REGION[region]}.extraction.json"
    if not path.exists():
        raise ApiError("upstream_error", f"Fake mode needs {path.name} in the samples directory.")
    return ExtractionChunk.model_validate_json(path.read_text())


def filter_chunk(chunk: ExtractionChunk, pages: set[int]) -> ExtractionChunk:
    return ExtractionChunk.model_validate(
        {name: [r.model_dump() for r in getattr(chunk, name) if r.page in pages] for name in ROW_FIELDS}
    )


class FakeBookletChatClient(
    FunctionInvocationLayer[Any], ChatMiddlewareLayer[Any], ChatTelemetryLayer[Any], BaseChatClient[Any]
):
    """Triage keeps every page, the extractor returns the golden rows for the prompt's pages, the verifier
    supports every row."""

    OTEL_PROVIDER_NAME = "benefura.fake"

    def __init__(self, *, model: str) -> None:
        super().__init__()
        self.model = model

    def _inner_get_response(
        self, *, messages: Sequence[Message], stream: bool, options: Mapping[str, Any], **kwargs: Any
    ) -> Awaitable[ChatResponse]:
        if stream:
            raise NotImplementedError("FakeBookletChatClient does not stream")
        return self._respond(messages, options)

    async def _respond(self, messages: Sequence[Message], options: Mapping[str, Any]) -> ChatResponse:
        if current_hooks().content_filter:
            raise ChatClientContentFilterException("fake guardrail: content_filter (ResponsibleAIPolicyViolation)")
        prompt = "\n".join(m.text for m in messages)
        response_format = options.get("response_format")
        pages = [int(n) for n in re.findall(r'<page number="(\d+)">', prompt)]
        region_match = re.search(r"<region>(CA|AU)</region>", prompt)
        region: Region = cast(Region, region_match.group(1) if region_match else "CA")
        value: BaseModel
        if response_format is PageTriageResult:
            value = PageTriageResult(
                pages=[PageTriage(page=p, relevant=True, sectionType="benefit_table") for p in pages]
            )
        elif response_format is ExtractionChunk:
            value = filter_chunk(golden_extraction(region), set(pages))
        elif response_format is VerifierResult:
            rows_match = re.search(r"<rows>\n(.*)\n</rows>", prompt, re.S)
            data = json.loads(rows_match.group(1)) if rows_match else {}
            ids = [row["row_id"] for rows in data.values() for row in rows]
            value = VerifierResult(
                verdicts=[RowVerdict(row_id=i, verdict="supported", correction=None, reason=None) for i in ids]
            )
        else:
            raise ValueError("FakeBookletChatClient only answers the booklet workflow's structured outputs")
        text = value.model_dump_json()
        return ChatResponse(
            messages=[Message("assistant", [text])],
            model=self.model,
            finish_reason="stop",
            usage_details={"input_token_count": len(prompt) // 4, "output_token_count": len(text) // 4},
            response_format=response_format,
        )
