"""POST /v1/ingest/batch — authenticated ingest-only batch upsert."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.security import RequireIngest
from app.db.session import get_session_factory
from app.schemas.ingestion import IngestBatchRequest, IngestBatchResponse
from app.services.ingestion import ingest_batch

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post(
    "/batch",
    response_model=IngestBatchResponse,
    summary="Ingest an iOS HealthKit export batch",
    description=(
        "Accepts a versioned iOS export wrapped as `{payload: {...}}`. "
        "Resolves user identity from the ingest API key (personal-primary). "
        "Stores the raw payload once on the ingestion batch, then upserts typed "
        "rows idempotently by `(user_id, source, source_sample_id)`. "
        "Invalid individual records are reported as rejections without blocking "
        "unrelated entity types."
    ),
    responses={
        401: {"description": "Missing or invalid ingest API key"},
        403: {"description": "Valid key without ingest role"},
        400: {"description": "Unsupported schema, incomplete export, or invalid timestamps"},
    },
)
async def ingest_export_batch(
    body: IngestBatchRequest,
    request: Request,
    auth: RequireIngest,
) -> IngestBatchResponse:
    return await ingest_batch(
        session_factory=get_session_factory(),
        payload=body.payload,
        external_user_id=auth.external_user_id,
        request_id=request.state.request_id,
    )
