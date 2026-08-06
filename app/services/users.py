"""Shared service helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import User


async def resolve_user(db: AsyncSession, external_id: str | None) -> User:
    """Resolve user by external identifier. Defaults to the sole user if only one exists."""
    if external_id:
        result = await db.execute(select(User).where(User.external_identifier == external_id))
        user = result.scalar_one_or_none()
        if not user:
            raise AppError("NO_DATA", f"User '{external_id}' not found", status_code=404)
        return user

    result = await db.execute(select(User).order_by(User.id).limit(2))
    users = list(result.scalars().all())
    if not users:
        raise AppError("NO_DATA", "No users found — ingest data first", status_code=404)
    if len(users) > 1:
        raise AppError(
            "MISSING_REQUIRED_FIELD",
            "Multiple users exist; provide user_id",
            hint="Pass user_id in the request body",
        )
    return users[0]
