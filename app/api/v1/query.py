"""GET /v1/query/* — authenticated read-only health data Query API v1.

All endpoints require Authorization: Bearer <READ_API_KEY>, explicit bounded
time ranges, and return UTC source timestamps. Timezone is used only for
local-calendar aggregation/labels (default America/New_York).

Resource protection is application-level only: hard date-range limits, meal page
size, glucose point ceilings, and Postgres statement_timeout on read queries.
There is no in-process rate limiter; rely on those caps (and platform limits).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import DbSession
from app.core import DEFAULT_MEAL_LIMIT, DEFAULT_QUERY_TIMEZONE, MAX_MEAL_LIMIT
from app.core.errors import AppError, ErrorResponse
from app.core.security import require_read_auth
from app.schemas.responses import (
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    MealsResponse,
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
        "description": "Invalid time range, timezone, resolution, cursor, limit, or result size",
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


def _log_query(
    *,
    request: Request,
    route: str,
    status: int,
    start: str | datetime | None,
    end: str | datetime | None,
    timezone: str | None,
    resolution: str | None = None,
    bucket: str | None = None,
    record_count: int | None = None,
    truncated: bool | None = None,
    latency_ms: float,
    error_code: str | None = None,
) -> None:
    start_s = start.isoformat() if isinstance(start, datetime) else start
    end_s = end.isoformat() if isinstance(end, datetime) else end
    logger.info(
        "query_access request_id=%s route=%s status=%s principal=%s "
        "start=%s end=%s timezone=%s resolution=%s bucket=%s "
        "record_count=%s truncated=%s latency_ms=%.1f error_code=%s",
        _request_id(request),
        route,
        status,
        getattr(request.state, "auth_role", "read"),
        start_s,
        end_s,
        timezone,
        resolution,
        bucket,
        record_count,
        truncated,
        latency_ms,
        error_code,
    )


async def _execute[T](
    request: Request,
    route: str,
    coro: Awaitable[T],
    *,
    on_success: Callable[
        [T],
        tuple[datetime, datetime, str, int | None, bool | None, str | None, str | None],
    ],
) -> T:
    started = time.perf_counter()
    try:
        result = await coro
        latency_ms = (time.perf_counter() - started) * 1000
        start, end, timezone, record_count, truncated, resolution, bucket = on_success(result)
        _log_query(
            request=request,
            route=route,
            status=200,
            start=start,
            end=end,
            timezone=timezone,
            resolution=resolution,
            bucket=bucket,
            record_count=record_count,
            truncated=truncated,
            latency_ms=latency_ms,
        )
        return result
    except AppError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        _log_query(
            request=request,
            route=route,
            status=exc.status_code,
            start=_qp(request, "start"),
            end=_qp(request, "end"),
            timezone=_qp(request, "timezone"),
            resolution=_qp(request, "resolution"),
            bucket=_qp(request, "bucket"),
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


@router.get(
    "/coverage",
    response_model=CoverageResponse,
    summary="Dataset coverage for a bounded window",
    description=(
        "Read-only discovery endpoint. Reports whether glucose, meals, workouts, "
        "sleep_intervals, and weight_measurements exist in the half-open "
        "`[start, end)` window for the fixed personal principal.\n\n"
        "All query endpoints are read-only and require explicit bounded time ranges.\n\n"
        "Empty categories return `count: 0` with null `first_at` / `last_at`."
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
        on_success=lambda r: (r.start, r.end, r.timezone, None, None, None, None),
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
        on_success=lambda r: (
            r.start,
            r.end,
            r.timezone,
            r.returned_point_count,
            r.truncated,
            r.resolution,
            None,
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
        return r.start, r.end, r.timezone, count, None, None, r.bucket

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
        "(ascending), including historical/backfilled meals.\n\n"
        "All query endpoints are read-only and require explicit bounded time ranges.\n\n"
        "Authenticated callers receive food strings. Notes, metadata, database "
        "primary keys, and internal source catalog IDs are never returned. "
        "Public `id` is the stable `source_sample_id`.\n\n"
        f"Pagination: default `limit={DEFAULT_MEAL_LIMIT}`, maximum "
        f"`limit={MAX_MEAL_LIMIT}`. When truncated, `truncated=true` and a "
        "HMAC-signed `next_cursor` (bound to the request range) are set."
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
    limit: Annotated[
        int | None,
        Query(description=f"Page size (default {DEFAULT_MEAL_LIMIT}, max {MAX_MEAL_LIMIT})"),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="HMAC-signed pagination cursor from a prior next_cursor"),
    ] = None,
) -> MealsResponse:
    service = HealthDataQueryService(db)
    return await _execute(
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
        on_success=lambda r: (
            r.start,
            r.end,
            r.timezone,
            r.record_count,
            r.truncated,
            None,
            None,
        ),
    )
