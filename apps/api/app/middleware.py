from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings
from app.errors import ApiError, error_response
from app.fake_hooks import parse_hooks, reset_hooks, use_hooks
from app.limits import enforce_rate_limit
from app.telemetry import ensure_tracer_provider, get_tracer

logger = logging.getLogger("benefura.http")

TRACE_HEADER = "x-benefura-trace-id"


def _trace_id_of(span: trace.Span) -> str | None:
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = _trace_id_of(trace.get_current_span())
        if trace_id:
            await self._run(scope, receive, send, trace_id)
            return
        # FastAPI instrumentation is absent or no SDK provider is configured yet: make a real span.
        ensure_tracer_provider()
        name = f"{scope.get('method', 'HTTP')} {scope.get('path', '')}"
        with get_tracer().start_as_current_span(name, kind=SpanKind.SERVER) as span:
            await self._run(scope, receive, send, _trace_id_of(span) or "0" * 32)

    async def _run(self, scope: Scope, receive: Receive, send: Send, trace_id: str) -> None:
        scope.setdefault("state", {})["trace_id"] = trace_id

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[TRACE_HEADER] = trace_id
            await send(message)

        await self.app(scope, receive, send_with_trace)


class _BodyTooLarge(Exception):
    pass


def body_limit_for(path: str, settings: Settings) -> int:
    if path == "/api/plan/analyze-chunk":
        return settings.max_chunk_bytes + settings.multipart_overhead_bytes
    if path == "/api/receipts/analyze":
        return settings.max_receipt_bytes + settings.multipart_overhead_bytes
    if path == "/api/chat":
        return settings.max_chat_body_bytes
    if path == "/api/plan/assemble":
        return settings.max_assemble_body_bytes
    return settings.max_default_body_bytes


class GuardMiddleware:
    def __init__(self, app: ASGIApp, settings_provider: Callable[[], Settings] = get_settings) -> None:
        self.app = app
        self.settings_provider = settings_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = self.settings_provider()
        state = scope.setdefault("state", {})
        trace_id: str | None = state.get("trace_id")
        headers = Headers(scope=scope)
        started = False
        tripped = False

        async def send_error(err: ApiError) -> None:
            response = error_response(err, trace_id)
            await response(scope, _empty_receive, send)

        limit = body_limit_for(scope.get("path", ""), settings)
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            await send_error(_too_large() if length.isdigit() else ApiError("invalid_request", "Bad Content-Length"))
            return

        if scope.get("method") == "POST" and scope.get("path", "").startswith("/api/"):
            try:
                enforce_rate_limit(HTTPConnection(scope))
            except ApiError as err:
                await send_error(err)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received, tripped
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    tripped = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if tripped:
                return  # the inner app is answering a body it never fully read; we send 413 instead
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        token = use_hooks(parse_hooks(headers)) if settings.ai_mode == "fake" else None
        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception as exc:
            if not tripped:
                trace.get_current_span().set_status(Status(StatusCode.ERROR, type(exc).__name__))
                # Type and trace id only: exception messages can carry request-derived text.
                logger.error("Unhandled %s (trace %s)", type(exc).__name__, trace_id)
                if not started:
                    await send_error(ApiError("internal_error", "Something went wrong on our side."))
                return
        finally:
            if token is not None:
                reset_hooks(token)
        if tripped and not started:
            await send_error(_too_large())


def _too_large() -> ApiError:
    return ApiError("payload_too_large", "Request body too large")


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
