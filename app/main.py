"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import events, ingest, plan, series, summary
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import RequestLoggingMiddleware, configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Health Data Platform",
        description="Phase 1 — typed, bounded, agent-first health data API",
        version=__version__,
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Wildcard origins cannot be combined with credentials (Fetch spec).
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestLoggingMiddleware)
    application.add_exception_handler(AppError, app_error_handler)

    application.include_router(ingest.router)
    application.include_router(series.router)
    application.include_router(summary.router)
    application.include_router(events.router)
    application.include_router(plan.router)

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__, "environment": settings.environment}

    @application.get("/")
    async def root() -> dict:
        return {
            "service": "health-db",
            "version": __version__,
            "docs": "/docs",
            "phase": 1,
        }

    return application


app = create_app()
