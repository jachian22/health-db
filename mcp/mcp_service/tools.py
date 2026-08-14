"""Read-only MCP tools mapped 1:1 onto Query API v1."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated, Literal, Protocol

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_service.config import Settings
from mcp_service.constants import (
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_PAGE_LIMIT,
    MAX_SLEEP_RANGE_DAYS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
)
from mcp_service.errors import QueryAPIError, ToolError
from mcp_service.logging import current_request_id, log_request
from mcp_service.models import (
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    MealsResponse,
    SleepIntervalsResponse,
    WeightMeasurementsResponse,
    WorkoutsResponse,
    coverage_record_count,
    enforce_glucose_range_limit,
    enforce_max_range_days,
    parse_timezone,
    summary_record_count,
    to_iso8601,
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
    "Return raw workout intervals that overlap an explicit [start, end) window "
    "(start_time < end AND end_time > start). "
    f"Maximum window: {MAX_WORKOUT_RANGE_DAYS} days. "
    "Timestamps are original stored UTC instants and are not clipped. "
    "Fields: id, start_time, end_time, sport, distance_meters, duration_minutes, source. "
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

Start = Annotated[
    datetime, Field(description="Inclusive range start (ISO-8601 with timezone)")
]
End = Annotated[
    datetime, Field(description="Exclusive range end (ISO-8601 with timezone)")
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


def _window(start: datetime, end: datetime, timezone: str) -> tuple[datetime, datetime, str]:
    start_utc, end_utc = validate_time_range(start, end)
    return start_utc, end_utc, parse_timezone(timezone)


async def _run_tool[T](
    *,
    tool_name: str,
    start: datetime,
    end: datetime,
    timezone: str,
    call: Callable[[], Awaitable[T]],
    count_of: Callable[[T], int],
    truncated_of: Callable[[T], bool] | None = None,
    resolution: str | None = None,
    bucket: str | None = None,
) -> T:
    request_id = current_request_id()
    started = time.perf_counter()
    try:
        result = await call()
        latency_ms = (time.perf_counter() - started) * 1000
        truncated = truncated_of(result) if truncated_of else None
        log_request(
            request_id=request_id,
            category="tools/call",
            tool_name=tool_name,
            outcome="ok",
            principal="mcp_caller",
            start=to_iso8601(start),
            end=to_iso8601(end),
            timezone=timezone,
            resolution=resolution,
            bucket=bucket,
            record_count=count_of(result),
            truncated=truncated,
            latency_ms=latency_ms,
        )
        return result
    except ToolError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        log_request(
            request_id=request_id,
            category="tools/call",
            tool_name=tool_name,
            outcome="validation_error",
            principal="mcp_caller",
            start=to_iso8601(start) if start.tzinfo else None,
            end=to_iso8601(end) if end.tzinfo else None,
            timezone=timezone,
            resolution=resolution,
            bucket=bucket,
            latency_ms=latency_ms,
            error_code=exc.code,
        )
        if "request_id" not in exc.extra:
            raise ToolError(exc.code, exc.message, request_id=request_id, **exc.extra) from exc
        raise
    except QueryAPIError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        log_request(
            request_id=request_id,
            category="tools/call",
            tool_name=tool_name,
            outcome=exc.code.lower(),
            principal="mcp_caller",
            start=to_iso8601(start),
            end=to_iso8601(end),
            timezone=timezone,
            resolution=resolution,
            bucket=bucket,
            latency_ms=latency_ms,
            error_code=exc.code,
        )
        raise exc.to_tool_error(request_id=request_id) from exc


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
        start=start,
        end=end,
        timezone=timezone,
        call=_call,
        count_of=lambda result: result.record_count,  # type: ignore[attr-defined]
        truncated_of=lambda result: result.truncated,  # type: ignore[attr-defined]
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
            "Always use explicit timezone-aware start/end. "
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
            start=start,
            end=end,
            timezone=timezone,
            call=_call,
            count_of=coverage_record_count,
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
            start=start,
            end=end,
            timezone=timezone,
            resolution=resolution,
            call=_call,
            count_of=lambda r: r.returned_point_count,
            truncated_of=lambda r: r.truncated,
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
            start=start,
            end=end,
            timezone=timezone,
            bucket=bucket,
            call=_call,
            count_of=summary_record_count,
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

    return mcp
