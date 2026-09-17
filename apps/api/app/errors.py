from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models.api import ErrorBody, ErrorCode, ErrorResponse, PiiDetectedDetail

STATUS_BY_CODE: dict[str, int] = {
    "payload_too_large": 413,
    "unsupported_media_type": 415,
    "pii_detected": 422,
    "unsafe_image": 422,
    "content_filtered": 422,
    "prompt_injection": 422,
    "invalid_request": 400,
    "page_cap_exceeded": 413,
    "booklet_cap_exceeded": 429,
    "rate_limited": 429,
    "budget_exhausted": 429,
    "ai_disabled": 503,
    "upstream_error": 502,
    "internal_error": 500,
}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: list[PiiDetectedDetail] | None = None,
        retry_after: int | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = details
        self.retry_after = retry_after
        self.status = status or STATUS_BY_CODE.get(code, 400)


class ContentFilteredError(Exception):
    pass


def is_content_filter_error(exc: BaseException) -> bool:
    """Checks the whole cause chain; Content Understanding reports its internal model's blocks as an error code."""
    from agent_framework.exceptions import AgentContentFilterException, ChatClientContentFilterException

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ContentFilteredError | ChatClientContentFilterException | AgentContentFilterException):
            return True
        code = getattr(current, "code", None)
        error = getattr(current, "error", None)
        if error is not None:
            code = getattr(error, "code", None) or code
        # M0-verify: exact error code CU returns when its completion model hits the guardrail.
        if isinstance(code, str) and code.lower() in {
            "content_filter",
            "contentfilter",
            "responsibleaipolicyviolation",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def error_response(err: ApiError, trace_id: str | None = None) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=err.code,
            message=err.message,
            details=err.details,
            retryAfterSeconds=err.retry_after,
            traceId=trace_id,
        )
    )
    headers = {"retry-after": str(err.retry_after)} if err.retry_after else None
    return JSONResponse(status_code=err.status, content=body.model_dump(mode="json"), headers=headers)


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc, _trace_id(request))


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Names the offending fields only; submitted values are never echoed."""
    assert isinstance(exc, RequestValidationError)
    fields = sorted({".".join(str(p) for p in err.get("loc", ()) if not isinstance(p, int)) for err in exc.errors()})
    message = "Invalid request" + (f": check {', '.join(f for f in fields if f)}" if fields else "")
    return error_response(ApiError("invalid_request", message[:300]), _trace_id(request))


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 413:
        err = ApiError("payload_too_large", "Request body too large")
    elif exc.status_code == 415:
        err = ApiError("unsupported_media_type", "Unsupported media type")
    else:
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code in (404, 405) else "Invalid request"
        err = ApiError("invalid_request", message, status=exc.status_code)
    response = error_response(err, _trace_id(request))
    if exc.headers:
        response.headers.update(exc.headers)
    return response
