"""FastAPI application entrypoint."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app import __version__
from app.api.errors import AppError, ErrorResponse, app_error_handler
from app.api.v1 import events, health, ingest, series
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.request_id import RequestIdMiddleware
from app.db.session import dispose_engine, get_session_factory
from app.services.audit import resolve_audit_user_id, write_request_audit

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    yield
    await dispose_engine()


class AuditMiddleware(BaseHTTPMiddleware):
    """Capture metadata-only request audit after each response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = datetime.now(UTC)
        started_perf = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            latency_ms = int((time.perf_counter() - started_perf) * 1000)
            status_code = response.status_code if response is not None else 500
            request_id = getattr(request.state, "request_id", None)
            if request_id is not None and request.url.path != "/health":
                try:
                    session_factory = get_session_factory()
                    audit_user_id = await resolve_audit_user_id(
                        session_factory,
                        getattr(request.state, "external_user_id", None),
                    )
                    await write_request_audit(
                        session_factory,
                        request_id=request_id,
                        user_id=audit_user_id,
                        auth_role=getattr(request.state, "auth_role", None),
                        method=request.method,
                        path=request.url.path,
                        status_code=status_code,
                        started_at=started,
                        latency_ms=latency_ms,
                        query_start=getattr(request.state, "query_start", None),
                        query_end=getattr(request.state, "query_end", None),
                        requested_resolution=getattr(request.state, "requested_resolution", None),
                        rows_returned=getattr(request.state, "rows_returned", None),
                        error_code=getattr(request.state, "error_code", None),
                    )
                except Exception:
                    logger.exception("audit_middleware_failed")


def create_app() -> FastAPI:
    settings = get_settings()
    error_responses = {
        400: {"model": ErrorResponse, "description": "Invalid request / range / validation"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden for this API key role"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    }
    application = FastAPI(
        title="Health Data Platform",
        description=(
            "Phase 1 — ingest and retrieve personal health data for later agent consumption.\n\n"
            "## Semantics\n"
            "- All timestamps are UTC.\n"
            "- Range queries use half-open `[start, end)` windows.\n"
            "- Maximum query range: 365 days.\n"
            "- Default row cap: 5000; hard cap: 20000 (never silently truncated).\n"
            "- Weight is stored and returned in kilograms.\n"
            "- Glucose is stored and returned in mg/dL.\n"
        ),
        version=__version__,
        lifespan=lifespan,
        responses=error_responses,
    )

    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(AuditMiddleware)
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(AppError, app_error_handler)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        request.state.error_code = "INVALID_REQUEST"
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed.",
                    "hint": "Check required fields, types, and enums.",
                    "details": {"errors": exc.errors()},
                },
                "request_id": str(request_id),
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        request.state.error_code = "INTERNAL_ERROR"
        logger.exception("unhandled_error request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                },
                "request_id": str(request_id),
            },
        )

    application.include_router(health.router)
    application.include_router(ingest.router)
    application.include_router(series.router)
    application.include_router(events.router)

    @application.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "health-db",
            "version": __version__,
            "docs": "/docs",
            "phase": 1,
        }

    return application


app = create_app()
