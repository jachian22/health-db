"""Ingestion endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.logging import logger
from app.db.session import get_db
from app.schemas.common import ApiResponse, ResponseMeta
from app.schemas.ingest import BatchIngestRequest, BatchIngestResponse
from app.services.ingest import ingest_batch

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/batch", response_model=ApiResponse[BatchIngestResponse])
async def post_ingest_batch(
    request: Request,
    payload: BatchIngestRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> ApiResponse[BatchIngestResponse]:
    result = await ingest_batch(db, payload)
    total = (
        result.glucose_samples.inserted
        + result.glucose_samples.updated
        + result.workouts.inserted
        + result.workouts.updated
        + result.sleep_sessions.inserted
        + result.sleep_sessions.updated
        + result.weight_measurements.inserted
        + result.weight_measurements.updated
        + result.meal_events.inserted
        + result.meal_events.updated
        + result.sync_state.inserted
        + result.sync_state.updated
    )
    tombstones = (
        result.glucose_samples.tombstoned
        + result.workouts.tombstoned
        + result.sleep_sessions.tombstoned
        + result.weight_measurements.tombstoned
        + result.meal_events.tombstoned
    )
    request.state.user_id = payload.user_id
    request.state.row_count = total
    logger.info(
        "ingest user_id=%s upserts=%s tombstones=%s",
        payload.user_id,
        total,
        tombstones,
    )
    return ApiResponse(
        data=result,
        meta=ResponseMeta(
            count=total,
            bounded=True,
            request_id=getattr(request.state, "request_id", None),
        ),
        warnings=[],
    )
