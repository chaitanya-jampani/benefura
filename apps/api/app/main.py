from __future__ import annotations

import io
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.budget import (
    DEMO_NAMESPACES,
    NAMESPACES,
    UsageTracker,
    charge_usage,
    close_budget,
    get_budget,
    namespace_from_request,
)
from app.config import VERSION, Settings, get_settings
from app.errors import ApiError, api_error_handler, http_error_handler, validation_error_handler
from app.fake_hooks import HOOK_HEADERS
from app.limits import client_ip, get_cap_store
from app.middleware import TRACE_HEADER, GuardMiddleware, TraceIdMiddleware
from app.models.api import (
    AnalyzeChunkResponse,
    AssembleRequest,
    AssembleResponse,
    ErrorResponse,
    HealthResponse,
)
from app.pipelines import chunk as chunk_pipeline
from app.pipelines.assemble import assemble_plan
from app.telemetry import configure_telemetry, current_trace_id, instrument_fastapi

logger = logging.getLogger("benefura.api")

ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAGIC = {"application/pdf": b"%PDF-", "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await configure_telemetry()
    yield
    await close_clients()


async def close_clients() -> None:
    from app.pipelines.booklet_workflow import close_workflow_clients
    from app.services.content_understanding import close_cu_client
    from app.services.foundry import close_clients as close_foundry
    from app.services.rest import close_http_client

    for close in (
        close_workflow_clients,
        close_cu_client,
        close_http_client,
        close_budget,
        close_foundry,
    ):
        try:
            await close()
        except Exception as exc:
            logger.warning("Shutdown: %s failed (%s)", close.__name__, type(exc).__name__)


def require_ai(settings: Settings) -> None:
    if not settings.ai_enabled:
        raise ApiError("ai_disabled", "AI features are turned off for this demo right now. Your data is unaffected.")


def trace_id_of(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or current_trace_id()


def parse_pages(raw: str, settings: Settings) -> list[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or not all(p.isdigit() and len(p) <= 4 for p in parts):
        raise ApiError("invalid_request", "pages must be comma-separated page numbers.")
    pages = [int(p) for p in parts]
    if len(pages) > settings.max_chunk_pages:
        raise ApiError("invalid_request", f"A chunk can hold at most {settings.max_chunk_pages} pages.")
    if any(b <= a for a, b in zip(pages, pages[1:], strict=False)) or pages[0] < 1:
        raise ApiError("invalid_request", "pages must be increasing 1-based page numbers.")
    if pages[-1] > settings.max_booklet_pages:
        raise ApiError("page_cap_exceeded", f"Booklets are limited to {settings.max_booklet_pages} pages in the demo.")
    return pages


async def read_upload(file: UploadFile, *, max_bytes: int, allowed: tuple[str, ...]) -> tuple[bytes, str]:
    """A missing or generic declared type is taken from the magic bytes; any other must match them."""
    declared = (file.content_type or "").split(";")[0].strip().lower()
    generic = declared in ("", "application/octet-stream")
    if not generic and declared not in allowed:
        raise ApiError("unsupported_media_type", f"Upload must be one of: {', '.join(allowed)}.")
    if file.size is not None and file.size > max_bytes:
        raise ApiError("payload_too_large", f"File is larger than {max_bytes // (1024 * 1024)} MB.")
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError("payload_too_large", f"File is larger than {max_bytes // (1024 * 1024)} MB.")
    sniffed = next((kind for kind in allowed if data.startswith(MAGIC[kind])), None)
    if sniffed is None or (not generic and sniffed != declared):
        raise ApiError("unsupported_media_type", f"File content must be one of: {', '.join(allowed)}.")
    return data, sniffed


def inspect_pdf(data: bytes, settings: Settings) -> int:
    """Refuses a text layer because it can hold unredacted text."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ApiError("invalid_request", "Encrypted PDFs are not supported.")
        pages = reader.pages
        count = len(pages)
        if settings.require_image_only_pdf and any((page.extract_text() or "").strip() for page in pages):
            raise ApiError(
                "invalid_request", "PDF pages must be redacted images without a text layer. Use the redaction step."
            )
    except PdfReadError as exc:
        raise ApiError("invalid_request", "The PDF could not be read.") from exc
    return count


def create_app() -> FastAPI:
    """CORS is fixed at startup; route handlers read settings per request."""
    settings = get_settings()
    app = FastAPI(title="Benefura API", version=VERSION, lifespan=lifespan)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    # add_middleware prepends, so the order is TraceId → CORS → Guard: every response gets a trace id and
    # guard errors stay readable cross-origin.
    app.add_middleware(GuardMiddleware)
    allow_headers = ["content-type"] + (list(HOOK_HEADERS) if settings.ai_mode == "fake" else [])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=allow_headers,
        expose_headers=[TRACE_HEADER, "retry-after"],
        max_age=600,
    )
    app.add_middleware(TraceIdMiddleware)
    instrument_fastapi(app)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        settings = get_settings()
        budget = get_budget()
        budgets: dict[str, float] = {}
        ok = True
        try:
            for namespace in NAMESPACES:
                budgets[namespace] = await budget.remaining_pct(namespace)
        except Exception as exc:
            logger.warning("Budget ledger unavailable for /healthz (%s)", type(exc).__name__)
            ok = False
        demo = [budgets[n] for n in DEMO_NAMESPACES if n in budgets]
        return HealthResponse(
            ok=ok,
            aiEnabled=settings.ai_enabled,
            aiMode=settings.ai_mode,
            budgetRemainingPct=min(demo) if demo else 0.0,
            budgets=budgets,
            agentVersions=settings.agent_versions,
            version=VERSION,
        )

    @app.post("/api/plan/analyze-chunk", response_model=AnalyzeChunkResponse, responses=ERROR_RESPONSES)
    async def analyze_chunk(
        request: Request,
        documentId: Annotated[str, Form(description="Random per-booklet id from the browser (booklet and page caps).")],
        region: Annotated[Literal["CA", "AU"], Form()],
        pages: Annotated[str, Form(description="Comma-separated 1-based page numbers in this chunk.")],
        file: Annotated[UploadFile, File(description="application/pdf, ≤8 MB, image-only pages.")],
    ) -> AnalyzeChunkResponse:
        settings = get_settings()
        require_ai(settings)
        if not DOCUMENT_ID_RE.match(documentId):
            raise ApiError("invalid_request", "documentId must be 8–64 letters, digits, '-' or '_'.")
        page_numbers = parse_pages(pages, settings)
        data, _ = await read_upload(file, max_bytes=settings.max_chunk_bytes, allowed=("application/pdf",))
        if inspect_pdf(data, settings) != len(page_numbers):
            raise ApiError("invalid_request", "The number of PDF pages does not match pages.")

        namespace = namespace_from_request(request, "demo-extraction")
        budget = get_budget()
        await budget.ensure_available(namespace)
        await get_cap_store().register_chunk(client_ip(request), documentId, page_numbers)

        tracker = UsageTracker()
        try:
            result = await chunk_pipeline.analyze_chunk(data, region=region, pages=page_numbers, tracker=tracker)
        finally:
            usage = await charge_usage(budget, namespace, tracker)
        return AnalyzeChunkResponse(
            pages=page_numbers, rows=result.rows, issues=result.issues, usage=usage, traceId=trace_id_of(request)
        )

    @app.post("/api/plan/assemble", response_model=AssembleResponse, responses=ERROR_RESPONSES)
    async def assemble(body: AssembleRequest) -> AssembleResponse:
        return assemble_plan(body)

    return app


app = create_app()
