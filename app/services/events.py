"""Event lookup services."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bounds import clamp_limit, validate_range
from app.models import GlucoseSample, MealEvent, Workout
from app.schemas.events import GlucoseEventOut, MealEventOut, RunEventOut
from app.services.users import resolve_user


async def events_meals(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    limit: int | None = None,
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[MealEventOut], dict[str, Any]]:
    start, end = validate_range(start, end)
    lim = clamp_limit(limit)
    user = await resolve_user(db, user_id)

    stmt = select(MealEvent).where(
        MealEvent.user_id == user.id,
        MealEvent.meal_start >= start,
        MealEvent.meal_start < end,
    )
    if not include_deleted:
        stmt = stmt.where(MealEvent.deleted_at.is_(None))
    stmt = stmt.order_by(MealEvent.meal_start.desc()).limit(lim)

    rows = list((await db.execute(stmt)).scalars().all())
    data = [MealEventOut.model_validate(r) for r in rows]
    return data, {
        "count": len(data),
        "start": start,
        "end": end,
        "bounded": True,
        "limit": lim,
        "user_id": user.external_identifier,
    }


async def events_runs(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    limit: int | None = None,
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[RunEventOut], dict[str, Any]]:
    start, end = validate_range(start, end)
    lim = clamp_limit(limit)
    user = await resolve_user(db, user_id)

    stmt = select(Workout).where(
        Workout.user_id == user.id,
        Workout.start_time >= start,
        Workout.start_time < end,
    )
    if not include_deleted:
        stmt = stmt.where(Workout.deleted_at.is_(None))
    stmt = stmt.order_by(Workout.start_time.desc()).limit(lim)

    rows = list((await db.execute(stmt)).scalars().all())
    data = [RunEventOut.model_validate(r) for r in rows]
    return data, {
        "count": len(data),
        "start": start,
        "end": end,
        "bounded": True,
        "limit": lim,
        "user_id": user.external_identifier,
    }


async def events_glucose(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    limit: int | None = None,
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[GlucoseEventOut], dict[str, Any]]:
    start, end = validate_range(start, end)
    lim = clamp_limit(limit)
    user = await resolve_user(db, user_id)

    stmt = select(GlucoseSample).where(
        GlucoseSample.user_id == user.id,
        GlucoseSample.sample_time >= start,
        GlucoseSample.sample_time < end,
    )
    if not include_deleted:
        stmt = stmt.where(GlucoseSample.deleted_at.is_(None))
    stmt = stmt.order_by(GlucoseSample.sample_time.desc()).limit(lim)

    rows = list((await db.execute(stmt)).scalars().all())
    data = [GlucoseEventOut.model_validate(r) for r in rows]
    return data, {
        "count": len(data),
        "start": start,
        "end": end,
        "bounded": True,
        "limit": lim,
        "user_id": user.external_identifier,
    }
