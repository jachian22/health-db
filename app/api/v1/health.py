"""Unauthenticated liveness probe — no DB, auth, or external calls."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    description="Unauthenticated liveness probe. Dependency-free; returns immediately.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}
