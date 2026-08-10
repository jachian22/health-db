"""Series query endpoints — glucose, runs, sleep, weight."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.dependencies import DbSession
from app.core.security import AuthContext, RequireRead
from app.schemas.queries import (
    GlucoseSeriesQuery,
    RunsSeriesQuery,
    SleepSeriesQuery,
    WeightSeriesQuery,
)
from app.schemas.responses import QueryResponse
from app.services import query_service

router = APIRouter(prefix="/v1/query/series", tags=["series"])


def _audit_query(request: Request, auth: AuthContext, body, response: QueryResponse) -> None:
    request.state.query_start = body.start
    request.state.query_end = body.end
    request.state.requested_resolution = getattr(body, "resolution", None)
    request.state.rows_returned = response.meta.row_count


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
    request: Request,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    response = await query_service.query_glucose(db, user.id, body)
    _audit_query(request, auth, body, response)
    return response


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
    request: Request,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    response = await query_service.query_runs(db, user.id, body)
    _audit_query(request, auth, body, response)
    return response


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
    request: Request,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    response = await query_service.query_sleep(db, user.id, body)
    _audit_query(request, auth, body, response)
    return response


@router.post(
    "/weight",
    response_model=QueryResponse,
    summary="Query weight measurements",
    description="Returns weight points in kilograms for `[start, end)` by `measured_at`.",
)
async def weight_series(
    body: WeightSeriesQuery,
    request: Request,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    response = await query_service.query_weight(db, user.id, body)
    _audit_query(request, auth, body, response)
    return response
