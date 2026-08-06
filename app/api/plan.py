"""Planner-lite QUERY endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.auth import require_api_key
from app.core.logging import logger
from app.schemas.common import ApiResponse, ResponseMeta
from app.schemas.plan import PlanRetrieveRequest, PlanRetrieveResponse
from app.services.plan import plan_retrieve

router = APIRouter(prefix="/v1/plan", tags=["plan"])


@router.api_route(
    "/retrieve",
    methods=["QUERY", "POST"],
    response_model=ApiResponse[PlanRetrieveResponse],
)
async def query_plan_retrieve(
    request: Request,
    body: PlanRetrieveRequest,
    _: str = Depends(require_api_key),
) -> ApiResponse[PlanRetrieveResponse]:
    plan = plan_retrieve(body)
    request.state.user_id = body.user_id
    request.state.range_start = plan.recommended_start
    request.state.range_end = plan.recommended_end
    request.state.bounded = True
    logger.info(
        "planner intent=%s entities=%s resolution=%s endpoints=%s",
        plan.intent,
        plan.recommended_entities,
        plan.recommended_resolution,
        [e.path for e in plan.recommended_endpoints],
    )
    return ApiResponse(
        data=plan,
        meta=ResponseMeta(
            count=len(plan.recommended_endpoints),
            start=plan.recommended_start,
            end=plan.recommended_end,
            resolution=plan.recommended_resolution,
            bounded=True,
            request_id=getattr(request.state, "request_id", None),
        ),
        warnings=plan.caveats,
    )
