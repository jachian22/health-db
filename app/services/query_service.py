"""Bounded typed read queries over stored health data."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import RESOLUTION_SECONDS
from app.core.errors import AppError
from app.db.models import GlucoseSample, MealEvent, SleepInterval, User, WeightMeasurement, Workout
from app.schemas.queries import (
    GlucoseSeriesQuery,
    MealsQuery,
    RangeQuery,
    RunsSeriesQuery,
    SleepSeriesQuery,
    WeightSeriesQuery,
)
from app.schemas.responses import (
    GlucoseBucketPoint,
    GlucoseRawPoint,
    MealPoint,
    QueryMeta,
    QueryResponse,
    RunPoint,
    SleepPoint,
    WeightPoint,
)


async def resolve_user(session: AsyncSession, external_id: str) -> User:
    result = await session.execute(
        select(User).where(User.external_identifier == external_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            code="INTERNAL_ERROR",
            message=f"Primary user '{external_id}' is not seeded.",
            status_code=500,
        )
    return user


def _duration_seconds(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds())


def _dec(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


async def _fetch_with_cap(
    session: AsyncSession,
    stmt: Select[Any],
    limit: int,
) -> list[Any]:
    capped = await session.execute(stmt.limit(limit + 1))
    rows = list(capped.scalars().all())
    if len(rows) > limit:
        raise AppError(
            code="TOO_MANY_ROWS",
            message=f"Query matched more than {limit} rows.",
            hint=(
                "Narrow the time range, lower the limit, or for glucose use an "
                "aggregated resolution (5m, 15m, 1h, 1d)."
            ),
            status_code=400,
        )
    return rows


def _empty_response(query: RangeQuery, warnings: list[str] | None = None) -> QueryResponse:
    return QueryResponse(
        data=[],
        meta=QueryMeta(
            requested_start=query.start,
            requested_end=query.end,
            actual_first_record_at=None,
            actual_last_record_at=None,
            row_count=0,
            timezone="UTC",
        ),
        warnings=warnings or [],
        next_cursor=None,
    )


def _response(
    query: RangeQuery,
    data: list[Any],
    first_at: datetime | None,
    last_at: datetime | None,
    warnings: list[str] | None = None,
) -> QueryResponse:
    return QueryResponse(
        data=data,
        meta=QueryMeta(
            requested_start=query.start,
            requested_end=query.end,
            actual_first_record_at=first_at,
            actual_last_record_at=last_at,
            row_count=len(data),
            timezone="UTC",
        ),
        warnings=warnings or [],
        next_cursor=None,
    )


async def query_glucose(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: GlucoseSeriesQuery,
) -> QueryResponse:
    query.enforce_bounds()
    limit = query.effective_limit()

    if query.resolution == "raw":
        stmt = (
            select(GlucoseSample)
            .where(
                GlucoseSample.user_id == user_id,
                GlucoseSample.deleted_at.is_(None),
                GlucoseSample.sample_time >= query.start,
                GlucoseSample.sample_time < query.end,
            )
            .order_by(GlucoseSample.sample_time.asc())
        )
        rows = await _fetch_with_cap(session, stmt, limit)
        if not rows:
            return _empty_response(query)
        data = [
            GlucoseRawPoint(
                timestamp=row.sample_time,
                value_mg_dl=_dec(row.value_mg_dl) or 0,
                source_name=row.source_name,
                source_sample_id=row.source_sample_id,
            )
            for row in rows
        ]
        return _response(query, data, rows[0].sample_time, rows[-1].sample_time)

    bucket_seconds = RESOLUTION_SECONDS[query.resolution]
    bucket_start = func.to_timestamp(
        func.floor(func.extract("epoch", GlucoseSample.sample_time) / bucket_seconds)
        * bucket_seconds
    )
    stmt = (
        select(
            bucket_start.label("bucket_start"),
            func.count().label("count"),
            func.min(GlucoseSample.value_mg_dl).label("min_mg_dl"),
            func.max(GlucoseSample.value_mg_dl).label("max_mg_dl"),
            func.avg(GlucoseSample.value_mg_dl).label("avg_mg_dl"),
        )
        .where(
            GlucoseSample.user_id == user_id,
            GlucoseSample.deleted_at.is_(None),
            GlucoseSample.sample_time >= query.start,
            GlucoseSample.sample_time < query.end,
        )
        .group_by("bucket_start")
        .order_by("bucket_start")
    )
    result = await session.execute(stmt.limit(limit + 1))
    rows = list(result.all())
    if len(rows) > limit:
        raise AppError(
            code="TOO_MANY_ROWS",
            message=f"Query matched more than {limit} aggregated buckets.",
            hint="Narrow the time range or choose a coarser resolution.",
            status_code=400,
        )
    if not rows:
        return _empty_response(query)

    data = []
    for row in rows:
        start = row.bucket_start
        if start.tzinfo is None:
            from datetime import UTC

            start = start.replace(tzinfo=UTC)
        end = start + timedelta(seconds=bucket_seconds)
        data.append(
            GlucoseBucketPoint(
                bucket_start=start,
                bucket_end=end,
                count=int(row.count),
                min_mg_dl=_dec(row.min_mg_dl) or 0,
                max_mg_dl=_dec(row.max_mg_dl) or 0,
                avg_mg_dl=round(float(row.avg_mg_dl), 2) if row.avg_mg_dl is not None else 0,
            )
        )
    return _response(query, data, data[0].bucket_start, data[-1].bucket_start)


async def query_runs(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: RunsSeriesQuery,
) -> QueryResponse:
    query.enforce_bounds()
    limit = query.effective_limit()
    stmt = (
        select(Workout)
        .where(
            Workout.user_id == user_id,
            Workout.deleted_at.is_(None),
            Workout.start_time < query.end,
            Workout.end_time > query.start,
        )
        .order_by(Workout.start_time.asc())
    )
    rows = await _fetch_with_cap(session, stmt, limit)
    if not rows:
        return _empty_response(query)
    data = [
        RunPoint(
            source_name=row.source_name,
            source_sample_id=row.source_sample_id,
            sport=row.sport,
            start_time=row.start_time,
            end_time=row.end_time,
            duration_seconds=_duration_seconds(row.start_time, row.end_time),
            distance_meters=_dec(row.distance_meters),
            active_energy_kcal=_dec(row.active_energy_kcal),
            average_heart_rate=_dec(row.average_heart_rate),
            maximum_heart_rate=_dec(row.maximum_heart_rate),
        )
        for row in rows
    ]
    return _response(query, data, rows[0].start_time, rows[-1].start_time)


async def query_sleep(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: SleepSeriesQuery,
) -> QueryResponse:
    query.enforce_bounds()
    limit = query.effective_limit()
    conditions = [
        SleepInterval.user_id == user_id,
        SleepInterval.deleted_at.is_(None),
        SleepInterval.start_time < query.end,
        SleepInterval.end_time > query.start,
    ]
    if query.stages:
        conditions.append(SleepInterval.stage.in_([s.lower() for s in query.stages]))

    stmt = select(SleepInterval).where(*conditions).order_by(SleepInterval.start_time.asc())
    rows = await _fetch_with_cap(session, stmt, limit)
    if not rows:
        return _empty_response(query)
    data = [
        SleepPoint(
            source_name=row.source_name,
            source_sample_id=row.source_sample_id,
            start_time=row.start_time,
            end_time=row.end_time,
            duration_seconds=_duration_seconds(row.start_time, row.end_time),
            stage=row.stage,
        )
        for row in rows
    ]
    return _response(query, data, rows[0].start_time, rows[-1].start_time)


async def query_weight(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: WeightSeriesQuery,
) -> QueryResponse:
    query.enforce_bounds()
    limit = query.effective_limit()
    stmt = (
        select(WeightMeasurement)
        .where(
            WeightMeasurement.user_id == user_id,
            WeightMeasurement.deleted_at.is_(None),
            WeightMeasurement.measured_at >= query.start,
            WeightMeasurement.measured_at < query.end,
        )
        .order_by(WeightMeasurement.measured_at.asc())
    )
    rows = await _fetch_with_cap(session, stmt, limit)
    if not rows:
        return _empty_response(query)
    data = [
        WeightPoint(
            timestamp=row.measured_at,
            value_kg=_dec(row.value_kg) or 0,
            source_name=row.source_name,
            source_sample_id=row.source_sample_id,
        )
        for row in rows
    ]
    return _response(query, data, rows[0].measured_at, rows[-1].measured_at)


async def query_meals(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: MealsQuery,
) -> QueryResponse:
    query.enforce_bounds()
    limit = query.effective_limit()
    stmt = (
        select(MealEvent)
        .where(
            MealEvent.user_id == user_id,
            MealEvent.deleted_at.is_(None),
            MealEvent.meal_completed_at >= query.start,
            MealEvent.meal_completed_at < query.end,
        )
        .order_by(MealEvent.meal_completed_at.asc())
    )
    rows = await _fetch_with_cap(session, stmt, limit)
    if not rows:
        return _empty_response(query)
    data = [
        MealPoint(
            source=row.source,
            source_sample_id=row.source_sample_id,
            meal_completed_at=row.meal_completed_at,
            foods=list(row.foods or []),
            notes=row.notes,
        )
        for row in rows
    ]
    return _response(query, data, rows[0].meal_completed_at, rows[-1].meal_completed_at)
