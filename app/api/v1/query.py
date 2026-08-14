"""GET /v1/query/* — authenticated read-only health data Query API v1.

All endpoints require Authorization: Bearer <READ_API_KEY>, explicit bounded
time ranges, and return UTC source timestamps. Timezone is response metadata
(default America/New_York). Glucose daily summary is the exception: it uses
timezone for local-calendar grouping.

Resource protection is application-level only: hard date-range limits, list page
size, glucose point ceilings, timeline item caps, and Postgres statement_timeout
on read queries. There is no in-process rate limiter; rely on those caps (and
platform limits).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import DbSession
from app.core import (
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
    MAX_TIMELINE_ITEMS_PER_CATEGORY,
    MAX_TIMELINE_RANGE_HOURS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
    SNAPSHOT_WEIGHT_LOOKBACK_DAYS,
    SNAPSHOT_WORKOUT_LOOKBACK_DAYS,
)
from app.core.errors import AppError, ErrorResponse
from app.core.security import require_read_auth
from app.schemas.responses import (
    ContextSnapshotResponse,
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    LastLoggedMealResponse,
    MealsResponse,
    PagedResponse,
    PersonalTimelineResponse,
    SleepIntervalsResponse,
    WeightMeasurementsResponse,
    WorkoutsResponse,
)
from app.services.query_service import HealthDataQueryService

logger = logging.getLogger("app.query")

router = APIRouter(
    prefix="/v1/query",
    tags=["query"],
    dependencies=[Depends(require_read_auth)],
)

_ERROR_RESPONSES = {
    401: {
        "model": ErrorResponse,
        "description": "Missing or invalid READ_API_KEY",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or missing read credentials",
                    },
                    "request_id": "00000000-0000-0000-0000-000000000001",
                }
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "description": "Invalid time range, timezone, resolution, cursor, limit, lookback, or result size",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_time_range": {
                        "summary": "end not after start",
                        "value": {
                            "error": {
                                "code": "INVALID_TIME_RANGE",
                                "message": "end must be later than start",
                            },
                            "request_id": "00000000-0000-0000-0000-000000000001",
                        },
                    },
                    "range_too_large": {
                        "summary": "Glucose resolution span exceeded",
                        "value": {
                            "error": {
                                "code": "RANGE_TOO_LARGE",
                                "message": "Raw glucose queries are limited to 7 days",
                                "details": {"max_days": 7},
                            },
                            "request_id": "00000000-0000-0000-0000-000000000001",
                        },
                    },
                    "invalid_lookback": {
                        "summary": "Lookback is not a positive integer",
                        "value": {
                            "error": {
                                "code": "INVALID_LOOKBACK",
                                "message": "lookback_days must be a positive integer",
                            },
                            "request_id": "00000000-0000-0000-0000-000000000001",
                        },
                    },
                    "result_too_large": {
                        "summary": "Glucose point ceiling exceeded",
                        "value": {
                            "error": {
                                "code": "RESULT_TOO_LARGE",
                                "message": (
                                    "Glucose query matched more than 10000 points; "
                                    "narrow the time range or use a coarser resolution"
                                ),
                                "details": {"max_points": 10000},
                            },
                            "request_id": "00000000-0000-0000-0000-000000000001",
                        },
                    },
                }
            }
        },
    },
    500: {
        "model": ErrorResponse,
        "description": "Sanitized query failure",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "QUERY_FAILED",
                        "message": "The requested health data could not be retrieved",
                    },
                    "request_id": "00000000-0000-0000-0000-000000000001",
                }
            }
        },
    },
}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _qp(request: Request, name: str) -> str | None:
    value = request.query_params.get(name)
    return value if value else None


@dataclass
class QueryLogMeta:
    """Safe query-access log fields. Never log health values or identifiers.

    ``record_count`` is endpoint-specific: list pages use item count, glucose
    series uses returned points, and personal-timeline uses the sum of the four
    event arrays (meals, workouts, sleep_intervals, weight_measurements), not
    glucose bucket count.
    """

    start: str | datetime | None = None
    end: str | datetime | None = None
    timezone: str | None = None
    resolution: str | None = None
    bucket: str | None = None
    record_count: int | None = None
    truncated: bool | None = None
    anchor: str | datetime | None = None
    lookback_days: int | str | None = None
    meal_lookback_days: int | str | None = None
    sleep_lookback_hours: int | str | None = None
    glucose_lookback_hours: int | str | None = None


def _fmt_ts(value: str | datetime | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _error_log_meta(request: Request) -> QueryLogMeta:
    return QueryLogMeta(
        start=_qp(request, "start"),
        end=_qp(request, "end"),
        timezone=_qp(request, "timezone"),
        resolution=_qp(request, "resolution"),
        bucket=_qp(request, "bucket"),
        anchor=_qp(request, "anchor"),
        lookback_days=_qp(request, "lookback_days"),
        meal_lookback_days=_qp(request, "meal_lookback_days"),
        sleep_lookback_hours=_qp(request, "sleep_lookback_hours"),
        glucose_lookback_hours=_qp(request, "glucose_lookback_hours"),
    )


def _log_query(
    *,
    request: Request,
    route: str,
    status: int,
    meta: QueryLogMeta,
    latency_ms: float,
    error_code: str | None = None,
) -> None:
    extras: list[str] = []
    anchor_s = _fmt_ts(meta.anchor)
    if anchor_s is not None:
        extras.append(f"anchor={anchor_s}")
    if meta.lookback_days is not None:
        extras.append(f"lookback_days={meta.lookback_days}")
    if meta.meal_lookback_days is not None:
        extras.append(f"meal_lookback_days={meta.meal_lookback_days}")
    if meta.sleep_lookback_hours is not None:
        extras.append(f"sleep_lookback_hours={meta.sleep_lookback_hours}")
    if meta.glucose_lookback_hours is not None:
        extras.append(f"glucose_lookback_hours={meta.glucose_lookback_hours}")
    extra_s = (" " + " ".join(extras)) if extras else ""
    logger.info(
        "query_access request_id=%s route=%s status=%s principal=%s "
        "start=%s end=%s timezone=%s resolution=%s bucket=%s "
        "record_count=%s truncated=%s latency_ms=%.1f error_code=%s%s",
        _request_id(request),
        route,
        status,
        getattr(request.state, "auth_role", "read"),
        _fmt_ts(meta.start),
        _fmt_ts(meta.end),
        meta.timezone,
        meta.resolution,
        meta.bucket,
        meta.record_count,
        meta.truncated,
        latency_ms,
        error_code,
        extra_s,
    )


async def _execute[T](
    request: Request,
    route: str,
    coro: Awaitable[T],
    *,
    on_success: Callable[[T], QueryLogMeta],
) -> T:
    started = time.perf_counter()
    try:
        result = await coro
        latency_ms = (time.perf_counter() - started) * 1000
        _log_query(
            request=request,
            route=route,
            status=200,
            meta=on_success(result),
            latency_ms=latency_ms,
        )
        return result
    except AppError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        _log_query(
            request=request,
            route=route,
            status=exc.status_code,
            meta=_error_log_meta(request),
            latency_ms=latency_ms,
            error_code=exc.code,
        )
        raise
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "query_failed request_id=%s route=%s error_type=%s latency_ms=%.1f",
            _request_id(request),
            route,
            type(exc).__name__,
            latency_ms,
        )
        raise AppError(
            code="QUERY_FAILED",
            message="The requested health data could not be retrieved",
            status_code=500,
        ) from exc


ListStart = Annotated[
    datetime, Query(description="Inclusive range start (ISO-8601 with timezone)")
]
ListEnd = Annotated[
    datetime, Query(description="Exclusive range end (ISO-8601 with timezone)")
]
ListTimezone = Annotated[
    str,
    Query(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})"),
]
ListLimit = Annotated[
    int | None,
    Query(description=f"Page size (default {DEFAULT_PAGE_LIMIT}, max {MAX_PAGE_LIMIT})"),
]
ListCursor = Annotated[
    str | None,
    Query(description="HMAC-signed pagination cursor from a prior next_cursor"),
]


def _paged_success(result: PagedResponse[object]) -> QueryLogMeta:
    return QueryLogMeta(
        start=result.start,
        end=result.end,
        timezone=result.timezone,
        record_count=result.record_count,
        truncated=result.truncated,
    )


def _timeline_event_count(result: PersonalTimelineResponse) -> int:
    """Sum of the four event arrays; excludes glucose points."""
    return (
        len(result.meals)
        + len(result.workouts)
        + len(result.sleep_intervals)
        + len(result.weight_measurements)
    )


async def _execute_paged[T](request: Request, route: str, coro: Awaitable[T]) -> T:
    return await _execute(request, route, coro, on_success=_paged_success)


@router.get(
    "/coverage",
    response_model=CoverageResponse,
    summary="Dataset coverage for a bounded window",
    description=(
        "Read-only discovery endpoint. Reports whether glucose, meals, workouts, "
        "sleep_intervals, and weight_measurements exist in a bounded half-open "
        "`[start, end)` window for the fixed personal principal.\n\n"
        "Workouts and sleep_intervals use interval overlap "
        "(`start_time < end AND end_time > start`). "
        "`first_at` / `last_at` are min/max stored `start_time` among included records.\n\n"
        "Glucose, meals, and weight_measurements count timestamps in `[start, end)` "
        "(`sample_time`, `meal_completed_at`, `measured_at`).\n\n"
        "Empty categories return `count: 0` with null `first_at` / `last_at`. "
        "Raw record counts only; no medical advice or clinical interpretation."
    ),
    responses=_ERROR_RESPONSES,
    response_model_exclude_none=False,
)
async def get_coverage(
    request: Request,
    db: DbSession,
    start: Annotated[
        datetime,
        Query(description="Inclusive range start (ISO-8601 with timezone)"),
    ],
    end: Annotated[
        datetime,
        Query(description="Exclusive range end (ISO-8601 with timezone); must be after start"),
    ],
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone for response metadata (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
) -> CoverageResponse:
    service = HealthDataQueryService(db)
    return await _execute(
        request,
        "/v1/query/coverage",
        service.coverage(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
        ),
        on_success=lambda r: QueryLogMeta(start=r.start, end=r.end, timezone=r.timezone),
    )


@router.get(
    "/glucose/series",
    response_model=GlucoseSeriesResponse,
    summary="Glucose time series (raw or aggregated)",
    description=(
        "Read-only glucose series over a half-open `[start, end)` window.\n\n"
        "All query endpoints are read-only and require explicit bounded time ranges.\n\n"
        "**Resolutions and hard maximum spans** (oversized ranges are rejected; "
        "resolution is never silently changed):\n"
        "- `raw` — max 7 days; source observations as `{timestamp, value_mg_dl}` (mg/dL)\n"
        "- `5m` — max 31 days; UTC-aligned buckets with mean/min/max/sample_count\n"
        "- `15m` — max 90 days (default)\n"
        "- `hourly` — max 365 days\n\n"
        "Hard ceiling: at most 10000 returned points (raw or buckets); excess raises "
        "`RESULT_TOO_LARGE`. Empty buckets are omitted (no interpolation). "
        "Aggregation for non-raw responses is `mean_min_max`. Internal IDs, source "
        "sample IDs, and metadata are never returned."
    ),
    responses={
        **_ERROR_RESPONSES,
        200: {
            "description": "Glucose series points",
            "content": {
                "application/json": {
                    "example": {
                        "request_id": "00000000-0000-0000-0000-000000000001",
                        "start": "2026-08-01T00:00:00Z",
                        "end": "2026-08-08T00:00:00Z",
                        "timezone": "America/New_York",
                        "resolution": "15m",
                        "aggregation": "mean_min_max",
                        "source_record_count": 2,
                        "returned_point_count": 1,
                        "truncated": False,
                        "data_fresh_through": "2026-08-05T14:30:00Z",
                        "points": [
                            {
                                "start": "2026-08-05T14:15:00Z",
                                "end": "2026-08-05T14:30:00Z",
                                "mean_mg_dl": 96.0,
                                "min_mg_dl": 96.0,
                                "max_mg_dl": 96.0,
                                "sample_count": 1,
                            }
                        ],
                    }
                }
            },
        },
    },
)
async def get_glucose_series(
    request: Request,
    db: DbSession,
    start: Annotated[
        datetime, Query(description="Inclusive range start (ISO-8601 with timezone)")
    ],
    end: Annotated[
        datetime, Query(description="Exclusive range end (ISO-8601 with timezone)")
    ],
    resolution: Annotated[
        str,
        Query(description="Series resolution: raw, 5m, 15m, or hourly (default 15m)"),
    ] = "15m",
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
) -> GlucoseSeriesResponse:
    service = HealthDataQueryService(db)
    return await _execute(
        request,
        "/v1/query/glucose/series",
        service.glucose_series(
            request_id=_request_id(request),
            start=start,
            end=end,
            resolution=resolution,
            timezone=timezone,
        ),
        on_success=lambda r: QueryLogMeta(
            start=r.start,
            end=r.end,
            timezone=r.timezone,
            record_count=r.returned_point_count,
            truncated=r.truncated,
            resolution=r.resolution,
        ),
    )


@router.get(
    "/glucose/summary",
    response_model=GlucoseSummaryResponse,
    summary="Descriptive glucose summary",
    description=(
        "Read-only non-clinical glucose statistics over `[start, end)`.\n\n"
        "All query endpoints are read-only and require explicit bounded time ranges.\n\n"
        "- `bucket=overall` (default): sample_count, first/last, min/max/mean/median\n"
        "- `bucket=daily`: one row per local calendar date in the requested timezone "
        "(DST-aware). Days with zero samples are omitted.\n\n"
        "Empty overall windows return zero counts with null metrics. No risk scores, "
        "time-in-range, or medical interpretation fields are included."
    ),
    responses=_ERROR_RESPONSES,
)
async def get_glucose_summary(
    request: Request,
    db: DbSession,
    start: Annotated[
        datetime, Query(description="Inclusive range start (ISO-8601 with timezone)")
    ],
    end: Annotated[
        datetime, Query(description="Exclusive range end (ISO-8601 with timezone)")
    ],
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone for daily grouping (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
    bucket: Annotated[
        str,
        Query(description="Summary bucketing mode: overall or daily (default overall)"),
    ] = "overall",
) -> GlucoseSummaryResponse:
    service = HealthDataQueryService(db)

    def _success(r: GlucoseSummaryResponse):
        if r.summary is not None:
            count = r.summary.sample_count
        elif r.days is not None:
            count = sum(day.sample_count for day in r.days)
        else:
            count = 0
        return QueryLogMeta(
            start=r.start,
            end=r.end,
            timezone=r.timezone,
            record_count=count,
            bucket=r.bucket,
        )

    return await _execute(
        request,
        "/v1/query/glucose/summary",
        service.glucose_summary(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
            bucket=bucket,
        ),
        on_success=_success,
    )


@router.get(
    "/meals",
    response_model=MealsResponse,
    summary="Meal events (foods, no notes)",
    description=(
        "Read-only meal list over `[start, end)` by `meal_completed_at` "
        "(ascending), including historical/backfilled meals. "
        "Public `id` is `source_sample_id`. Notes and metadata are never returned. "
        "Raw records only; no medical advice or clinical interpretation.\n\n"
        f"Pagination: default `limit={DEFAULT_PAGE_LIMIT}`, maximum "
        f"`limit={MAX_PAGE_LIMIT}`. HMAC-signed `next_cursor` is bound to the "
        "request range."
    ),
    responses={
        **_ERROR_RESPONSES,
        200: {
            "description": "Meal page",
            "content": {
                "application/json": {
                    "example": {
                        "request_id": "00000000-0000-0000-0000-000000000001",
                        "start": "2026-08-01T00:00:00Z",
                        "end": "2026-08-12T00:00:00Z",
                        "timezone": "America/New_York",
                        "record_count": 1,
                        "truncated": False,
                        "next_cursor": None,
                        "data_fresh_through": "2026-08-05T19:42:00Z",
                        "items": [
                            {
                                "id": "meal-eeeeeeee-1111-2222-3333-444444444401",
                                "meal_completed_at": "2026-08-05T19:42:00Z",
                                "foods": ["rice", "chicken"],
                                "source": "manual",
                            }
                        ],
                    }
                }
            },
        },
    },
)
async def get_meals(
    request: Request,
    db: DbSession,
    start: ListStart,
    end: ListEnd,
    timezone: ListTimezone = DEFAULT_QUERY_TIMEZONE,
    limit: ListLimit = None,
    cursor: ListCursor = None,
) -> MealsResponse:
    service = HealthDataQueryService(db)
    return await _execute_paged(
        request,
        "/v1/query/meals",
        service.meals(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.get(
    "/workouts",
    response_model=WorkoutsResponse,
    summary="Workout intervals overlapping a bounded window",
    description=(
        "Read-only workout list of ingest-accepted (Strava) records. "
        "Native Apple Health duplicates are rejected at ingest and are not "
        "listed. Included when the interval overlaps `[start, end)`: "
        "`start_time < end AND end_time > start`. "
        "Returned timestamps are unclipped stored UTC instants. "
        "`source` is the stored provenance value (typically `apple_health`). "
        f"Maximum window: {MAX_WORKOUT_RANGE_DAYS} days. "
        "Public `id` is `source_sample_id`. `duration_minutes` is derived; "
        "`distance_meters` may be null. Heart rate, energy, and metadata are "
        "never returned. Raw records only; no medical advice or clinical "
        "interpretation.\n\n"
        f"Pagination: default `limit={DEFAULT_PAGE_LIMIT}`, maximum "
        f"`limit={MAX_PAGE_LIMIT}`. HMAC-signed `next_cursor` is bound to the "
        "request range."
    ),
    responses=_ERROR_RESPONSES,
)
async def get_workouts(
    request: Request,
    db: DbSession,
    start: ListStart,
    end: ListEnd,
    timezone: ListTimezone = DEFAULT_QUERY_TIMEZONE,
    limit: ListLimit = None,
    cursor: ListCursor = None,
) -> WorkoutsResponse:
    service = HealthDataQueryService(db)
    return await _execute_paged(
        request,
        "/v1/query/workouts",
        service.workouts(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.get(
    "/sleep-intervals",
    response_model=SleepIntervalsResponse,
    summary="Raw sleep intervals overlapping a bounded window",
    description=(
        "Read-only raw sleep intervals. Included when the interval overlaps "
        "`[start, end)`: `start_time < end AND end_time > start`. "
        "Timestamps are unclipped; stages are stored strings (not remapped or "
        f"sessionized). Maximum window: {MAX_SLEEP_RANGE_DAYS} days. "
        "Public `id` is `source_sample_id`. Raw records only; no medical advice "
        "or clinical interpretation.\n\n"
        f"Pagination: default `limit={DEFAULT_PAGE_LIMIT}`, maximum "
        f"`limit={MAX_PAGE_LIMIT}`. HMAC-signed `next_cursor` is bound to the "
        "request range."
    ),
    responses=_ERROR_RESPONSES,
)
async def get_sleep_intervals(
    request: Request,
    db: DbSession,
    start: ListStart,
    end: ListEnd,
    timezone: ListTimezone = DEFAULT_QUERY_TIMEZONE,
    limit: ListLimit = None,
    cursor: ListCursor = None,
) -> SleepIntervalsResponse:
    service = HealthDataQueryService(db)
    return await _execute_paged(
        request,
        "/v1/query/sleep-intervals",
        service.sleep_intervals(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.get(
    "/weight-measurements",
    response_model=WeightMeasurementsResponse,
    summary="Weight measurements in a bounded window",
    description=(
        "Read-only weight measurements in `[start, end)` by `measured_at`. "
        f"Maximum window: {MAX_WEIGHT_RANGE_DAYS} days. "
        "Public `id` is `source_sample_id`. Values are kilograms (`value_kg`). "
        "Raw records only; no medical advice or clinical interpretation.\n\n"
        f"Pagination: default `limit={DEFAULT_PAGE_LIMIT}`, maximum "
        f"`limit={MAX_PAGE_LIMIT}`. HMAC-signed `next_cursor` is bound to the "
        "request range."
    ),
    responses=_ERROR_RESPONSES,
)
async def get_weight_measurements(
    request: Request,
    db: DbSession,
    start: ListStart,
    end: ListEnd,
    timezone: ListTimezone = DEFAULT_QUERY_TIMEZONE,
    limit: ListLimit = None,
    cursor: ListCursor = None,
) -> WeightMeasurementsResponse:
    service = HealthDataQueryService(db)
    return await _execute_paged(
        request,
        "/v1/query/weight-measurements",
        service.weight_measurements(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.get(
    "/last-logged-meal",
    response_model=LastLoggedMealResponse,
    summary="Latest logged meal at or before an anchor",
    description=(
        "Read-only latest logged meal at or before a required timezone-aware `anchor`. "
        "Foods are included. Meal notes are excluded. "
        "Optional `lookback_days` defaults to "
        f"{DEFAULT_MEAL_LOOKBACK_DAYS} and is capped at {MAX_MEAL_LOOKBACK_DAYS}. "
        "Selection is `meal_completed_at <= anchor` and "
        "`meal_completed_at >= anchor - lookback_days` (elapsed duration, not "
        "civil-calendar arithmetic), ordered by `meal_completed_at` descending then "
        "`source_sample_id` descending.\n\n"
        "`minutes_since_last_logged_meal` is based only on the latest logged meal. "
        "It does not confirm fasting or account for unlogged food or caloric intake. "
        "Absence of a meal returns HTTP 200 with `meal: null`; it does not 404. "
        "This endpoint reports recorded data and transparent calculations only; "
        "it does not provide medical advice or clinical interpretation."
    ),
    responses=_ERROR_RESPONSES,
    response_model_exclude_none=False,
)
async def get_last_logged_meal(
    request: Request,
    db: DbSession,
    anchor: Annotated[
        datetime,
        Query(description="Timezone-aware ISO-8601 timestamp (latest meal at or before this instant)"),
    ],
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
    lookback_days: Annotated[
        int | None,
        Query(
            description=(
                "Positive integer lookback in elapsed days "
                f"(default {DEFAULT_MEAL_LOOKBACK_DAYS}, max {MAX_MEAL_LOOKBACK_DAYS})"
            ),
        ),
    ] = None,
) -> LastLoggedMealResponse:
    service = HealthDataQueryService(db)

    def _success(result: LastLoggedMealResponse) -> QueryLogMeta:
        lookback_start = result.anchor - timedelta(days=result.lookback_days)
        return QueryLogMeta(
            start=lookback_start,
            end=result.anchor,
            timezone=result.timezone,
            record_count=0 if result.meal is None else 1,
            anchor=result.anchor,
            lookback_days=result.lookback_days,
        )

    return await _execute(
        request,
        "/v1/query/last-logged-meal",
        service.last_logged_meal(
            request_id=_request_id(request),
            anchor=anchor,
            timezone=timezone,
            lookback_days=lookback_days,
        ),
        on_success=_success,
    )


@router.get(
    "/context-snapshot",
    response_model=ContextSnapshotResponse,
    summary="Bounded evidence-only context around an anchor",
    description=(
        "Read-only bounded context snapshot around a required timezone-aware `anchor`. "
        "Includes the latest logged meal (foods included, notes excluded), the most "
        "recent completed workout "
        f"(end_time at or before anchor, {SNAPSHOT_WORKOUT_LOOKBACK_DAYS}-day lookback), "
        "a compact raw sleep-interval aggregate (overlap with the sleep lookback; "
        "not a sleep session, not stage values, not a quality assessment), "
        f"the most recent weight measurement ({SNAPSHOT_WEIGHT_LOOKBACK_DAYS}-day lookback), "
        "glucose coverage, and a descriptive overall glucose summary. "
        "It does not return a glucose series.\n\n"
        f"`meal_lookback_days` default {DEFAULT_MEAL_LOOKBACK_DAYS}, max {MAX_MEAL_LOOKBACK_DAYS}. "
        f"`sleep_lookback_hours` default {DEFAULT_SLEEP_LOOKBACK_HOURS}, max {MAX_SLEEP_LOOKBACK_HOURS}. "
        f"`glucose_lookback_hours` default {DEFAULT_GLUCOSE_LOOKBACK_HOURS}, max {MAX_GLUCOSE_LOOKBACK_HOURS}. "
        "Lookbacks are elapsed durations, not civil-calendar arithmetic.\n\n"
        "Time since last logged meal does not confirm fasting. "
        "A partial snapshot is always HTTP 200 with `unavailable` entries. "
        "This endpoint reports recorded data and transparent calculations only; "
        "it does not diagnose, infer symptoms or causality, assess safety or "
        "readiness, or provide medical advice or clinical interpretation."
    ),
    responses=_ERROR_RESPONSES,
    response_model_exclude_none=False,
)
async def get_context_snapshot(
    request: Request,
    db: DbSession,
    anchor: Annotated[
        datetime,
        Query(description="Timezone-aware ISO-8601 timestamp anchoring the snapshot"),
    ],
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
    meal_lookback_days: Annotated[
        int | None,
        Query(
            description=(
                "Positive integer meal lookback in elapsed days "
                f"(default {DEFAULT_MEAL_LOOKBACK_DAYS}, max {MAX_MEAL_LOOKBACK_DAYS})"
            ),
        ),
    ] = None,
    sleep_lookback_hours: Annotated[
        int | None,
        Query(
            description=(
                "Positive integer sleep lookback in elapsed hours "
                f"(default {DEFAULT_SLEEP_LOOKBACK_HOURS}, max {MAX_SLEEP_LOOKBACK_HOURS})"
            ),
        ),
    ] = None,
    glucose_lookback_hours: Annotated[
        int | None,
        Query(
            description=(
                "Positive integer glucose lookback in elapsed hours "
                f"(default {DEFAULT_GLUCOSE_LOOKBACK_HOURS}, max {MAX_GLUCOSE_LOOKBACK_HOURS})"
            ),
        ),
    ] = None,
) -> ContextSnapshotResponse:
    service = HealthDataQueryService(db)

    def _success(result: ContextSnapshotResponse) -> QueryLogMeta:
        return QueryLogMeta(
            timezone=result.timezone,
            record_count=result.recent_sleep_intervals.record_count,
            anchor=result.anchor,
            meal_lookback_days=result.meal_lookback_days,
            sleep_lookback_hours=result.sleep_lookback_hours,
            glucose_lookback_hours=result.glucose_lookback_hours,
        )

    return await _execute(
        request,
        "/v1/query/context-snapshot",
        service.build_context_snapshot(
            request_id=_request_id(request),
            anchor=anchor,
            timezone=timezone,
            meal_lookback_days=meal_lookback_days,
            sleep_lookback_hours=sleep_lookback_hours,
            glucose_lookback_hours=glucose_lookback_hours,
        ),
        on_success=_success,
    )


@router.get(
    "/personal-timeline",
    response_model=PersonalTimelineResponse,
    summary="Bounded visualization-ready historical timeline",
    description=(
        "Read-only historical timeline for an explicit half-open `[start, end)` window "
        f"(maximum {MAX_TIMELINE_RANGE_HOURS} elapsed hours). "
        "Returns meals with foods (notes excluded), workouts, raw sleep intervals "
        "(not sessionized), weight measurements, a fixed 15-minute mean/min/max "
        "glucose series, and category-level coverage. "
        "Glucose resolution is fixed at 15 minutes and is not client-selectable. "
        "Event arrays are not paginated; more than "
        f"{MAX_TIMELINE_ITEMS_PER_CATEGORY} matching records in any "
        "event category returns `RESULT_TOO_LARGE`. "
        "This endpoint reports recorded data only; it does not diagnose, infer "
        "causes or symptoms, assess safety, or provide medical advice or "
        "clinical interpretation."
    ),
    responses=_ERROR_RESPONSES,
    response_model_exclude_none=False,
)
async def get_personal_timeline(
    request: Request,
    db: DbSession,
    start: Annotated[
        datetime, Query(description="Inclusive range start (ISO-8601 with timezone)")
    ],
    end: Annotated[
        datetime, Query(description="Exclusive range end (ISO-8601 with timezone)")
    ],
    timezone: Annotated[
        str,
        Query(description=f"IANA timezone (default {DEFAULT_QUERY_TIMEZONE})"),
    ] = DEFAULT_QUERY_TIMEZONE,
) -> PersonalTimelineResponse:
    service = HealthDataQueryService(db)

    def _success(result: PersonalTimelineResponse) -> QueryLogMeta:
        return QueryLogMeta(
            start=result.start,
            end=result.end,
            timezone=result.timezone,
            resolution=result.glucose_resolution,
            record_count=_timeline_event_count(result),
            truncated=False,
        )

    return await _execute(
        request,
        "/v1/query/personal-timeline",
        service.personal_timeline(
            request_id=_request_id(request),
            start=start,
            end=end,
            timezone=timezone,
        ),
        on_success=_success,
    )
