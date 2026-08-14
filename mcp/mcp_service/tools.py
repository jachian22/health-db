"""Read-only MCP tools mapped 1:1 onto Query API v1."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal, Protocol

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_service.config import Settings
from mcp_service.constants import (
    DEFAULT_GLUCOSE_LOOKBACK_HOURS,
    DEFAULT_MEAL_LOOKBACK_DAYS,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    DEFAULT_SLEEP_LOOKBACK_HOURS,
    MAX_GLUCOSE_LOOKBACK_HOURS,
    MAX_MEAL_LOOKBACK_DAYS,
    MAX_PAGE_LIMIT,
    MAX_SLEEP_LOOKBACK_HOURS,
    MAX_SLEEP_RANGE_DAYS,
    MAX_TIMELINE_RANGE_HOURS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
)
from mcp_service.errors import QueryAPIError, ToolError
from mcp_service.logging import current_request_id, log_request
from mcp_service.models import (
    ContextSnapshotResponse,
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    LastLoggedMealResponse,
    MealsResponse,
    PersonalTimelineResponse,
    SleepIntervalsResponse,
    WeightMeasurementsResponse,
    WorkoutsResponse,
    coverage_record_count,
    enforce_glucose_range_limit,
    enforce_max_range_days,
    enforce_max_range_hours,
    parse_bound_timestamp,
    parse_timezone,
    summary_record_count,
    timeline_record_count,
    to_iso8601,
    validate_lookback,
    validate_page_limit,
    validate_time_range,
)

COVERAGE_DESCRIPTION = (
    "Call this first when exploring an unfamiliar date range. "
    "It returns coverage counts and first/last timestamps for glucose, meals, "
    "workouts, sleep intervals, and weight measurements. "
    "Workouts and sleep intervals use interval overlap "
    "(start_time < end AND end_time > start); first_at / last_at are min/max "
    "stored start_time among included records. "
    "Glucose, meals, and weight measurements count timestamps in [start, end). "
    "It does not return raw health data."
)

GLUCOSE_SERIES_DESCRIPTION = (
    "Return bounded raw or aggregated CGM/glucose data for a chart or analysis. "
    "Query API limits: raw: maximum 7 days; 5m: maximum 31 days; "
    "15m: maximum 90 days; hourly: maximum 365 days. "
    "Raw points are source observations. "
    "Aggregated points contain bucket mean/min/max/sample_count. "
    "Do not treat an aggregate as a raw CGM reading."
)

GLUCOSE_SUMMARY_DESCRIPTION = (
    "Return descriptive glucose statistics for an explicit window without returning "
    "the full glucose series. Returns descriptive statistics only. "
    "It does not provide medical advice, diagnosis, risk scoring, or clinical interpretation. "
    "Daily grouping uses the requested/default timezone."
)

MEALS_DESCRIPTION = (
    "Return manual meal events in an explicit time window for contextual analysis. "
    "Food strings are included for authenticated users. "
    "Meal notes are intentionally excluded. "
    "Historical/backfilled meals appear according to meal_completed_at. "
    "Use returned next_cursor to fetch later pages only when necessary."
)

WORKOUTS_DESCRIPTION = (
    "Return ingest-accepted (Strava) workout intervals that overlap an explicit "
    "[start, end) window (start_time < end AND end_time > start). "
    "Native Apple Health duplicates are rejected at ingest and are not listed. "
    f"Maximum window: {MAX_WORKOUT_RANGE_DAYS} days. "
    "Timestamps are original stored UTC instants and are not clipped. "
    "Fields: id, start_time, end_time, sport, distance_meters, duration_minutes, source. "
    "source is the stored provenance value (typically apple_health). "
    "Heart rate, active energy, pace, elevation, metadata, and source_name are excluded. "
    "Do not give medical advice, diagnosis, training-load, readiness, or clinical interpretation. "
    "Use returned next_cursor to fetch later pages only when necessary."
)

SLEEP_INTERVALS_DESCRIPTION = (
    "Return raw synced sleep intervals that overlap an explicit [start, end) window "
    "(start_time < end AND end_time > start). "
    f"Maximum window: {MAX_SLEEP_RANGE_DAYS} days. "
    "Intervals are not sessionized, deduplicated, or stage-remapped. "
    "Adjacent and overlapping intervals are preserved exactly as stored. "
    "Timestamps are original stored UTC instants and are not clipped. "
    "Do not infer sleep quality, readiness, or give medical advice or clinical interpretation. "
    "Use returned next_cursor to fetch later pages only when necessary."
)

WEIGHT_MEASUREMENTS_DESCRIPTION = (
    "Return weight measurements in an explicit [start, end) window "
    "(start <= measured_at < end). "
    f"Maximum window: {MAX_WEIGHT_RANGE_DAYS} days. "
    "Values are kilograms (value_kg); pounds are not returned or converted. "
    "Do not give trend diagnosis, body-composition interpretation, medical advice, "
    "or clinical interpretation. "
    "Use returned next_cursor to fetch later pages only when necessary."
)

LAST_LOGGED_MEAL_DESCRIPTION = (
    "Return the latest logged meal at or before a required timezone-aware anchor. "
    "Foods are included. Meal notes are excluded. "
    f"lookback_days defaults to {DEFAULT_MEAL_LOOKBACK_DAYS} and cannot exceed "
    f"{MAX_MEAL_LOOKBACK_DAYS}. "
    "Time since last logged meal is based only on logged meals. "
    "It does not establish fasting or account for unlogged food. "
    "It provides no medical advice, diagnosis, or clinical interpretation."
)

CONTEXT_SNAPSHOT_DESCRIPTION = (
    "Return bounded, evidence-only context around a required timezone-aware anchor. "
    "Includes the latest logged meal and foods when present (notes excluded), "
    "the most recent completed workout, a compact raw-sleep aggregate "
    "(not a sleep session or stage list), the most recent weight measurement, "
    "glucose coverage, and a descriptive overall glucose summary. "
    "It does not return a glucose series. "
    "Time since last logged meal does not confirm fasting. "
    "It does not diagnose, infer symptoms or causality, assess safety or readiness, "
    "or give medical advice or clinical interpretation."
)

PERSONAL_TIMELINE_DESCRIPTION = (
    "Return one bounded, visualization-ready historical timeline for an explicit "
    "[start, end) window (maximum 72 elapsed hours). Includes meals with foods "
    "(notes excluded), workouts, raw sleep intervals, weight measurements, "
    "15-minute mean/min/max glucose, and category coverage. Sleep is not "
    "sessionized. Glucose resolution is fixed at 15m and is not client-selectable. "
    "This tool reports recorded data only; it does not diagnose, infer causes or "
    "symptoms, assess safety, or give medical advice."
)

Start = Annotated[
    datetime, Field(description="Inclusive range start (ISO-8601 with timezone)")
]
End = Annotated[
    datetime, Field(description="Exclusive range end (ISO-8601 with timezone)")
]
Anchor = Annotated[
    datetime, Field(description="Timezone-aware ISO-8601 timestamp")
]
Timezone = Annotated[
    str, Field(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})")
]
Limit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_PAGE_LIMIT,
        description=f"Page size (default {DEFAULT_PAGE_LIMIT}, maximum {MAX_PAGE_LIMIT})",
    ),
]
Cursor = Annotated[
    str | None,
    Field(description="Opaque pagination cursor from a prior next_cursor"),
]


class QueryClient(Protocol):
    async def check_ready(self) -> bool: ...

    async def aclose(self) -> None: ...

    async def get_coverage(
        self, *, start: datetime, end: datetime, timezone: str
    ) -> CoverageResponse: ...

    async def get_glucose_series(
        self, *, start: datetime, end: datetime, resolution: str, timezone: str
    ) -> GlucoseSeriesResponse: ...

    async def get_glucose_summary(
        self, *, start: datetime, end: datetime, bucket: str, timezone: str
    ) -> GlucoseSummaryResponse: ...

    async def get_meals(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> MealsResponse: ...

    async def get_workouts(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> WorkoutsResponse: ...

    async def get_sleep_intervals(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> SleepIntervalsResponse: ...

    async def get_weight_measurements(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> WeightMeasurementsResponse: ...

    async def get_last_logged_meal(
        self,
        *,
        anchor: datetime,
        timezone: str,
        lookback_days: int,
    ) -> LastLoggedMealResponse: ...

    async def get_context_snapshot(
        self,
        *,
        anchor: datetime,
        timezone: str,
        meal_lookback_days: int,
        sleep_lookback_hours: int,
        glucose_lookback_hours: int,
    ) -> ContextSnapshotResponse: ...

    async def get_personal_timeline(
        self, *, start: datetime, end: datetime, timezone: str
    ) -> PersonalTimelineResponse: ...


def _window(start: datetime, end: datetime, timezone: str) -> tuple[datetime, datetime, str]:
    start_utc, end_utc = validate_time_range(start, end)
    return start_utc, end_utc, parse_timezone(timezone)


def _iso(value: datetime) -> str | None:
    return to_iso8601(value) if value.tzinfo else None


@dataclass(frozen=True)
class ToolLogMeta:
    """Safe tool-call log fields. Never log health values or identifiers.

    ``record_count`` is tool-specific: list tools use item count, glucose series
    uses returned points, and get_personal_timeline uses the sum of the four
    event arrays (not glucose bucket count).
    """

    start: str | None = None
    end: str | None = None
    timezone: str | None = None
    resolution: str | None = None
    bucket: str | None = None
    record_count: int | None = None
    truncated: bool | None = None
    anchor: str | None = None
    lookback_days: int | None = None
    meal_lookback_days: int | None = None
    sleep_lookback_hours: int | None = None
    glucose_lookback_hours: int | None = None


def _bounds_meta(
    start: datetime,
    end: datetime,
    timezone: str,
    *,
    record_count: int | None = None,
    truncated: bool | None = None,
    resolution: str | None = None,
    bucket: str | None = None,
) -> ToolLogMeta:
    return ToolLogMeta(
        start=_iso(start),
        end=_iso(end),
        timezone=timezone,
        record_count=record_count,
        truncated=truncated,
        resolution=resolution,
        bucket=bucket,
    )


def _last_meal_log_meta(result: LastLoggedMealResponse) -> ToolLogMeta:
    window_start = result.anchor - timedelta(days=result.lookback_days)
    return ToolLogMeta(
        start=to_iso8601(window_start),
        end=to_iso8601(result.anchor),
        timezone=result.timezone,
        record_count=0 if result.meal is None else 1,
        anchor=to_iso8601(result.anchor),
        lookback_days=result.lookback_days,
    )


def _snapshot_log_meta(result: ContextSnapshotResponse) -> ToolLogMeta:
    return ToolLogMeta(
        timezone=result.timezone,
        record_count=result.recent_sleep_intervals.record_count,
        anchor=to_iso8601(result.anchor),
        meal_lookback_days=result.meal_lookback_days,
        sleep_lookback_hours=result.sleep_lookback_hours,
        glucose_lookback_hours=result.glucose_lookback_hours,
    )


def _log_tool(
    *,
    tool_name: str,
    outcome: str,
    latency_ms: float,
    meta: ToolLogMeta,
    error_code: str | None = None,
) -> None:
    log_request(
        request_id=current_request_id(),
        category="tools/call",
        tool_name=tool_name,
        outcome=outcome,
        principal="mcp_caller",
        start=meta.start,
        end=meta.end,
        timezone=meta.timezone,
        resolution=meta.resolution,
        bucket=meta.bucket,
        record_count=meta.record_count,
        truncated=meta.truncated,
        latency_ms=latency_ms,
        error_code=error_code,
        anchor=meta.anchor,
        lookback_days=meta.lookback_days,
        meal_lookback_days=meta.meal_lookback_days,
        sleep_lookback_hours=meta.sleep_lookback_hours,
        glucose_lookback_hours=meta.glucose_lookback_hours,
    )


async def _run_tool[T](
    *,
    tool_name: str,
    call: Callable[[], Awaitable[T]],
    success_meta: Callable[[T], ToolLogMeta],
    error_meta: ToolLogMeta,
) -> T:
    started = time.perf_counter()
    try:
        result = await call()
        latency_ms = (time.perf_counter() - started) * 1000
        _log_tool(
            tool_name=tool_name,
            outcome="ok",
            latency_ms=latency_ms,
            meta=success_meta(result),
        )
        return result
    except ToolError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        _log_tool(
            tool_name=tool_name,
            outcome="validation_error",
            latency_ms=latency_ms,
            meta=error_meta,
            error_code=exc.code,
        )
        request_id = current_request_id()
        if "request_id" not in exc.extra:
            raise ToolError(exc.code, exc.message, request_id=request_id, **exc.extra) from exc
        raise
    except QueryAPIError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        _log_tool(
            tool_name=tool_name,
            outcome=exc.code.lower(),
            latency_ms=latency_ms,
            meta=error_meta,
            error_code=exc.code,
        )
        raise exc.to_tool_error(request_id=current_request_id()) from exc


async def _run_paged_tool[T](
    *,
    tool_name: str,
    start: datetime,
    end: datetime,
    timezone: str,
    limit: int,
    call: Callable[[datetime, datetime, str, int], Awaitable[T]],
    max_days: int | None = None,
    range_label: str | None = None,
) -> T:
    async def _call() -> T:
        start_utc, end_utc, tz = _window(start, end, timezone)
        if max_days is not None:
            enforce_max_range_days(
                start_utc,
                end_utc,
                max_days=max_days,
                label=range_label or "Query",
            )
        return await call(start_utc, end_utc, tz, validate_page_limit(limit))

    return await _run_tool(
        tool_name=tool_name,
        call=_call,
        success_meta=lambda result: _bounds_meta(
            start,
            end,
            timezone,
            record_count=result.record_count,  # type: ignore[attr-defined]
            truncated=result.truncated,  # type: ignore[attr-defined]
        ),
        error_meta=_bounds_meta(start, end, timezone),
    )


def build_mcp_server(
    settings: Settings,
    query_client: QueryClient,
    *,
    lifespan: Callable[[MCPServer], AbstractAsyncContextManager[object]] | None = None,
) -> MCPServer:
    mcp = MCPServer(
        settings.mcp_service_name,
        version=settings.mcp_service_version,
        instructions=(
            "Read-only personal health-data tools. "
            "Typical workflow: get_data_coverage, then get_meals / get_workouts / "
            "get_sleep_intervals / get_weight_measurements, "
            "then get_glucose_series, then get_glucose_summary. "
            "Use get_last_logged_meal or build_context_snapshot for anchor-relative context. "
            "For a bounded historical replay window, prefer get_personal_timeline rather "
            "than manually combining many category tools. "
            "Always use explicit timezone-aware start/end or a timezone-aware anchor. "
            "Workouts and sleep intervals use overlap inclusion "
            "(start_time < end AND end_time > start) for both coverage and lists. "
            "Do not give medical advice, diagnosis, or clinical interpretation."
        ),
        lifespan=lifespan,
    )
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    @mcp.tool(name="get_data_coverage", description=COVERAGE_DESCRIPTION, annotations=read_only)
    async def get_data_coverage(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
    ) -> CoverageResponse:
        async def _call() -> CoverageResponse:
            start_utc, end_utc, tz = _window(start, end, timezone)
            return await query_client.get_coverage(start=start_utc, end=end_utc, timezone=tz)

        return await _run_tool(
            tool_name="get_data_coverage",
            call=_call,
            success_meta=lambda result: _bounds_meta(
                start, end, timezone, record_count=coverage_record_count(result)
            ),
            error_meta=_bounds_meta(start, end, timezone),
        )

    @mcp.tool(
        name="get_glucose_series",
        description=GLUCOSE_SERIES_DESCRIPTION,
        annotations=read_only,
    )
    async def get_glucose_series(
        start: Start,
        end: End,
        resolution: Annotated[
            Literal["raw", "5m", "15m", "hourly"],
            Field(description="Series resolution: raw, 5m, 15m, or hourly (default 15m)"),
        ] = "15m",
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
    ) -> GlucoseSeriesResponse:
        async def _call() -> GlucoseSeriesResponse:
            start_utc, end_utc, tz = _window(start, end, timezone)
            enforce_glucose_range_limit(start_utc, end_utc, resolution)
            return await query_client.get_glucose_series(
                start=start_utc, end=end_utc, resolution=resolution, timezone=tz
            )

        return await _run_tool(
            tool_name="get_glucose_series",
            call=_call,
            success_meta=lambda result: _bounds_meta(
                start,
                end,
                timezone,
                record_count=result.returned_point_count,
                truncated=result.truncated,
                resolution=resolution,
            ),
            error_meta=_bounds_meta(start, end, timezone, resolution=resolution),
        )

    @mcp.tool(
        name="get_glucose_summary",
        description=GLUCOSE_SUMMARY_DESCRIPTION,
        annotations=read_only,
    )
    async def get_glucose_summary(
        start: Start,
        end: End,
        bucket: Annotated[
            Literal["overall", "daily"],
            Field(description="Summary bucketing mode: overall or daily (default overall)"),
        ] = "overall",
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
    ) -> GlucoseSummaryResponse:
        async def _call() -> GlucoseSummaryResponse:
            start_utc, end_utc, tz = _window(start, end, timezone)
            return await query_client.get_glucose_summary(
                start=start_utc, end=end_utc, bucket=bucket, timezone=tz
            )

        return await _run_tool(
            tool_name="get_glucose_summary",
            call=_call,
            success_meta=lambda result: _bounds_meta(
                start,
                end,
                timezone,
                record_count=summary_record_count(result),
                bucket=bucket,
            ),
            error_meta=_bounds_meta(start, end, timezone, bucket=bucket),
        )

    @mcp.tool(name="get_meals", description=MEALS_DESCRIPTION, annotations=read_only)
    async def get_meals(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        limit: Limit = DEFAULT_PAGE_LIMIT,
        cursor: Cursor = None,
    ) -> MealsResponse:
        return await _run_paged_tool(
            tool_name="get_meals",
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            call=lambda start_utc, end_utc, tz, resolved_limit: query_client.get_meals(
                start=start_utc,
                end=end_utc,
                timezone=tz,
                limit=resolved_limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(name="get_workouts", description=WORKOUTS_DESCRIPTION, annotations=read_only)
    async def get_workouts(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        limit: Limit = DEFAULT_PAGE_LIMIT,
        cursor: Cursor = None,
    ) -> WorkoutsResponse:
        return await _run_paged_tool(
            tool_name="get_workouts",
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_WORKOUT_RANGE_DAYS,
            range_label="Workout",
            call=lambda start_utc, end_utc, tz, resolved_limit: query_client.get_workouts(
                start=start_utc,
                end=end_utc,
                timezone=tz,
                limit=resolved_limit,
                cursor=cursor,
            ),
        )

    @mcp.tool(
        name="get_sleep_intervals",
        description=SLEEP_INTERVALS_DESCRIPTION,
        annotations=read_only,
    )
    async def get_sleep_intervals(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        limit: Limit = DEFAULT_PAGE_LIMIT,
        cursor: Cursor = None,
    ) -> SleepIntervalsResponse:
        return await _run_paged_tool(
            tool_name="get_sleep_intervals",
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_SLEEP_RANGE_DAYS,
            range_label="Sleep interval",
            call=lambda start_utc, end_utc, tz, resolved_limit: (
                query_client.get_sleep_intervals(
                    start=start_utc,
                    end=end_utc,
                    timezone=tz,
                    limit=resolved_limit,
                    cursor=cursor,
                )
            ),
        )

    @mcp.tool(
        name="get_weight_measurements",
        description=WEIGHT_MEASUREMENTS_DESCRIPTION,
        annotations=read_only,
    )
    async def get_weight_measurements(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        limit: Limit = DEFAULT_PAGE_LIMIT,
        cursor: Cursor = None,
    ) -> WeightMeasurementsResponse:
        return await _run_paged_tool(
            tool_name="get_weight_measurements",
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            max_days=MAX_WEIGHT_RANGE_DAYS,
            range_label="Weight measurement",
            call=lambda start_utc, end_utc, tz, resolved_limit: (
                query_client.get_weight_measurements(
                    start=start_utc,
                    end=end_utc,
                    timezone=tz,
                    limit=resolved_limit,
                    cursor=cursor,
                )
            ),
        )

    @mcp.tool(
        name="get_last_logged_meal",
        description=LAST_LOGGED_MEAL_DESCRIPTION,
        annotations=read_only,
    )
    async def get_last_logged_meal(
        anchor: Anchor,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        lookback_days: Annotated[
            int,
            Field(
                description=(
                    "Elapsed-day lookback "
                    f"(default {DEFAULT_MEAL_LOOKBACK_DAYS}, max {MAX_MEAL_LOOKBACK_DAYS})"
                )
            ),
        ] = DEFAULT_MEAL_LOOKBACK_DAYS,
    ) -> LastLoggedMealResponse:
        async def _call() -> LastLoggedMealResponse:
            anchor_utc = parse_bound_timestamp(anchor, field_name="anchor")
            tz = parse_timezone(timezone)
            resolved = validate_lookback(
                lookback_days,
                default=DEFAULT_MEAL_LOOKBACK_DAYS,
                max_value=MAX_MEAL_LOOKBACK_DAYS,
                unit="days",
                field_name="lookback_days",
                label="Meal lookback",
            )
            return await query_client.get_last_logged_meal(
                anchor=anchor_utc, timezone=tz, lookback_days=resolved
            )

        return await _run_tool(
            tool_name="get_last_logged_meal",
            call=_call,
            success_meta=_last_meal_log_meta,
            error_meta=ToolLogMeta(
                timezone=timezone,
                anchor=_iso(anchor),
                lookback_days=lookback_days,
            ),
        )

    @mcp.tool(
        name="build_context_snapshot",
        description=CONTEXT_SNAPSHOT_DESCRIPTION,
        annotations=read_only,
    )
    async def build_context_snapshot(
        anchor: Anchor,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
        meal_lookback_days: Annotated[
            int,
            Field(
                description=(
                    "Elapsed-day meal lookback "
                    f"(default {DEFAULT_MEAL_LOOKBACK_DAYS}, max {MAX_MEAL_LOOKBACK_DAYS})"
                )
            ),
        ] = DEFAULT_MEAL_LOOKBACK_DAYS,
        sleep_lookback_hours: Annotated[
            int,
            Field(
                description=(
                    "Elapsed-hour sleep lookback "
                    f"(default {DEFAULT_SLEEP_LOOKBACK_HOURS}, max {MAX_SLEEP_LOOKBACK_HOURS})"
                )
            ),
        ] = DEFAULT_SLEEP_LOOKBACK_HOURS,
        glucose_lookback_hours: Annotated[
            int,
            Field(
                description=(
                    "Elapsed-hour glucose lookback "
                    f"(default {DEFAULT_GLUCOSE_LOOKBACK_HOURS}, max {MAX_GLUCOSE_LOOKBACK_HOURS})"
                )
            ),
        ] = DEFAULT_GLUCOSE_LOOKBACK_HOURS,
    ) -> ContextSnapshotResponse:
        async def _call() -> ContextSnapshotResponse:
            anchor_utc = parse_bound_timestamp(anchor, field_name="anchor")
            tz = parse_timezone(timezone)
            meal_lb = validate_lookback(
                meal_lookback_days,
                default=DEFAULT_MEAL_LOOKBACK_DAYS,
                max_value=MAX_MEAL_LOOKBACK_DAYS,
                unit="days",
                field_name="meal_lookback_days",
                label="Meal lookback",
            )
            sleep_lb = validate_lookback(
                sleep_lookback_hours,
                default=DEFAULT_SLEEP_LOOKBACK_HOURS,
                max_value=MAX_SLEEP_LOOKBACK_HOURS,
                unit="hours",
                field_name="sleep_lookback_hours",
                label="Sleep lookback",
            )
            glucose_lb = validate_lookback(
                glucose_lookback_hours,
                default=DEFAULT_GLUCOSE_LOOKBACK_HOURS,
                max_value=MAX_GLUCOSE_LOOKBACK_HOURS,
                unit="hours",
                field_name="glucose_lookback_hours",
                label="Glucose lookback",
            )
            return await query_client.get_context_snapshot(
                anchor=anchor_utc,
                timezone=tz,
                meal_lookback_days=meal_lb,
                sleep_lookback_hours=sleep_lb,
                glucose_lookback_hours=glucose_lb,
            )

        return await _run_tool(
            tool_name="build_context_snapshot",
            call=_call,
            success_meta=_snapshot_log_meta,
            error_meta=ToolLogMeta(
                timezone=timezone,
                anchor=_iso(anchor),
                meal_lookback_days=meal_lookback_days,
                sleep_lookback_hours=sleep_lookback_hours,
                glucose_lookback_hours=glucose_lookback_hours,
            ),
        )

    @mcp.tool(
        name="get_personal_timeline",
        description=PERSONAL_TIMELINE_DESCRIPTION,
        annotations=read_only,
    )
    async def get_personal_timeline(
        start: Start,
        end: End,
        timezone: Timezone = DEFAULT_QUERY_TIMEZONE,
    ) -> PersonalTimelineResponse:
        async def _call() -> PersonalTimelineResponse:
            start_utc, end_utc, tz = _window(start, end, timezone)
            enforce_max_range_hours(
                start_utc,
                end_utc,
                max_hours=MAX_TIMELINE_RANGE_HOURS,
                label="Personal timeline",
            )
            return await query_client.get_personal_timeline(
                start=start_utc, end=end_utc, timezone=tz
            )

        return await _run_tool(
            tool_name="get_personal_timeline",
            call=_call,
            success_meta=lambda result: _bounds_meta(
                start,
                end,
                timezone,
                record_count=timeline_record_count(result),
                truncated=False,
                resolution="15m",
            ),
            error_meta=_bounds_meta(start, end, timezone, resolution="15m"),
        )

    return mcp
