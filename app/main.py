"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    logger.info("application_startup version=%s", __version__)
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Health Data Platform",
        description=(
            "Personal health data platform — ingest (iOS) and Query API v1 "
            "(authenticated read-only contract for future MCP/agent access).\n\n"
            "## Semantics\n"
            "- All source timestamps are stored and returned in UTC.\n"
            "- Query API ranges are half-open `[start, end)` and require timezone-aware "
            "ISO-8601 `start` / `end` parameters.\n"
            "- Default display/aggregation timezone: `America/New_York`.\n"
            "- All query endpoints are read-only and require explicit bounded time ranges.\n"
            "- Glucose units: mg/dL. Weight units: kg.\n"
            "- Query API uses `READ_API_KEY`; ingest uses `INGEST_API_KEY` "
            "(keys are not interchangeable).\n"
        ),
        version=__version__,
        lifespan=lifespan,
    )

    # Operational probes first so Railway healthchecks and readiness can work
    # even when the rest of the stack is not configured yet.
    # /health never touches the database; /ready does a minimal SELECT 1.
    from app.api.v1.health import router as health_router

    application.include_router(health_router)

    @application.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "health-db",
            "version": __version__,
            "docs": "/docs",
            "phase": 1,
        }

    try:
        _mount_phase1(application)
    except Exception:
        # Never prevent process boot /health because full wiring failed.
        logger.exception("phase1_wiring_failed")

    return application


def _mount_phase1(application: FastAPI) -> None:
    """Attach auth, ingest, query, and middleware. May raise if misconfigured."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    from app.api.errors import AppError, app_error_handler
    from app.api.v1 import ingest, query
    from app.core.config import get_settings
    from app.core.request_id import RequestIdMiddleware

    settings = get_settings()

    def custom_openapi():
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components["ErrorBody"] = {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "hint": {"type": "string", "nullable": True},
                "details": {"type": "object", "nullable": True},
            },
            "required": ["code", "message"],
        }
        components["ErrorResponse"] = {
            "type": "object",
            "properties": {
                "error": {"$ref": "#/components/schemas/ErrorBody"},
                "request_id": {"type": "string"},
            },
            "required": ["error", "request_id"],
        }
        application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = custom_openapi

    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
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

    application.include_router(ingest.router)
    application.include_router(query.router)
    logger.info("phase1_wiring_complete")


app = create_app()
