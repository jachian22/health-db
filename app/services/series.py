"""Series retrieval services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bounds import (
    RESOLUTION_SECONDS,
    enforce_row_limit,
    validate_range,
    validate_resolution,
)
from app.core.config import get_settings
from app.core.sql import dialect_name, time_bucket_start
from app.models import GlucoseSample, MealEvent, SleepSession, WeightMeasurement, Workout
from app.schemas.series import GlucosePoint, MealPoint, RunPoint, SleepPoint, WeightPoint
from app.services.users import resolve_user


async def series_glucose(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    resolution: str | None = "raw",
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[GlucosePoint], dict[str, Any]]:
    start, end = validate_range(start, end)
    res = validate_resolution(resolution)
    user = await resolve_user(db, user_id)
    settings = get_settings()
    max_rows = settings.max_rows_per_response
    seconds = RESOLUTION_SECONDS.get(res)

    base_filters = [
        GlucoseSample.user_id == user.id,
        GlucoseSample.sample_time >= start,
        GlucoseSample.sample_time < end,
    ]
    if not include_deleted:
        base_filters.append(GlucoseSample.deleted_at.is_(None))

    if seconds is None:
        stmt = (
            select(GlucoseSample)
            .where(*base_filters)
            .order_by(GlucoseSample.sample_time.asc())
            .limit(max_rows + 1)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        enforce_row_limit(len(rows), settings=settings)
        points = [
            GlucosePoint(
                t=r.sample_time,
                v=r.value,
                unit=r.unit,
                trend=r.trend,
                source_sample_id=r.source_sample_id,
            )
            for r in rows
        ]
    else:
        dialect = dialect_name(db)
        bucket = time_bucket_start(GlucoseSample.sample_time, seconds, dialect)
        stmt = (
            select(
                bucket.label("t"),
                func.avg(GlucoseSample.value).label("avg_v"),
                func.min(GlucoseSample.unit).label("unit"),
                func.count().label("cnt"),
            )
            .where(*base_filters)
            .group_by(bucket)
            .order_by(bucket.asc())
            .limit(max_rows + 1)
        )
        rows = list((await db.execute(stmt)).all())
        enforce_row_limit(len(rows), settings=settings)
        points = []
        for r in rows:
            t = r.t
            if isinstance(t, str):
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
            elif isinstance(t, datetime) and t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            points.append(
                GlucosePoint(
                    t=t,
                    v=round(float(r.avg_v), 2),
                    unit=r.unit,
                    trend=None,
                    source_sample_id=None,
                )
            )

    meta = {
        "count": len(points),
        "start": start,
        "end": end,
        "resolution": res,
        "bounded": True,
        "user_id": user.external_identifier,
    }
    return points, meta


async def series_runs(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    resolution: str | None = "raw",
    user_id: str | None = None,
    include_deleted: bool = False,
    sport_filter: str | None = None,
) -> tuple[list[RunPoint], dict[str, Any]]:
    start, end = validate_range(start, end)
    res = validate_resolution(resolution)
    user = await resolve_user(db, user_id)
    settings = get_settings()
    max_rows = settings.max_rows_per_response

    stmt = select(Workout).where(
        Workout.user_id == user.id,
        Workout.start_time >= start,
        Workout.start_time < end,
    )
    if not include_deleted:
        stmt = stmt.where(Workout.deleted_at.is_(None))
    if sport_filter:
        stmt = stmt.where(func.lower(Workout.sport).like(f"%{sport_filter.lower()}%"))
    stmt = stmt.order_by(Workout.start_time.asc()).limit(max_rows + 1)

    rows = list((await db.execute(stmt)).scalars().all())
    enforce_row_limit(len(rows), settings=settings)
    points = [
        RunPoint(
            start=r.start_time,
            end=r.end_time,
            sport=r.sport,
            distance_m=r.distance_m,
            active_energy_kcal=r.active_energy_kcal,
            avg_hr=r.avg_hr,
            max_hr=r.max_hr,
            source_sample_id=r.source_sample_id,
        )
        for r in rows
    ]
    return points, {
        "count": len(points),
        "start": start,
        "end": end,
        "resolution": res,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def series_sleep(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    resolution: str | None = "raw",
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[SleepPoint], dict[str, Any]]:
    start, end = validate_range(start, end)
    res = validate_resolution(resolution)
    user = await resolve_user(db, user_id)
    settings = get_settings()
    max_rows = settings.max_rows_per_response

    stmt = select(SleepSession).where(
        SleepSession.user_id == user.id,
        SleepSession.start_time >= start,
        SleepSession.start_time < end,
    )
    if not include_deleted:
        stmt = stmt.where(SleepSession.deleted_at.is_(None))
    stmt = stmt.order_by(SleepSession.start_time.asc()).limit(max_rows + 1)

    rows = list((await db.execute(stmt)).scalars().all())
    enforce_row_limit(len(rows), settings=settings)
    points = [
        SleepPoint(
            start=r.start_time,
            end=r.end_time,
            duration_s=r.duration_s,
            sleep_stage_summary=r.sleep_stage_summary,
            source_sample_id=r.source_sample_id,
        )
        for r in rows
    ]
    return points, {
        "count": len(points),
        "start": start,
        "end": end,
        "resolution": res,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def series_weight(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    resolution: str | None = "raw",
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[WeightPoint], dict[str, Any]]:
    start, end = validate_range(start, end)
    res = validate_resolution(resolution)
    user = await resolve_user(db, user_id)
    settings = get_settings()
    max_rows = settings.max_rows_per_response

    stmt = select(WeightMeasurement).where(
        WeightMeasurement.user_id == user.id,
        WeightMeasurement.measured_at >= start,
        WeightMeasurement.measured_at < end,
    )
    if not include_deleted:
        stmt = stmt.where(WeightMeasurement.deleted_at.is_(None))
    stmt = stmt.order_by(WeightMeasurement.measured_at.asc()).limit(max_rows + 1)

    rows = list((await db.execute(stmt)).scalars().all())
    enforce_row_limit(len(rows), settings=settings)
    points = [
        WeightPoint(t=r.measured_at, v=r.value, unit=r.unit, source_sample_id=r.source_sample_id)
        for r in rows
    ]
    return points, {
        "count": len(points),
        "start": start,
        "end": end,
        "resolution": res,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def series_meals(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    resolution: str | None = "raw",
    user_id: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[MealPoint], dict[str, Any]]:
    start, end = validate_range(start, end)
    res = validate_resolution(resolution)
    user = await resolve_user(db, user_id)
    settings = get_settings()
    max_rows = settings.max_rows_per_response

    stmt = select(MealEvent).where(
        MealEvent.user_id == user.id,
        MealEvent.meal_start >= start,
        MealEvent.meal_start < end,
    )
    if not include_deleted:
        stmt = stmt.where(MealEvent.deleted_at.is_(None))
    stmt = stmt.order_by(MealEvent.meal_start.asc()).limit(max_rows + 1)

    rows = list((await db.execute(stmt)).scalars().all())
    enforce_row_limit(len(rows), settings=settings)
    points = [
        MealPoint(
            meal_start=r.meal_start,
            meal_end=r.meal_end,
            meal_completed_at=r.meal_completed_at,
            notes=r.notes,
            foods=r.foods,
            source_sample_id=r.source_sample_id,
            anchor=r.meal_completed_at or r.meal_end,
        )
        for r in rows
    ]
    return points, {
        "count": len(points),
        "start": start,
        "end": end,
        "resolution": res,
        "bounded": True,
        "user_id": user.external_identifier,
        "anchor_field": "meal_completed_at|meal_end",
    }
