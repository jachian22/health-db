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
from mcp_service.constants import DEFAULT_MEAL_LIMIT, DEFAULT_QUERY_TIMEZONE, MAX_MEAL_LIMIT
from mcp_service.errors import QueryAPIError, ToolError
from mcp_service.logging import current_request_id, log_request
from mcp_service.models import (
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    MealsResponse,
    coverage_record_count,
    enforce_glucose_range_limit,
    parse_timezone,
    summary_record_count,
    to_iso8601,
    validate_meal_limit,
    validate_time_range,
)

COVERAGE_DESCRIPTION = (
    "Call this first when exploring an unfamiliar date range. "
    "It returns coverage counts and first/last timestamps for glucose, meals, "
    "workouts, sleep intervals, and weight measurements. "
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

Start = Annotated[
    datetime, Field(description="Inclusive range start (ISO-8601 with timezone)")
]
End = Annotated[
    datetime, Field(description="Exclusive range end (ISO-8601 with timezone)")
]
Timezone = Annotated[
    str, Field(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})")
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
            "Typical workflow: get_data_coverage, then get_meals, "
            "then get_glucose_series, then get_glucose_summary. "
            "Always use explicit timezone-aware start/end. "
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
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_MEAL_LIMIT,
                description=(
                    f"Page size (default {DEFAULT_MEAL_LIMIT}, maximum {MAX_MEAL_LIMIT})"
                ),
            ),
        ] = DEFAULT_MEAL_LIMIT,
        cursor: Annotated[
            str | None,
            Field(description="Opaque pagination cursor from a prior next_cursor"),
        ] = None,
    ) -> MealsResponse:
        async def _call() -> MealsResponse:
            start_utc, end_utc, tz = _window(start, end, timezone)
            resolved_limit = validate_meal_limit(limit)
            return await query_client.get_meals(
                start=start_utc,
                end=end_utc,
                timezone=tz,
                limit=resolved_limit,
                cursor=cursor,
            )

        return await _run_tool(
            tool_name="get_meals",
            start=start,
            end=end,
            timezone=timezone,
            call=_call,
            count_of=lambda r: r.record_count,
            truncated_of=lambda r: r.truncated,
        )

    return mcp
