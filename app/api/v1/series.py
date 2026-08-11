"""Series query endpoints — glucose, runs, sleep, weight."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.core.security import RequireRead
from app.schemas.queries import (
    GlucoseSeriesQuery,
    RunsSeriesQuery,
    SleepSeriesQuery,
    WeightSeriesQuery,
)
from app.schemas.responses import QueryResponse
from app.services import query_service

router = APIRouter(prefix="/v1/query/series", tags=["series"])


@router.post(
    "/glucose",
    response_model=QueryResponse,
    summary="Query glucose series",
    description=(
        "Half-open `[start, end)` window over glucose samples. "
        "Resolutions: `raw`, `5m`, `15m`, `1h`, `1d`. "
        "Aggregated modes return count/min/max/avg per UTC bucket and omit empty buckets "
        "(no interpolation). Units are mg/dL."
    ),
)
async def glucose_series(
    body: GlucoseSeriesQuery,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    return await query_service.query_glucose(db, user.id, body)


@router.post(
    "/runs",
    response_model=QueryResponse,
    summary="Query running workouts",
    description=(
        "Returns workouts overlapping `[start, end)` using "
        "`start_time < end AND end_time > start`. "
        "`duration_seconds` is computed server-side."
    ),
)
async def runs_series(
    body: RunsSeriesQuery,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    return await query_service.query_runs(db, user.id, body)


@router.post(
    "/sleep",
    response_model=QueryResponse,
    summary="Query sleep intervals",
    description=(
        "Returns raw sleep intervals overlapping `[start, end)`. "
        "Optional `stages` filter. Does not derive nightly totals."
    ),
)
async def sleep_series(
    body: SleepSeriesQuery,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    return await query_service.query_sleep(db, user.id, body)


@router.post(
    "/weight",
    response_model=QueryResponse,
    summary="Query weight measurements",
    description="Returns weight points in kilograms for `[start, end)` by `measured_at`.",
)
async def weight_series(
    body: WeightSeriesQuery,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    return await query_service.query_weight(db, user.id, body)
