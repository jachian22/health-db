"""Event query endpoints — meals."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.core.security import RequireRead
from app.schemas.queries import MealsQuery
from app.schemas.responses import QueryResponse
from app.services import query_service

router = APIRouter(prefix="/v1/query/events", tags=["events"])


@router.post(
    "/meals",
    response_model=QueryResponse,
    summary="Query meal events",
    description=(
        "Returns meal completion events where "
        "`meal_completed_at >= start AND meal_completed_at < end`. "
        "Meals are singular completion-time events (no meal_start/meal_end)."
    ),
)
async def meals_events(
    body: MealsQuery,
    auth: RequireRead,
    db: DbSession,
) -> QueryResponse:
    user = await query_service.resolve_user(db, auth.external_user_id)
    return await query_service.query_meals(db, user.id, body)
