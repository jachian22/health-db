"""Summary / aggregate retrieval services — aggregation pushed into SQL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bounds import validate_range
from app.core.errors import ConflictingFiltersError
from app.core.sql import date_trunc_day, date_trunc_week, dialect_name
from app.models import GlucoseSample, MealEvent, SleepSession, WeightMeasurement, Workout
from app.schemas.summary import (
    DailyBucket,
    GlucoseSummary,
    RunsSummary,
    SleepSummary,
    WeeklyBucket,
)
from app.services.users import resolve_user


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


async def summary_daily(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    user_id: str | None = None,
) -> tuple[list[DailyBucket], dict[str, Any]]:
    start, end = validate_range(start, end)
    user = await resolve_user(db, user_id)
    dialect = dialect_name(db)
    buckets: dict[date, DailyBucket] = {}

    def bucket(d: date) -> DailyBucket:
        if d not in buckets:
            buckets[d] = DailyBucket(day=d)
        return buckets[d]

    day_g = date_trunc_day(GlucoseSample.sample_time, dialect)
    glucose_rows = (
        await db.execute(
            select(
                day_g.label("day"),
                func.count().label("cnt"),
                func.avg(GlucoseSample.value).label("avg"),
                func.min(GlucoseSample.value).label("min_v"),
                func.max(GlucoseSample.value).label("max_v"),
            )
            .where(
                GlucoseSample.user_id == user.id,
                GlucoseSample.sample_time >= start,
                GlucoseSample.sample_time < end,
                GlucoseSample.deleted_at.is_(None),
            )
            .group_by(day_g)
        )
    ).all()
    for row in glucose_rows:
        b = bucket(_as_date(row.day))
        b.glucose_count = int(row.cnt)
        b.glucose_avg = round(float(row.avg), 2) if row.avg is not None else None
        b.glucose_min = float(row.min_v) if row.min_v is not None else None
        b.glucose_max = float(row.max_v) if row.max_v is not None else None

    day_w = date_trunc_day(Workout.start_time, dialect)
    workout_rows = (
        await db.execute(
            select(
                day_w.label("day"),
                func.count().label("cnt"),
                func.sum(Workout.distance_m).label("dist"),
            )
            .where(
                Workout.user_id == user.id,
                Workout.start_time >= start,
                Workout.start_time < end,
                Workout.deleted_at.is_(None),
            )
            .group_by(day_w)
        )
    ).all()
    for row in workout_rows:
        b = bucket(_as_date(row.day))
        b.run_count = int(row.cnt)
        b.run_distance_m = float(row.dist) if row.dist is not None else None

    day_s = date_trunc_day(SleepSession.start_time, dialect)
    sleep_rows = (
        await db.execute(
            select(
                day_s.label("day"),
                func.count().label("cnt"),
                func.sum(SleepSession.duration_s).label("dur"),
            )
            .where(
                SleepSession.user_id == user.id,
                SleepSession.start_time >= start,
                SleepSession.start_time < end,
                SleepSession.deleted_at.is_(None),
            )
            .group_by(day_s)
        )
    ).all()
    for row in sleep_rows:
        b = bucket(_as_date(row.day))
        b.sleep_count = int(row.cnt)
        b.sleep_duration_s = int(row.dur) if row.dur is not None else None

    meal_anchor = func.coalesce(
        MealEvent.meal_completed_at, MealEvent.meal_end, MealEvent.meal_start
    )
    day_m = date_trunc_day(meal_anchor, dialect)
    meal_rows = (
        await db.execute(
            select(day_m.label("day"), func.count().label("cnt"))
            .where(
                MealEvent.user_id == user.id,
                MealEvent.meal_start >= start,
                MealEvent.meal_start < end,
                MealEvent.deleted_at.is_(None),
            )
            .group_by(day_m)
        )
    ).all()
    for row in meal_rows:
        bucket(_as_date(row.day)).meal_count = int(row.cnt)

    day_wt = date_trunc_day(WeightMeasurement.measured_at, dialect)
    weight_rows = (
        await db.execute(
            select(
                day_wt.label("day"),
                func.avg(WeightMeasurement.value).label("avg"),
                func.min(WeightMeasurement.unit).label("unit"),
            )
            .where(
                WeightMeasurement.user_id == user.id,
                WeightMeasurement.measured_at >= start,
                WeightMeasurement.measured_at < end,
                WeightMeasurement.deleted_at.is_(None),
            )
            .group_by(day_wt)
        )
    ).all()
    for row in weight_rows:
        b = bucket(_as_date(row.day))
        b.weight_avg = round(float(row.avg), 2) if row.avg is not None else None
        b.weight_unit = row.unit

    data = [buckets[d] for d in sorted(buckets)]
    return data, {
        "count": len(data),
        "start": start,
        "end": end,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def summary_weekly(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    user_id: str | None = None,
) -> tuple[list[WeeklyBucket], dict[str, Any]]:
    """Aggregate raw rows by week (sample-weighted), not re-aggregate daily buckets."""
    start, end = validate_range(start, end)
    user = await resolve_user(db, user_id)
    dialect = dialect_name(db)
    weeks: dict[date, WeeklyBucket] = {}

    def week_bucket(d: date) -> WeeklyBucket:
        if d not in weeks:
            weeks[d] = WeeklyBucket(week_start=d)
        return weeks[d]

    week_g = date_trunc_week(GlucoseSample.sample_time, dialect)
    for row in (
        await db.execute(
            select(
                week_g.label("week_start"),
                func.avg(GlucoseSample.value).label("avg"),
            )
            .where(
                GlucoseSample.user_id == user.id,
                GlucoseSample.sample_time >= start,
                GlucoseSample.sample_time < end,
                GlucoseSample.deleted_at.is_(None),
            )
            .group_by(week_g)
        )
    ).all():
        week_bucket(_as_date(row.week_start)).glucose_avg = (
            round(float(row.avg), 2) if row.avg is not None else None
        )

    week_w = date_trunc_week(Workout.start_time, dialect)
    for row in (
        await db.execute(
            select(
                week_w.label("week_start"),
                func.count().label("cnt"),
                func.sum(Workout.distance_m).label("dist"),
            )
            .where(
                Workout.user_id == user.id,
                Workout.start_time >= start,
                Workout.start_time < end,
                Workout.deleted_at.is_(None),
            )
            .group_by(week_w)
        )
    ).all():
        b = week_bucket(_as_date(row.week_start))
        b.run_count = int(row.cnt)
        b.run_distance_m = float(row.dist) if row.dist is not None else None

    week_s = date_trunc_week(SleepSession.start_time, dialect)
    for row in (
        await db.execute(
            select(
                week_s.label("week_start"),
                func.avg(SleepSession.duration_s).label("avg_dur"),
            )
            .where(
                SleepSession.user_id == user.id,
                SleepSession.start_time >= start,
                SleepSession.start_time < end,
                SleepSession.deleted_at.is_(None),
            )
            .group_by(week_s)
        )
    ).all():
        week_bucket(_as_date(row.week_start)).sleep_duration_s_avg = (
            round(float(row.avg_dur), 1) if row.avg_dur is not None else None
        )

    meal_anchor = func.coalesce(
        MealEvent.meal_completed_at, MealEvent.meal_end, MealEvent.meal_start
    )
    week_m = date_trunc_week(meal_anchor, dialect)
    for row in (
        await db.execute(
            select(week_m.label("week_start"), func.count().label("cnt"))
            .where(
                MealEvent.user_id == user.id,
                MealEvent.meal_start >= start,
                MealEvent.meal_start < end,
                MealEvent.deleted_at.is_(None),
            )
            .group_by(week_m)
        )
    ).all():
        week_bucket(_as_date(row.week_start)).meal_count = int(row.cnt)

    data = [weeks[d] for d in sorted(weeks)]
    return data, {
        "count": len(data),
        "start": start,
        "end": end,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def summary_glucose(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    group_by: str = "day",
    user_id: str | None = None,
) -> tuple[GlucoseSummary, dict[str, Any]]:
    if group_by not in ("day", "week", "none"):
        raise ConflictingFiltersError(f"Unsupported group_by '{group_by}'")
    start, end = validate_range(start, end)
    user = await resolve_user(db, user_id)
    dialect = dialect_name(db)

    base_where = (
        GlucoseSample.user_id == user.id,
        GlucoseSample.sample_time >= start,
        GlucoseSample.sample_time < end,
        GlucoseSample.deleted_at.is_(None),
    )

    overall = (
        await db.execute(
            select(
                func.count().label("cnt"),
                func.avg(GlucoseSample.value).label("avg"),
                func.min(GlucoseSample.value).label("min_v"),
                func.max(GlucoseSample.value).label("max_v"),
                func.min(GlucoseSample.unit).label("unit"),
            ).where(*base_where)
        )
    ).one()

    by_day: list[dict[str, Any]] = []
    if group_by == "day":
        day_col = date_trunc_day(GlucoseSample.sample_time, dialect)
        grouped = (
            await db.execute(
                select(
                    day_col.label("day"),
                    func.avg(GlucoseSample.value).label("avg"),
                    func.min(GlucoseSample.value).label("min_v"),
                    func.max(GlucoseSample.value).label("max_v"),
                    func.count().label("cnt"),
                )
                .where(*base_where)
                .group_by(day_col)
                .order_by(day_col)
            )
        ).all()
        by_day = [
            {
                "day": _as_date(r.day).isoformat(),
                "avg": round(float(r.avg), 2),
                "min": float(r.min_v),
                "max": float(r.max_v),
                "count": int(r.cnt),
            }
            for r in grouped
        ]
    elif group_by == "week":
        week_col = date_trunc_week(GlucoseSample.sample_time, dialect)
        grouped = (
            await db.execute(
                select(
                    week_col.label("week_start"),
                    func.avg(GlucoseSample.value).label("avg"),
                    func.min(GlucoseSample.value).label("min_v"),
                    func.max(GlucoseSample.value).label("max_v"),
                    func.count().label("cnt"),
                )
                .where(*base_where)
                .group_by(week_col)
                .order_by(week_col)
            )
        ).all()
        by_day = [
            {
                "week_start": _as_date(r.week_start).isoformat(),
                "avg": round(float(r.avg), 2),
                "min": float(r.min_v),
                "max": float(r.max_v),
                "count": int(r.cnt),
            }
            for r in grouped
        ]

    count = int(overall.cnt or 0)
    summary = GlucoseSummary(
        start=start,
        end=end,
        count=count,
        avg=round(float(overall.avg), 2) if overall.avg is not None else None,
        min=float(overall.min_v) if overall.min_v is not None else None,
        max=float(overall.max_v) if overall.max_v is not None else None,
        unit=overall.unit,
        by_day=by_day,
    )
    return summary, {
        "count": summary.count,
        "start": start,
        "end": end,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def summary_runs(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    user_id: str | None = None,
) -> tuple[RunsSummary, dict[str, Any]]:
    start, end = validate_range(start, end)
    user = await resolve_user(db, user_id)
    dialect = dialect_name(db)

    base_where = (
        Workout.user_id == user.id,
        Workout.start_time >= start,
        Workout.start_time < end,
        Workout.deleted_at.is_(None),
    )

    overall = (
        await db.execute(
            select(
                func.count().label("cnt"),
                func.sum(Workout.distance_m).label("dist"),
                func.sum(Workout.active_energy_kcal).label("kcal"),
            ).where(*base_where)
        )
    ).one()

    sport_rows = (
        await db.execute(
            select(Workout.sport, func.count().label("cnt"))
            .where(*base_where)
            .group_by(Workout.sport)
        )
    ).all()
    by_sport = {r.sport: int(r.cnt) for r in sport_rows}

    day_col = date_trunc_day(Workout.start_time, dialect)
    day_rows = (
        await db.execute(
            select(day_col.label("day"), func.count().label("cnt"))
            .where(*base_where)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    summary = RunsSummary(
        start=start,
        end=end,
        count=int(overall.cnt or 0),
        total_distance_m=float(overall.dist) if overall.dist is not None else None,
        total_active_energy_kcal=float(overall.kcal) if overall.kcal is not None else None,
        by_sport=by_sport,
        by_day=[{"day": _as_date(r.day).isoformat(), "count": int(r.cnt)} for r in day_rows],
    )
    return summary, {
        "count": summary.count,
        "start": start,
        "end": end,
        "bounded": True,
        "user_id": user.external_identifier,
    }


async def summary_sleep(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    user_id: str | None = None,
) -> tuple[SleepSummary, dict[str, Any]]:
    start, end = validate_range(start, end)
    user = await resolve_user(db, user_id)
    dialect = dialect_name(db)

    base_where = (
        SleepSession.user_id == user.id,
        SleepSession.start_time >= start,
        SleepSession.start_time < end,
        SleepSession.deleted_at.is_(None),
    )

    overall = (
        await db.execute(
            select(
                func.count().label("cnt"),
                func.sum(SleepSession.duration_s).label("total"),
                func.avg(SleepSession.duration_s).label("avg"),
            ).where(*base_where)
        )
    ).one()

    day_col = date_trunc_day(SleepSession.start_time, dialect)
    day_rows = (
        await db.execute(
            select(day_col.label("day"), func.sum(SleepSession.duration_s).label("dur"))
            .where(*base_where)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    count = int(overall.cnt or 0)
    total = int(overall.total or 0)
    summary = SleepSummary(
        start=start,
        end=end,
        count=count,
        total_duration_s=total,
        avg_duration_s=round(float(overall.avg), 1) if overall.avg is not None else None,
        by_day=[{"day": _as_date(r.day).isoformat(), "duration_s": int(r.dur)} for r in day_rows],
    )
    return summary, {
        "count": summary.count,
        "start": start,
        "end": end,
        "bounded": True,
        "user_id": user.external_identifier,
    }
