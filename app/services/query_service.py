"""HealthDataQueryService — bounded typed read queries over stored health data.

Independent of MCP / agent transport. Callers authenticate separately and resolve
the fixed personal principal via settings.primary_user_external_id.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, literal, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import MAX_GLUCOSE_POINTS, RESOLUTION_SECONDS
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import GlucoseSample, MealEvent, SleepInterval, User, WeightMeasurement, Workout
from app.schemas.queries import (
    enforce_glucose_point_limit,
    enforce_glucose_range_limit,
    parse_timezone,
    validate_glucose_resolution,
    validate_meal_limit,
    validate_summary_bucket,
    validate_time_range,
)
from app.schemas.responses import (
    CoverageCategory,
    CoverageMap,
    CoverageResponse,
    GlucoseBucketPoint,
    GlucoseDailySummary,
    GlucoseRawPoint,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    GlucoseSummaryStats,
    MealItem,
    MealsResponse,
)

logger = logging.getLogger("app.query")


def _dec(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _round_mg_dl(value: float | None, *, places: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return _ensure_aware(value).isoformat().replace("+00:00", "Z")


def _sign_payload(secret: str, payload: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def encode_meal_cursor(
    *,
    secret: str,
    range_start: datetime,
    range_end: datetime,
    completed_at: datetime,
    source_sample_id: str,
) -> str:
    payload = "|".join(
        [
            "v1",
            _iso_z(range_start),
            _iso_z(range_end),
            _iso_z(completed_at),
            source_sample_id,
        ]
    )
    signed = f"{payload}|{_sign_payload(secret, payload)}"
    return base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii")


def decode_meal_cursor(
    *,
    secret: str,
    cursor: str,
    range_start: datetime,
    range_end: datetime,
) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        version, start_s, end_s, stamp, source_sample_id, sig = raw.split("|", 5)
        if version != "v1" or not source_sample_id or not sig:
            raise ValueError("bad cursor fields")
        payload = "|".join([version, start_s, end_s, stamp, source_sample_id])
        expected = _sign_payload(secret, payload)
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        cursor_start = datetime.fromisoformat(start_s.replace("Z", "+00:00")).astimezone(UTC)
        cursor_end = datetime.fromisoformat(end_s.replace("Z", "+00:00")).astimezone(UTC)
        if cursor_start != _ensure_aware(range_start) or cursor_end != _ensure_aware(range_end):
            raise ValueError("range mismatch")
        completed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if completed_at.tzinfo is None:
            raise ValueError("naive cursor timestamp")
        return completed_at.astimezone(UTC), source_sample_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            code="INVALID_CURSOR",
            message="cursor is malformed or invalid",
            status_code=422,
        ) from exc


def _coverage_category(count: Any, first_at: Any, last_at: Any) -> CoverageCategory:
    n = int(count or 0)
    if n == 0:
        return CoverageCategory(count=0, first_at=None, last_at=None)
    return CoverageCategory(
        count=n,
        first_at=_ensure_aware(first_at) if first_at else None,
        last_at=_ensure_aware(last_at) if last_at else None,
    )


class HealthDataQueryService:
    """Read-only query surface for the personal-primary health dataset."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._timeout_applied = False

    async def _apply_statement_timeout(self) -> None:
        if self._timeout_applied:
            return
        ms = max(1, int(self.settings.query_statement_timeout_ms))
        # SET LOCAL is transaction-scoped; integer interpolation is intentional.
        await self.session.execute(text(f"SET LOCAL statement_timeout = '{ms}ms'"))
        self._timeout_applied = True

    async def resolve_personal_user(self) -> User:
        await self._apply_statement_timeout()
        external_id = self.settings.primary_user_external_id
        result = await self.session.execute(
            select(User).where(User.external_identifier == external_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise AppError(
                code="QUERY_FAILED",
                message="The requested health data could not be retrieved",
                status_code=500,
            )
        return user

    async def coverage(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
    ) -> CoverageResponse:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        user = await self.resolve_personal_user()

        glucose_q = select(
            literal("glucose").label("kind"),
            func.count().label("count"),
            func.min(GlucoseSample.sample_time).label("first_at"),
            func.max(GlucoseSample.sample_time).label("last_at"),
        ).where(
            GlucoseSample.user_id == user.id,
            GlucoseSample.deleted_at.is_(None),
            GlucoseSample.sample_time >= start_utc,
            GlucoseSample.sample_time < end_utc,
        )
        meals_q = select(
            literal("meals").label("kind"),
            func.count().label("count"),
            func.min(MealEvent.meal_completed_at).label("first_at"),
            func.max(MealEvent.meal_completed_at).label("last_at"),
        ).where(
            MealEvent.user_id == user.id,
            MealEvent.deleted_at.is_(None),
            MealEvent.meal_completed_at >= start_utc,
            MealEvent.meal_completed_at < end_utc,
        )
        workouts_q = select(
            literal("workouts").label("kind"),
            func.count().label("count"),
            func.min(Workout.start_time).label("first_at"),
            func.max(Workout.start_time).label("last_at"),
        ).where(
            Workout.user_id == user.id,
            Workout.deleted_at.is_(None),
            Workout.start_time >= start_utc,
            Workout.start_time < end_utc,
        )
        sleep_q = select(
            literal("sleep_intervals").label("kind"),
            func.count().label("count"),
            func.min(SleepInterval.start_time).label("first_at"),
            func.max(SleepInterval.start_time).label("last_at"),
        ).where(
            SleepInterval.user_id == user.id,
            SleepInterval.deleted_at.is_(None),
            SleepInterval.start_time >= start_utc,
            SleepInterval.start_time < end_utc,
        )
        weight_q = select(
            literal("weight_measurements").label("kind"),
            func.count().label("count"),
            func.min(WeightMeasurement.measured_at).label("first_at"),
            func.max(WeightMeasurement.measured_at).label("last_at"),
        ).where(
            WeightMeasurement.user_id == user.id,
            WeightMeasurement.deleted_at.is_(None),
            WeightMeasurement.measured_at >= start_utc,
            WeightMeasurement.measured_at < end_utc,
        )

        stmt = union_all(glucose_q, meals_q, workouts_q, sleep_q, weight_q)
        rows = {
            row.kind: row
            for row in (await self.session.execute(stmt)).all()
        }

        empty = CoverageCategory(count=0, first_at=None, last_at=None)

        def cat(kind: str) -> CoverageCategory:
            row = rows.get(kind)
            if row is None:
                return empty
            return _coverage_category(row.count, row.first_at, row.last_at)

        return CoverageResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            coverage=CoverageMap(
                glucose=cat("glucose"),
                meals=cat("meals"),
                workouts=cat("workouts"),
                sleep_intervals=cat("sleep_intervals"),
                weight_measurements=cat("weight_measurements"),
            ),
        )

    async def glucose_series(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        resolution: str,
        timezone: str | None,
    ) -> GlucoseSeriesResponse:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        resolution = validate_glucose_resolution(resolution)
        enforce_glucose_range_limit(start_utc, end_utc, resolution)
        user = await self.resolve_personal_user()

        if resolution == "raw":
            return await self._glucose_raw(
                request_id=request_id,
                user_id=user.id,
                start=start_utc,
                end=end_utc,
                timezone=tz_name,
            )
        return await self._glucose_aggregated(
            request_id=request_id,
            user_id=user.id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            resolution=resolution,
        )

    async def glucose_summary(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        bucket: str,
    ) -> GlucoseSummaryResponse:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        bucket = validate_summary_bucket(bucket)
        user = await self.resolve_personal_user()

        if bucket == "overall":
            summary = await self._glucose_overall_summary(user.id, start_utc, end_utc)
            return GlucoseSummaryResponse(
                request_id=request_id,
                start=start_utc,
                end=end_utc,
                timezone=tz_name,
                bucket="overall",
                summary=summary,
                days=None,
            )

        days = await self._glucose_daily_summary(user.id, start_utc, end_utc, tz_name)
        return GlucoseSummaryResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            bucket="daily",
            summary=None,
            days=days,
        )

    async def meals(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> MealsResponse:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        page_size = validate_meal_limit(limit)
        user = await self.resolve_personal_user()

        conditions = [
            MealEvent.user_id == user.id,
            MealEvent.deleted_at.is_(None),
            MealEvent.meal_completed_at >= start_utc,
            MealEvent.meal_completed_at < end_utc,
        ]
        if cursor:
            cursor_at, cursor_id = decode_meal_cursor(
                secret=self.settings.read_api_key,
                cursor=cursor,
                range_start=start_utc,
                range_end=end_utc,
            )
            conditions.append(
                (MealEvent.meal_completed_at > cursor_at)
                | (
                    (MealEvent.meal_completed_at == cursor_at)
                    & (MealEvent.source_sample_id > cursor_id)
                )
            )

        # Never select notes / metadata / internal IDs.
        stmt = (
            select(
                MealEvent.source_sample_id,
                MealEvent.meal_completed_at,
                MealEvent.foods,
                MealEvent.source,
            )
            .where(*conditions)
            .order_by(MealEvent.meal_completed_at.asc(), MealEvent.source_sample_id.asc())
            .limit(page_size + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        truncated = len(rows) > page_size
        page = rows[:page_size]

        items = [
            MealItem(
                id=row.source_sample_id,
                meal_completed_at=_ensure_aware(row.meal_completed_at),
                foods=[str(item) for item in (row.foods or [])],
                source=row.source,
            )
            for row in page
        ]
        next_cursor = None
        if truncated and page:
            last = page[-1]
            next_cursor = encode_meal_cursor(
                secret=self.settings.read_api_key,
                range_start=start_utc,
                range_end=end_utc,
                completed_at=last.meal_completed_at,
                source_sample_id=last.source_sample_id,
            )

        fresh = items[-1].meal_completed_at if items else None
        return MealsResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            record_count=len(items),
            truncated=truncated,
            next_cursor=next_cursor,
            data_fresh_through=fresh,
            items=items,
        )

    async def _glucose_raw(
        self,
        *,
        request_id: str,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> GlucoseSeriesResponse:
        stmt = (
            select(GlucoseSample.sample_time, GlucoseSample.value_mg_dl)
            .where(
                GlucoseSample.user_id == user_id,
                GlucoseSample.deleted_at.is_(None),
                GlucoseSample.sample_time >= start,
                GlucoseSample.sample_time < end,
            )
            .order_by(GlucoseSample.sample_time.asc())
            .limit(MAX_GLUCOSE_POINTS + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        enforce_glucose_point_limit(len(rows))
        points = [
            GlucoseRawPoint(
                timestamp=_ensure_aware(row.sample_time),
                value_mg_dl=_round_mg_dl(_dec(row.value_mg_dl), places=1) or 0.0,
            )
            for row in rows
        ]
        fresh = points[-1].timestamp if points else None
        return GlucoseSeriesResponse(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            resolution="raw",
            aggregation=None,
            source_record_count=len(points),
            returned_point_count=len(points),
            truncated=False,
            data_fresh_through=fresh,
            points=points,
        )

    async def _glucose_aggregated(
        self,
        *,
        request_id: str,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        timezone: str,
        resolution: str,
    ) -> GlucoseSeriesResponse:
        bucket_seconds = RESOLUTION_SECONDS[resolution]
        # UTC-aligned epoch bucketing: floor(epoch / N) * N
        bucket_start = func.to_timestamp(
            func.floor(func.extract("epoch", GlucoseSample.sample_time) / bucket_seconds)
            * bucket_seconds
        )
        # Single pass: per-bucket stats + window totals for source count and freshness.
        stmt = (
            select(
                bucket_start.label("bucket_start"),
                func.count().label("sample_count"),
                func.min(GlucoseSample.value_mg_dl).label("min_mg_dl"),
                func.max(GlucoseSample.value_mg_dl).label("max_mg_dl"),
                func.avg(GlucoseSample.value_mg_dl).label("mean_mg_dl"),
                func.sum(func.count()).over().label("source_record_count"),
                func.max(func.max(GlucoseSample.sample_time)).over().label("data_fresh_through"),
            )
            .where(
                GlucoseSample.user_id == user_id,
                GlucoseSample.deleted_at.is_(None),
                GlucoseSample.sample_time >= start,
                GlucoseSample.sample_time < end,
            )
            .group_by("bucket_start")
            .order_by("bucket_start")
            .limit(MAX_GLUCOSE_POINTS + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        enforce_glucose_point_limit(len(rows))

        source_record_count = int(rows[0].source_record_count) if rows else 0
        fresh = (
            _ensure_aware(rows[0].data_fresh_through)
            if rows and rows[0].data_fresh_through is not None
            else None
        )
        points: list[GlucoseBucketPoint] = []
        for row in rows:
            bucket = _ensure_aware(row.bucket_start)
            points.append(
                GlucoseBucketPoint(
                    start=bucket,
                    end=bucket + timedelta(seconds=bucket_seconds),
                    mean_mg_dl=_round_mg_dl(_dec(row.mean_mg_dl), places=1) or 0.0,
                    min_mg_dl=_round_mg_dl(_dec(row.min_mg_dl), places=1) or 0.0,
                    max_mg_dl=_round_mg_dl(_dec(row.max_mg_dl), places=1) or 0.0,
                    sample_count=int(row.sample_count),
                )
            )

        return GlucoseSeriesResponse(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            resolution=resolution,  # type: ignore[arg-type]
            aggregation="mean_min_max",
            source_record_count=source_record_count,
            returned_point_count=len(points),
            truncated=False,
            data_fresh_through=fresh,
            points=points,
        )

    async def _glucose_overall_summary(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> GlucoseSummaryStats:
        stmt = select(
            func.count().label("sample_count"),
            func.min(GlucoseSample.sample_time).label("first_at"),
            func.max(GlucoseSample.sample_time).label("last_at"),
            func.min(GlucoseSample.value_mg_dl).label("min_mg_dl"),
            func.max(GlucoseSample.value_mg_dl).label("max_mg_dl"),
            func.avg(GlucoseSample.value_mg_dl).label("mean_mg_dl"),
            func.percentile_cont(0.5)
            .within_group(GlucoseSample.value_mg_dl)
            .label("median_mg_dl"),
        ).where(
            GlucoseSample.user_id == user_id,
            GlucoseSample.deleted_at.is_(None),
            GlucoseSample.sample_time >= start,
            GlucoseSample.sample_time < end,
        )
        row = (await self.session.execute(stmt)).one()
        count = int(row.sample_count or 0)
        if count == 0:
            return GlucoseSummaryStats(
                sample_count=0,
                first_at=None,
                last_at=None,
                min_mg_dl=None,
                max_mg_dl=None,
                mean_mg_dl=None,
                median_mg_dl=None,
            )
        return GlucoseSummaryStats(
            sample_count=count,
            first_at=_ensure_aware(row.first_at),
            last_at=_ensure_aware(row.last_at),
            min_mg_dl=_round_mg_dl(_dec(row.min_mg_dl), places=1),
            max_mg_dl=_round_mg_dl(_dec(row.max_mg_dl), places=1),
            mean_mg_dl=_round_mg_dl(_dec(row.mean_mg_dl), places=1),
            median_mg_dl=_round_mg_dl(_dec(row.median_mg_dl), places=1),
        )

    async def _glucose_daily_summary(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> list[GlucoseDailySummary]:
        # timestamptz → local wall clock via AT TIME ZONE, then truncate to local day.
        local_day = func.date_trunc("day", func.timezone(timezone, GlucoseSample.sample_time))

        stmt = (
            select(
                local_day.label("local_day"),
                func.count().label("sample_count"),
                func.min(GlucoseSample.sample_time).label("first_at"),
                func.max(GlucoseSample.sample_time).label("last_at"),
                func.min(GlucoseSample.value_mg_dl).label("min_mg_dl"),
                func.max(GlucoseSample.value_mg_dl).label("max_mg_dl"),
                func.avg(GlucoseSample.value_mg_dl).label("mean_mg_dl"),
                func.percentile_cont(0.5)
                .within_group(GlucoseSample.value_mg_dl)
                .label("median_mg_dl"),
            )
            .where(
                GlucoseSample.user_id == user_id,
                GlucoseSample.deleted_at.is_(None),
                GlucoseSample.sample_time >= start,
                GlucoseSample.sample_time < end,
            )
            .group_by("local_day")
            .order_by("local_day")
        )

        rows = list((await self.session.execute(stmt)).all())
        days: list[GlucoseDailySummary] = []
        for row in rows:
            local_day_value = row.local_day
            if isinstance(local_day_value, datetime):
                local_date_value = local_day_value.date()
            else:
                local_date_value = local_day_value
            days.append(
                GlucoseDailySummary(
                    local_date=local_date_value,
                    sample_count=int(row.sample_count),
                    first_at=_ensure_aware(row.first_at),
                    last_at=_ensure_aware(row.last_at),
                    min_mg_dl=_round_mg_dl(_dec(row.min_mg_dl), places=1) or 0.0,
                    max_mg_dl=_round_mg_dl(_dec(row.max_mg_dl), places=1) or 0.0,
                    mean_mg_dl=_round_mg_dl(_dec(row.mean_mg_dl), places=1) or 0.0,
                    median_mg_dl=_round_mg_dl(_dec(row.median_mg_dl), places=1) or 0.0,
                )
            )
        return days
