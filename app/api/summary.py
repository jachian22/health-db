"""Summary QUERY endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import envelope
from app.core.auth import require_api_key
from app.db.session import get_db
from app.schemas.common import ApiResponse, SummaryRequest
from app.schemas.summary import DailyBucket, GlucoseSummary, RunsSummary, SleepSummary, WeeklyBucket
from app.services import summary as summary_svc

router = APIRouter(prefix="/v1/summary", tags=["summary"])


@router.api_route("/daily", methods=["QUERY", "POST"], response_model=ApiResponse[list[DailyBucket]])
async def query_daily_summary(
    request: Request,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[DailyBucket]]:
    data, meta = await summary_svc.summary_daily(
        db, start=body.start, end=body.end, user_id=body.user_id
    )
    return envelope(request, data, meta)


@router.api_route("/weekly", methods=["QUERY", "POST"], response_model=ApiResponse[list[WeeklyBucket]])
async def query_weekly_summary(
    request: Request,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[list[WeeklyBucket]]:
    data, meta = await summary_svc.summary_weekly(
        db, start=body.start, end=body.end, user_id=body.user_id
    )
    return envelope(request, data, meta)


@router.api_route("/glucose", methods=["QUERY", "POST"], response_model=ApiResponse[GlucoseSummary])
async def query_glucose_summary(
    request: Request,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[GlucoseSummary]:
    data, meta = await summary_svc.summary_glucose(
        db,
        start=body.start,
        end=body.end,
        group_by=body.group_by,
        user_id=body.user_id,
    )
    return envelope(request, data, meta)


@router.api_route("/runs", methods=["QUERY", "POST"], response_model=ApiResponse[RunsSummary])
async def query_runs_summary(
    request: Request,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[RunsSummary]:
    data, meta = await summary_svc.summary_runs(
        db, start=body.start, end=body.end, user_id=body.user_id
    )
    return envelope(request, data, meta)


@router.api_route("/sleep", methods=["QUERY", "POST"], response_model=ApiResponse[SleepSummary])
async def query_sleep_summary(
    request: Request,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[SleepSummary]:
    data, meta = await summary_svc.summary_sleep(
        db, start=body.start, end=body.end, user_id=body.user_id
    )
    return envelope(request, data, meta)
