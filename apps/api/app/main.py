from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import VERSION, get_settings
from app.errors import ApiError, api_error_handler, http_error_handler, validation_error_handler
from app.models.api import HealthResponse


def create_app() -> FastAPI:
    """CORS is fixed at startup; route handlers read settings per request."""
    settings = get_settings()
    app = FastAPI(title="Benefura API", version=VERSION)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
        max_age=600,
    )

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        settings = get_settings()
        return HealthResponse(
            ok=True,
            aiEnabled=settings.ai_enabled,
            aiMode=settings.ai_mode,
            budgetRemainingPct=100.0,
            agentVersions=settings.agent_versions,
            version=VERSION,
        )

    return app


app = create_app()
