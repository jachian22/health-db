"""Unauthenticated health check."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.db.session import get_session_factory

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    description="Unauthenticated liveness/readiness probe. Checks database connectivity.",
)
async def health() -> dict:
    settings = get_settings()
    db_ok = False
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "version": __version__,
        "environment": settings.environment,
        "database": "up" if db_ok else "down",
    }
