"""Event lookup QUERY endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import envelope
from app.core.auth import require_api_key
from app.db.session import get_db
from app.schemas.common import ApiResponse, EventRequest
from app.schemas.events import GlucoseEventOut, MealEventOut, RunEventOut
from app.services import events as events_svc

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.api_route("/meals", methods=["QUERY", "POST"], response_model=ApiResponse[list[MealEventOut]])
async def query_meal_events(
    request: Request,
    body: EventRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[MealEventOut]]:
    data, meta = await events_svc.events_meals(
        db,
        start=body.start,
        end=body.end,
        limit=body.limit,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)


@router.api_route("/runs", methods=["QUERY", "POST"], response_model=ApiResponse[list[RunEventOut]])
async def query_run_events(
    request: Request,
    body: EventRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[RunEventOut]]:
    data, meta = await events_svc.events_runs(
        db,
        start=body.start,
        end=body.end,
        limit=body.limit,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)


@router.api_route("/glucose", methods=["QUERY", "POST"], response_model=ApiResponse[list[GlucoseEventOut]])
async def query_glucose_events(
    request: Request,
    body: EventRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[GlucoseEventOut]]:
    data, meta = await events_svc.events_glucose(
        db,
        start=body.start,
        end=body.end,
        limit=body.limit,
        user_id=body.user_id,
        include_deleted=body.include_deleted,
    )
    return envelope(request, data, meta)
