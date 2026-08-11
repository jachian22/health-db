"""Operational probes — unauthenticated, no API-key auth."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db.session import check_database_ready

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    description="Unauthenticated liveness probe. Dependency-free; returns immediately.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Database readiness",
    description=(
        "Unauthenticated readiness probe. Runs `SELECT 1` against Postgres. "
        "Returns 200 when connected; 503 when the database URL is missing or unreachable."
    ),
)
async def ready() -> dict[str, str]:
    if not await check_database_ready():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Database is unavailable",
            },
        )
    return {"status": "ready", "database": "connected"}
