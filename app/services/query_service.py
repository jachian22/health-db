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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, literal, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import (
    DEFAULT_GLUCOSE_LOOKBACK_HOURS,
    DEFAULT_MEAL_LOOKBACK_DAYS,
    DEFAULT_SLEEP_LOOKBACK_HOURS,
    MAX_GLUCOSE_LOOKBACK_HOURS,
    MAX_GLUCOSE_POINTS,
    MAX_MEAL_LOOKBACK_DAYS,
    MAX_SLEEP_LOOKBACK_HOURS,
    MAX_SLEEP_RANGE_DAYS,
    MAX_TIMELINE_ITEMS_PER_CATEGORY,
    MAX_TIMELINE_RANGE_HOURS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
    RESOLUTION_SECONDS,
    SNAPSHOT_WEIGHT_LOOKBACK_DAYS,
    SNAPSHOT_WORKOUT_LOOKBACK_DAYS,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import GlucoseSample, MealEvent, SleepInterval, User, WeightMeasurement, Workout
from app.schemas.queries import (
    enforce_glucose_point_limit,
    enforce_glucose_range_limit,
    enforce_max_range_days,
    enforce_max_range_hours,
    parse_bound_timestamp,
    parse_timezone,
    validate_glucose_resolution,
    validate_lookback,
    validate_page_limit,
    validate_summary_bucket,
    validate_time_range,
)
from app.schemas.responses import (
    LAST_MEAL_DERIVED_BASIS,
    LAST_MEAL_LIMITS_FOUND,
    LAST_MEAL_LIMITS_MISSING,
    SNAPSHOT_LIMITS,
    TIMELINE_LIMITS,
    ContextSnapshotResponse,
    CoverageCategory,
    CoverageMap,
    CoverageResponse,
    GlucoseBucketPoint,
    GlucoseDailySummary,
    GlucoseRawPoint,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    GlucoseSummaryStats,
    LastLoggedMealResponse,
    LastMealDerived,
    MealItem,
    MealsResponse,
    PersonalTimelineResponse,
    RecentSleepIntervals,
    SleepIntervalItem,
    SleepIntervalsResponse,
    TimelineGlucoseSeries,
    UnavailableItem,
    WeightMeasurementItem,
    WeightMeasurementsResponse,
    WorkoutItem,
    WorkoutsResponse,
)

PAGE_CURSOR_KINDS = frozenset({"workouts", "sleep_intervals", "weight_measurements"})

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


def _encode_signed_cursor(secret: str, *parts: str) -> str:
    payload = "|".join(parts)
    signed = f"{payload}|{_sign_payload(secret, payload)}"
    return base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii")


def _decode_signed_cursor(secret: str, cursor: str, payload_fields: int) -> list[str]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    pieces = raw.split("|", payload_fields)
    if len(pieces) != payload_fields + 1:
        raise ValueError("bad cursor fields")
    *fields, sig = pieces
    if not sig or any(not field for field in fields):
        raise ValueError("bad cursor fields")
    payload = "|".join(fields)
    expected = _sign_payload(secret, payload)
    if not hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    return fields


def _parse_cursor_range(
    start_s: str,
    end_s: str,
    stamp_s: str,
    range_start: datetime,
    range_end: datetime,
) -> datetime:
    cursor_start = datetime.fromisoformat(start_s.replace("Z", "+00:00")).astimezone(UTC)
    cursor_end = datetime.fromisoformat(end_s.replace("Z", "+00:00")).astimezone(UTC)
    if cursor_start != _ensure_aware(range_start) or cursor_end != _ensure_aware(range_end):
        raise ValueError("range mismatch")
    stamp_at = datetime.fromisoformat(stamp_s.replace("Z", "+00:00"))
    if stamp_at.tzinfo is None:
        raise ValueError("naive cursor timestamp")
    return stamp_at.astimezone(UTC)


def encode_meal_cursor(
    *,
    secret: str,
    range_start: datetime,
    range_end: datetime,
    completed_at: datetime,
    source_sample_id: str,
) -> str:
    return _encode_signed_cursor(
        secret,
        "v1",
        _iso_z(range_start),
        _iso_z(range_end),
        _iso_z(completed_at),
        source_sample_id,
    )


def decode_meal_cursor(
    *,
    secret: str,
    cursor: str,
    range_start: datetime,
    range_end: datetime,
) -> tuple[datetime, str]:
    try:
        version, start_s, end_s, stamp, source_sample_id = _decode_signed_cursor(
            secret, cursor, 5
        )
        if version != "v1":
            raise ValueError("bad cursor fields")
        completed_at = _parse_cursor_range(
            start_s, end_s, stamp, range_start, range_end
        )
        return completed_at, source_sample_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            code="INVALID_CURSOR",
            message="cursor is malformed or invalid",
            status_code=422,
        ) from exc


def encode_page_cursor(
    *,
    secret: str,
    kind: str,
    range_start: datetime,
    range_end: datetime,
    stamp: datetime,
    source_sample_id: str,
) -> str:
    return _encode_signed_cursor(
        secret,
        "v1",
        kind,
        _iso_z(range_start),
        _iso_z(range_end),
        _iso_z(stamp),
        source_sample_id,
    )


def decode_page_cursor(
    *,
    secret: str,
    cursor: str,
    kind: str,
    range_start: datetime,
    range_end: datetime,
) -> tuple[datetime, str]:
    try:
        if kind not in PAGE_CURSOR_KINDS:
            raise ValueError("bad cursor kind")
        version, cursor_kind, start_s, end_s, stamp, source_sample_id = (
            _decode_signed_cursor(secret, cursor, 6)
        )
        if version != "v1" or cursor_kind != kind:
            raise ValueError("bad cursor fields")
        stamp_at = _parse_cursor_range(start_s, end_s, stamp, range_start, range_end)
        return stamp_at, source_sample_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            code="INVALID_CURSOR",
            message="cursor is malformed or invalid",
            status_code=422,
        ) from exc


def _duration_minutes(start: datetime, end: datetime) -> float:
    return round((_ensure_aware(end) - _ensure_aware(start)).total_seconds() / 60, 1)


def _keyset_after(column, cursor_at: datetime, source_sample_id_column, cursor_id: str):
    return (column > cursor_at) | ((column == cursor_at) & (source_sample_id_column > cursor_id))


def _interval_freshness(items: Sequence[Any]) -> datetime | None:
    if not items:
        return None
    return max(item.end_time for item in items)


@dataclass(frozen=True)
class _AggregatedGlucose:
    source_record_count: int
    data_fresh_through: datetime | None
    points: list[GlucoseBucketPoint]

    @property
    def returned_point_count(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class _ListWindow:
    request_id: str
    start_utc: datetime
    end_utc: datetime
    timezone: str
    page_size: int
    user: User


def _coverage_category(count: Any, first_at: Any, last_at: Any) -> CoverageCategory:
    n = int(count or 0)
    if n == 0:
        return CoverageCategory(count=0, first_at=None, last_at=None)
    return CoverageCategory(
        count=n,
        first_at=_ensure_aware(first_at) if first_at else None,
        last_at=_ensure_aware(last_at) if last_at else None,
    )


def _meal_item(row: Any) -> MealItem:
    return MealItem(
        id=row.source_sample_id,
        meal_completed_at=_ensure_aware(row.meal_completed_at),
        foods=[str(item) for item in (row.foods or [])],
        source=row.source,
    )


def _workout_item(row: Any) -> WorkoutItem:
    return WorkoutItem(
        id=row.source_sample_id,
        start_time=_ensure_aware(row.start_time),
        end_time=_ensure_aware(row.end_time),
        sport=row.sport,
        distance_meters=_dec(row.distance_meters),
        duration_minutes=_duration_minutes(row.start_time, row.end_time),
        source=row.source,
    )


def _sleep_item(row: Any) -> SleepIntervalItem:
    return SleepIntervalItem(
        id=row.source_sample_id,
        start_time=_ensure_aware(row.start_time),
        end_time=_ensure_aware(row.end_time),
        duration_minutes=_duration_minutes(row.start_time, row.end_time),
        stage=row.stage,
        source=row.source,
    )


def _weight_item(row: Any) -> WeightMeasurementItem:
    value_kg = _dec(row.value_kg)
    if value_kg is None:
        raise AppError(
            code="QUERY_FAILED",
            message="The requested health data could not be retrieved",
            status_code=500,
        )
    return WeightMeasurementItem(
        id=row.source_sample_id,
        measured_at=_ensure_aware(row.measured_at),
        value_kg=value_kg,
        source=row.source,
    )


def _last_meal_derived(meal: MealItem | None, anchor: datetime) -> LastMealDerived:
    if meal is None:
        return LastMealDerived(minutes_since_last_logged_meal=None, basis=None)
    return LastMealDerived(
        minutes_since_last_logged_meal=_duration_minutes(meal.meal_completed_at, anchor),
        basis=LAST_MEAL_DERIVED_BASIS,
    )


def _empty_sleep_aggregate() -> RecentSleepIntervals:
    return RecentSleepIntervals(
        record_count=0,
        first_start_time=None,
        last_end_time=None,
        sources=[],
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

    async def _prepare_list(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        limit: int | None,
        max_days: int | None = None,
        range_label: str | None = None,
    ) -> _ListWindow:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        if max_days is not None:
            enforce_max_range_days(
                start_utc,
                end_utc,
                max_days=max_days,
                label=range_label or "Query",
            )
        return _ListWindow(
            request_id=request_id,
            start_utc=start_utc,
            end_utc=end_utc,
            timezone=tz_name,
            page_size=validate_page_limit(limit),
            user=await self.resolve_personal_user(),
        )

    def _cursor_keyset(
        self,
        *,
        cursor: str | None,
        kind: str,
        window: _ListWindow,
        sort_column: Any,
        id_column: Any,
    ) -> Any | None:
        if not cursor:
            return None
        secret = self.settings.read_api_key
        if kind == "meals":
            cursor_at, cursor_id = decode_meal_cursor(
                secret=secret,
                cursor=cursor,
                range_start=window.start_utc,
                range_end=window.end_utc,
            )
        else:
            cursor_at, cursor_id = decode_page_cursor(
                secret=secret,
                cursor=cursor,
                kind=kind,
                range_start=window.start_utc,
                range_end=window.end_utc,
            )
        return _keyset_after(sort_column, cursor_at, id_column, cursor_id)

    def _paged_response[TItem, TResp](
        self,
        *,
        window: _ListWindow,
        rows: Sequence[Any],
        kind: str,
        item_builder: Callable[[Any], TItem],
        stamp_of_row: Callable[[Any], datetime],
        id_of_row: Callable[[Any], str],
        fresh_of_items: Callable[[list[TItem]], datetime | None],
        response_cls: type[TResp],
    ) -> TResp:
        truncated = len(rows) > window.page_size
        page = list(rows[: window.page_size])
        items = [item_builder(row) for row in page]
        next_cursor = None
        if truncated and page:
            last = page[-1]
            secret = self.settings.read_api_key
            if kind == "meals":
                next_cursor = encode_meal_cursor(
                    secret=secret,
                    range_start=window.start_utc,
                    range_end=window.end_utc,
                    completed_at=stamp_of_row(last),
                    source_sample_id=id_of_row(last),
                )
            else:
                next_cursor = encode_page_cursor(
                    secret=secret,
                    kind=kind,
                    range_start=window.start_utc,
                    range_end=window.end_utc,
                    stamp=stamp_of_row(last),
                    source_sample_id=id_of_row(last),
                )
        return response_cls(
            request_id=window.request_id,
            start=window.start_utc,
            end=window.end_utc,
            timezone=window.timezone,
            record_count=len(items),
            truncated=truncated,
            next_cursor=next_cursor,
            data_fresh_through=fresh_of_items(items) if items else None,
            items=items,
        )

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
        return CoverageResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            coverage=await self._coverage_map(user.id, start_utc, end_utc),
        )

    async def _coverage_map(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> CoverageMap:
        glucose_q = select(
            literal("glucose").label("kind"),
            func.count().label("count"),
            func.min(GlucoseSample.sample_time).label("first_at"),
            func.max(GlucoseSample.sample_time).label("last_at"),
        ).where(
            GlucoseSample.user_id == user_id,
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
            MealEvent.user_id == user_id,
            MealEvent.deleted_at.is_(None),
            MealEvent.meal_completed_at >= start_utc,
            MealEvent.meal_completed_at < end_utc,
        )
        workouts_q = select(
            literal("workouts").label("kind"),
            func.count().label("count"),
            # first_at / last_at remain min/max stored start_time of overlapping rows.
            func.min(Workout.start_time).label("first_at"),
            func.max(Workout.start_time).label("last_at"),
        ).where(
            Workout.user_id == user_id,
            Workout.deleted_at.is_(None),
            Workout.start_time < end_utc,
            Workout.end_time > start_utc,
        )
        sleep_q = select(
            literal("sleep_intervals").label("kind"),
            func.count().label("count"),
            func.min(SleepInterval.start_time).label("first_at"),
            func.max(SleepInterval.start_time).label("last_at"),
        ).where(
            SleepInterval.user_id == user_id,
            SleepInterval.deleted_at.is_(None),
            SleepInterval.start_time < end_utc,
            SleepInterval.end_time > start_utc,
        )
        weight_q = select(
            literal("weight_measurements").label("kind"),
            func.count().label("count"),
            func.min(WeightMeasurement.measured_at).label("first_at"),
            func.max(WeightMeasurement.measured_at).label("last_at"),
        ).where(
            WeightMeasurement.user_id == user_id,
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

        return CoverageMap(
            glucose=cat("glucose"),
            meals=cat("meals"),
            workouts=cat("workouts"),
            sleep_intervals=cat("sleep_intervals"),
            weight_measurements=cat("weight_measurements"),
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
        aggregated = await self._load_aggregated_glucose(
            user_id=user.id,
            start=start_utc,
            end=end_utc,
            resolution=resolution,
        )
        return GlucoseSeriesResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            resolution=resolution,  # type: ignore[arg-type]
            aggregation="mean_min_max",
            source_record_count=aggregated.source_record_count,
            returned_point_count=aggregated.returned_point_count,
            truncated=False,
            data_fresh_through=aggregated.data_fresh_through,
            points=aggregated.points,
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
        window = await self._prepare_list(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
        )
        conditions = [
            MealEvent.user_id == window.user.id,
            MealEvent.deleted_at.is_(None),
            MealEvent.meal_completed_at >= window.start_utc,
            MealEvent.meal_completed_at < window.end_utc,
        ]
        keyset = self._cursor_keyset(
            cursor=cursor,
            kind="meals",
            window=window,
            sort_column=MealEvent.meal_completed_at,
            id_column=MealEvent.source_sample_id,
        )
        if keyset is not None:
            conditions.append(keyset)

        stmt = (
            select(
                MealEvent.source_sample_id,
                MealEvent.meal_completed_at,
                MealEvent.foods,
                MealEvent.source,
            )
            .where(*conditions)
            .order_by(MealEvent.meal_completed_at.asc(), MealEvent.source_sample_id.asc())
            .limit(window.page_size + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        return self._paged_response(
            window=window,
            rows=rows,
            kind="meals",
            item_builder=_meal_item,
            stamp_of_row=lambda row: row.meal_completed_at,
            id_of_row=lambda row: row.source_sample_id,
            fresh_of_items=lambda items: items[-1].meal_completed_at,
            response_cls=MealsResponse,
        )

    async def workouts(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> WorkoutsResponse:
        # Canonical rows are whatever survived ingest: source_name must be
        # "Strava" or the record is rejected (UNSUPPORTED_WORKOUT_SOURCE).
        # There is no query-time overlap collapse against Apple Health copies.
        # Public `source` is the stored provenance column (typically apple_health).
        #
        # Overlap uses end_time. There is no ix_workouts_user_id_end_time in M1.
        # After deploy, inspect EXPLAIN (ANALYZE, BUFFERS) on this query (and
        # coverage) before adding that index.
        window = await self._prepare_list(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_WORKOUT_RANGE_DAYS,
            range_label="Workout",
        )
        conditions = [
            Workout.user_id == window.user.id,
            Workout.deleted_at.is_(None),
            Workout.start_time < window.end_utc,
            Workout.end_time > window.start_utc,
        ]
        keyset = self._cursor_keyset(
            cursor=cursor,
            kind="workouts",
            window=window,
            sort_column=Workout.start_time,
            id_column=Workout.source_sample_id,
        )
        if keyset is not None:
            conditions.append(keyset)

        stmt = (
            select(
                Workout.source_sample_id,
                Workout.start_time,
                Workout.end_time,
                Workout.sport,
                Workout.distance_meters,
                Workout.source,
            )
            .where(*conditions)
            .order_by(Workout.start_time.asc(), Workout.source_sample_id.asc())
            .limit(window.page_size + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        return self._paged_response(
            window=window,
            rows=rows,
            kind="workouts",
            item_builder=_workout_item,
            stamp_of_row=lambda row: row.start_time,
            id_of_row=lambda row: row.source_sample_id,
            fresh_of_items=_interval_freshness,
            response_cls=WorkoutsResponse,
        )

    async def sleep_intervals(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> SleepIntervalsResponse:
        window = await self._prepare_list(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_SLEEP_RANGE_DAYS,
            range_label="Sleep interval",
        )
        conditions = [
            SleepInterval.user_id == window.user.id,
            SleepInterval.deleted_at.is_(None),
            SleepInterval.start_time < window.end_utc,
            SleepInterval.end_time > window.start_utc,
        ]
        keyset = self._cursor_keyset(
            cursor=cursor,
            kind="sleep_intervals",
            window=window,
            sort_column=SleepInterval.start_time,
            id_column=SleepInterval.source_sample_id,
        )
        if keyset is not None:
            conditions.append(keyset)

        stmt = (
            select(
                SleepInterval.source_sample_id,
                SleepInterval.start_time,
                SleepInterval.end_time,
                SleepInterval.stage,
                SleepInterval.source,
            )
            .where(*conditions)
            .order_by(SleepInterval.start_time.asc(), SleepInterval.source_sample_id.asc())
            .limit(window.page_size + 1)
        )
        rows = list((await self.session.execute(stmt)).all())
        return self._paged_response(
            window=window,
            rows=rows,
            kind="sleep_intervals",
            item_builder=_sleep_item,
            stamp_of_row=lambda row: row.start_time,
            id_of_row=lambda row: row.source_sample_id,
            fresh_of_items=_interval_freshness,
            response_cls=SleepIntervalsResponse,
        )

    async def weight_measurements(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> WeightMeasurementsResponse:
        window = await self._prepare_list(
            request_id=request_id,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_WEIGHT_RANGE_DAYS,
            range_label="Weight measurement",
        )
        conditions = [
            WeightMeasurement.user_id == window.user.id,
            WeightMeasurement.deleted_at.is_(None),
            WeightMeasurement.measured_at >= window.start_utc,
            WeightMeasurement.measured_at < window.end_utc,
        ]
        keyset = self._cursor_keyset(
            cursor=cursor,
            kind="weight_measurements",
            window=window,
            sort_column=WeightMeasurement.measured_at,
            id_column=WeightMeasurement.source_sample_id,
        )
        if keyset is not None:
            conditions.append(keyset)

        stmt = (
            select(
                WeightMeasurement.source_sample_id,
                WeightMeasurement.measured_at,
                WeightMeasurement.value_kg,
                WeightMeasurement.source,
            )
            .where(*conditions)
            .order_by(
                WeightMeasurement.measured_at.asc(),
                WeightMeasurement.source_sample_id.asc(),
            )
            .limit(window.page_size + 1)
        )
        rows = list((await self.session.execute(stmt)).all())

        return self._paged_response(
            window=window,
            rows=rows,
            kind="weight_measurements",
            item_builder=_weight_item,
            stamp_of_row=lambda row: row.measured_at,
            id_of_row=lambda row: row.source_sample_id,
            fresh_of_items=lambda items: items[-1].measured_at,
            response_cls=WeightMeasurementsResponse,
        )

    async def last_logged_meal(
        self,
        *,
        request_id: str,
        anchor: datetime,
        timezone: str | None,
        lookback_days: str | int | None,
    ) -> LastLoggedMealResponse:
        anchor_utc = parse_bound_timestamp(anchor, field_name="anchor")
        tz_name = parse_timezone(timezone)
        resolved_lookback = validate_lookback(
            lookback_days,
            default=DEFAULT_MEAL_LOOKBACK_DAYS,
            max_value=MAX_MEAL_LOOKBACK_DAYS,
            unit="days",
            field_name="lookback_days",
            label="Meal lookback",
        )
        user = await self.resolve_personal_user()
        lookback_start = anchor_utc - timedelta(days=resolved_lookback)
        meal = await self._select_last_logged_meal(user.id, anchor_utc, lookback_start)
        found = meal is not None
        return LastLoggedMealResponse(
            request_id=request_id,
            anchor=anchor_utc,
            timezone=tz_name,
            lookback_days=resolved_lookback,
            meal=meal,
            derived=_last_meal_derived(meal, anchor_utc),
            limits=list(LAST_MEAL_LIMITS_FOUND if found else LAST_MEAL_LIMITS_MISSING),
        )

    async def build_context_snapshot(
        self,
        *,
        request_id: str,
        anchor: datetime,
        timezone: str | None,
        meal_lookback_days: str | int | None,
        sleep_lookback_hours: str | int | None,
        glucose_lookback_hours: str | int | None,
    ) -> ContextSnapshotResponse:
        anchor_utc = parse_bound_timestamp(anchor, field_name="anchor")
        tz_name = parse_timezone(timezone)
        resolved_meal_lookback = validate_lookback(
            meal_lookback_days,
            default=DEFAULT_MEAL_LOOKBACK_DAYS,
            max_value=MAX_MEAL_LOOKBACK_DAYS,
            unit="days",
            field_name="meal_lookback_days",
            label="Meal lookback",
        )
        resolved_sleep_lookback = validate_lookback(
            sleep_lookback_hours,
            default=DEFAULT_SLEEP_LOOKBACK_HOURS,
            max_value=MAX_SLEEP_LOOKBACK_HOURS,
            unit="hours",
            field_name="sleep_lookback_hours",
            label="Sleep lookback",
        )
        resolved_glucose_lookback = validate_lookback(
            glucose_lookback_hours,
            default=DEFAULT_GLUCOSE_LOOKBACK_HOURS,
            max_value=MAX_GLUCOSE_LOOKBACK_HOURS,
            unit="hours",
            field_name="glucose_lookback_hours",
            label="Glucose lookback",
        )
        user = await self.resolve_personal_user()
        user_id = user.id

        meal_start = anchor_utc - timedelta(days=resolved_meal_lookback)
        workout_start = anchor_utc - timedelta(days=SNAPSHOT_WORKOUT_LOOKBACK_DAYS)
        sleep_start = anchor_utc - timedelta(hours=resolved_sleep_lookback)
        weight_start = anchor_utc - timedelta(days=SNAPSHOT_WEIGHT_LOOKBACK_DAYS)
        glucose_start = anchor_utc - timedelta(hours=resolved_glucose_lookback)

        meal = await self._select_last_logged_meal(user_id, anchor_utc, meal_start)
        workout = await self._select_latest_completed_workout(
            user_id, anchor_utc, workout_start
        )
        sleep = await self._select_recent_sleep_aggregate(user_id, sleep_start, anchor_utc)
        weight = await self._select_latest_weight(user_id, anchor_utc, weight_start)
        glucose_coverage = await self._glucose_coverage(user_id, glucose_start, anchor_utc)
        glucose_summary = await self._glucose_overall_summary(
            user_id, glucose_start, anchor_utc
        )

        unavailable: list[UnavailableItem] = []
        if meal is None:
            unavailable.append(
                UnavailableItem(
                    category="last_logged_meal", reason="no_record_in_lookback"
                )
            )
        if workout is None:
            unavailable.append(
                UnavailableItem(
                    category="most_recent_workout", reason="no_record_in_lookback"
                )
            )
        if sleep.record_count == 0:
            unavailable.append(
                UnavailableItem(
                    category="recent_sleep_intervals", reason="no_record_in_lookback"
                )
            )
        if weight is None:
            unavailable.append(
                UnavailableItem(
                    category="most_recent_weight_measurement",
                    reason="no_record_in_lookback",
                )
            )
        if glucose_coverage.count == 0:
            unavailable.append(
                UnavailableItem(
                    category="glucose_coverage", reason="no_samples_in_window"
                )
            )
        if glucose_summary.sample_count == 0:
            unavailable.append(
                UnavailableItem(
                    category="glucose_summary", reason="no_samples_in_window"
                )
            )

        return ContextSnapshotResponse(
            request_id=request_id,
            anchor=anchor_utc,
            timezone=tz_name,
            meal_lookback_days=resolved_meal_lookback,
            sleep_lookback_hours=resolved_sleep_lookback,
            glucose_lookback_hours=resolved_glucose_lookback,
            last_logged_meal=meal,
            most_recent_workout=workout,
            recent_sleep_intervals=sleep,
            most_recent_weight_measurement=weight,
            glucose_coverage=glucose_coverage,
            glucose_summary=glucose_summary,
            derived=_last_meal_derived(meal, anchor_utc),
            unavailable=unavailable,
            limits=list(SNAPSHOT_LIMITS),
        )

    async def personal_timeline(
        self,
        *,
        request_id: str,
        start: datetime,
        end: datetime,
        timezone: str | None,
    ) -> PersonalTimelineResponse:
        start_utc, end_utc = validate_time_range(start, end)
        tz_name = parse_timezone(timezone)
        enforce_max_range_hours(
            start_utc,
            end_utc,
            max_hours=MAX_TIMELINE_RANGE_HOURS,
            label="Personal timeline",
        )
        user = await self.resolve_personal_user()
        user_id = user.id

        meals = await self._fetch_timeline_meals(user_id, start_utc, end_utc)
        workouts = await self._fetch_timeline_workouts(user_id, start_utc, end_utc)
        sleep_intervals = await self._fetch_timeline_sleep_intervals(
            user_id, start_utc, end_utc
        )
        weight_measurements = await self._fetch_timeline_weight_measurements(
            user_id, start_utc, end_utc
        )
        glucose = await self._load_aggregated_glucose(
            user_id=user_id,
            start=start_utc,
            end=end_utc,
            resolution="15m",
        )
        coverage = await self._coverage_map(user_id, start_utc, end_utc)

        return PersonalTimelineResponse(
            request_id=request_id,
            start=start_utc,
            end=end_utc,
            timezone=tz_name,
            glucose_resolution="15m",
            meals=meals,
            workouts=workouts,
            sleep_intervals=sleep_intervals,
            weight_measurements=weight_measurements,
            glucose=TimelineGlucoseSeries(
                aggregation="mean_min_max",
                source_record_count=glucose.source_record_count,
                returned_point_count=glucose.returned_point_count,
                truncated=False,
                data_fresh_through=glucose.data_fresh_through,
                points=glucose.points,
            ),
            coverage=coverage,
            limits=list(TIMELINE_LIMITS),
        )

    async def _select_last_logged_meal(
        self,
        user_id: uuid.UUID,
        anchor: datetime,
        lookback_start: datetime,
    ) -> MealItem | None:
        stmt = (
            select(
                MealEvent.source_sample_id,
                MealEvent.meal_completed_at,
                MealEvent.foods,
                MealEvent.source,
            )
            .where(
                MealEvent.user_id == user_id,
                MealEvent.deleted_at.is_(None),
                MealEvent.meal_completed_at <= anchor,
                MealEvent.meal_completed_at >= lookback_start,
            )
            .order_by(
                MealEvent.meal_completed_at.desc(),
                MealEvent.source_sample_id.desc(),
            )
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return _meal_item(row) if row is not None else None

    async def _select_latest_completed_workout(
        self,
        user_id: uuid.UUID,
        anchor: datetime,
        lookback_start: datetime,
    ) -> WorkoutItem | None:
        stmt = (
            select(
                Workout.source_sample_id,
                Workout.start_time,
                Workout.end_time,
                Workout.sport,
                Workout.distance_meters,
                Workout.source,
            )
            .where(
                Workout.user_id == user_id,
                Workout.deleted_at.is_(None),
                Workout.end_time <= anchor,
                Workout.end_time >= lookback_start,
            )
            .order_by(Workout.end_time.desc(), Workout.source_sample_id.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return _workout_item(row) if row is not None else None

    async def _glucose_coverage(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> CoverageCategory:
        row = (
            await self.session.execute(
                select(
                    func.count().label("count"),
                    func.min(GlucoseSample.sample_time).label("first_at"),
                    func.max(GlucoseSample.sample_time).label("last_at"),
                ).where(
                    GlucoseSample.user_id == user_id,
                    GlucoseSample.deleted_at.is_(None),
                    GlucoseSample.sample_time >= start_utc,
                    GlucoseSample.sample_time < end_utc,
                )
            )
        ).one()
        return _coverage_category(row.count, row.first_at, row.last_at)

    async def _select_recent_sleep_aggregate(
        self,
        user_id: uuid.UUID,
        window_start: datetime,
        anchor: datetime,
    ) -> RecentSleepIntervals:
        row = (
            await self.session.execute(
                select(
                    func.count().label("record_count"),
                    func.min(SleepInterval.start_time).label("first_start_time"),
                    func.max(SleepInterval.end_time).label("last_end_time"),
                    func.array_agg(distinct(SleepInterval.source)).label("sources"),
                ).where(
                    SleepInterval.user_id == user_id,
                    SleepInterval.deleted_at.is_(None),
                    SleepInterval.start_time < anchor,
                    SleepInterval.end_time > window_start,
                )
            )
        ).one()
        count = int(row.record_count or 0)
        if count == 0:
            return _empty_sleep_aggregate()
        sources = sorted({str(source) for source in (row.sources or []) if source is not None})
        return RecentSleepIntervals(
            record_count=count,
            first_start_time=_ensure_aware(row.first_start_time),
            last_end_time=_ensure_aware(row.last_end_time),
            sources=sources,
        )

    async def _select_latest_weight(
        self,
        user_id: uuid.UUID,
        anchor: datetime,
        lookback_start: datetime,
    ) -> WeightMeasurementItem | None:
        stmt = (
            select(
                WeightMeasurement.source_sample_id,
                WeightMeasurement.measured_at,
                WeightMeasurement.value_kg,
                WeightMeasurement.source,
            )
            .where(
                WeightMeasurement.user_id == user_id,
                WeightMeasurement.deleted_at.is_(None),
                WeightMeasurement.measured_at <= anchor,
                WeightMeasurement.measured_at >= lookback_start,
            )
            .order_by(
                WeightMeasurement.measured_at.desc(),
                WeightMeasurement.source_sample_id.desc(),
            )
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return _weight_item(row) if row is not None else None

    def _enforce_timeline_item_cap(self, rows: Sequence[Any], *, category: str) -> None:
        if len(rows) > MAX_TIMELINE_ITEMS_PER_CATEGORY:
            raise AppError(
                code="RESULT_TOO_LARGE",
                message=(
                    f"Personal timeline matched more than {MAX_TIMELINE_ITEMS_PER_CATEGORY} "
                    f"{category} records; narrow the time range"
                ),
                status_code=422,
                details={
                    "max_items": MAX_TIMELINE_ITEMS_PER_CATEGORY,
                    "category": category,
                },
            )

    async def _fetch_capped[T](
        self,
        stmt: Any,
        *,
        category: str,
        item_builder: Callable[[Any], T],
    ) -> list[T]:
        rows = list((await self.session.execute(stmt)).all())
        self._enforce_timeline_item_cap(rows, category=category)
        return [item_builder(row) for row in rows]

    async def _fetch_timeline_meals(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[MealItem]:
        stmt = (
            select(
                MealEvent.source_sample_id,
                MealEvent.meal_completed_at,
                MealEvent.foods,
                MealEvent.source,
            )
            .where(
                MealEvent.user_id == user_id,
                MealEvent.deleted_at.is_(None),
                MealEvent.meal_completed_at >= start_utc,
                MealEvent.meal_completed_at < end_utc,
            )
            .order_by(MealEvent.meal_completed_at.asc(), MealEvent.source_sample_id.asc())
            .limit(MAX_TIMELINE_ITEMS_PER_CATEGORY + 1)
        )
        return await self._fetch_capped(stmt, category="meals", item_builder=_meal_item)

    async def _fetch_timeline_workouts(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[WorkoutItem]:
        stmt = (
            select(
                Workout.source_sample_id,
                Workout.start_time,
                Workout.end_time,
                Workout.sport,
                Workout.distance_meters,
                Workout.source,
            )
            .where(
                Workout.user_id == user_id,
                Workout.deleted_at.is_(None),
                Workout.start_time < end_utc,
                Workout.end_time > start_utc,
            )
            .order_by(Workout.start_time.asc(), Workout.source_sample_id.asc())
            .limit(MAX_TIMELINE_ITEMS_PER_CATEGORY + 1)
        )
        return await self._fetch_capped(
            stmt, category="workouts", item_builder=_workout_item
        )

    async def _fetch_timeline_sleep_intervals(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[SleepIntervalItem]:
        stmt = (
            select(
                SleepInterval.source_sample_id,
                SleepInterval.start_time,
                SleepInterval.end_time,
                SleepInterval.stage,
                SleepInterval.source,
            )
            .where(
                SleepInterval.user_id == user_id,
                SleepInterval.deleted_at.is_(None),
                SleepInterval.start_time < end_utc,
                SleepInterval.end_time > start_utc,
            )
            .order_by(SleepInterval.start_time.asc(), SleepInterval.source_sample_id.asc())
            .limit(MAX_TIMELINE_ITEMS_PER_CATEGORY + 1)
        )
        return await self._fetch_capped(
            stmt, category="sleep_intervals", item_builder=_sleep_item
        )

    async def _fetch_timeline_weight_measurements(
        self,
        user_id: uuid.UUID,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[WeightMeasurementItem]:
        stmt = (
            select(
                WeightMeasurement.source_sample_id,
                WeightMeasurement.measured_at,
                WeightMeasurement.value_kg,
                WeightMeasurement.source,
            )
            .where(
                WeightMeasurement.user_id == user_id,
                WeightMeasurement.deleted_at.is_(None),
                WeightMeasurement.measured_at >= start_utc,
                WeightMeasurement.measured_at < end_utc,
            )
            .order_by(
                WeightMeasurement.measured_at.asc(),
                WeightMeasurement.source_sample_id.asc(),
            )
            .limit(MAX_TIMELINE_ITEMS_PER_CATEGORY + 1)
        )
        return await self._fetch_capped(
            stmt, category="weight_measurements", item_builder=_weight_item
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

    async def _load_aggregated_glucose(
        self,
        *,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        resolution: str,
    ) -> _AggregatedGlucose:
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
        return _AggregatedGlucose(
            source_record_count=source_record_count,
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
