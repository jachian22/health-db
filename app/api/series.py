"""Series QUERY endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import envelope
from app.core.auth import require_api_key
from app.db.session import get_db
from app.schemas.common import ApiResponse, SeriesRequest
from app.schemas.series import GlucosePoint, MealPoint, RunPoint, SleepPoint, WeightPoint
from app.services import series as series_svc

router = APIRouter(prefix="/v1/series", tags=["series"])


@router.api_route("/glucose", methods=["QUERY", "POST"], response_model=ApiResponse[list[GlucosePoint]])
async def query_glucose_series(
    request: Request,
    body: SeriesRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[GlucosePoint]]:
    data, meta = await series_svc.series_glucose(
        db,
        start=body.start,
        end=body.end,
        resolution=body.resolution,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)


@router.api_route("/runs", methods=["QUERY", "POST"], response_model=ApiResponse[list[RunPoint]])
async def query_runs_series(
    request: Request,
    body: SeriesRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[RunPoint]]:
    data, meta = await series_svc.series_runs(
        db,
        start=body.start,
        end=body.end,
        resolution=body.resolution,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
        sport_filter=body.sport,
    )
    return envelope(request, data, meta)


@router.api_route("/sleep", methods=["QUERY", "POST"], response_model=ApiResponse[list[SleepPoint]])
async def query_sleep_series(
    request: Request,
    body: SeriesRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[SleepPoint]]:
    data, meta = await series_svc.series_sleep(
        db,
        start=body.start,
        end=body.end,
        resolution=body.resolution,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)


@router.api_route("/weight", methods=["QUERY", "POST"], response_model=ApiResponse[list[WeightPoint]])
async def query_weight_series(
    request: Request,
    body: SeriesRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[WeightPoint]]:
    data, meta = await series_svc.series_weight(
        db,
        start=body.start,
        end=body.end,
        resolution=body.resolution,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)


@router.api_route("/meals", methods=["QUERY", "POST"], response_model=ApiResponse[list[MealPoint]])
async def query_meals_series(
    request: Request,
    body: SeriesRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[MealPoint]]:
    data, meta = await series_svc.series_meals(
        db,
        start=body.start,
        end=body.end,
        resolution=body.resolution,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    warnings = []
    if meta.get("anchor_field"):
        warnings.append(
            "Meal series includes 'anchor' (meal_completed_at if set, else meal_end) for future "
            "completion-time pivots."
        )
    return envelope(request, data, meta, warnings=warnings)
