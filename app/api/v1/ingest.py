"""POST /v1/ingest/batch — authenticated ingest-only batch upsert."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.security import require_ingest_auth
from app.db.session import get_session_factory
from app.schemas.export import HealthExportPayload
from app.schemas.ingestion import IngestBatchResponse
from app.services.ingestion import ingest_batch

router = APIRouter(
    prefix="/v1/ingest",
    tags=["ingest"],
    # Auth is enforced for its side effect (401 on bad/missing key);
    # identity is server-owned, so handlers never consume the auth context.
    dependencies=[Depends(require_ingest_auth)],
)


@router.post(
    "/batch",
    response_model=IngestBatchResponse,
    summary="Ingest an iOS HealthKit export batch",
    description=(
        "Accepts a versioned iOS HealthKit export object directly as the request body "
        "(schema_version 1).\n\n"
        "**Units**\n"
        "- Glucose: `mg/dL`\n"
        "- Weight: `kg` (pounds are rejected; no conversion)\n"
        "- Distance: meters\n"
        "- Energy: kcal\n"
        "- Heart rate: bpm\n\n"
        "**Identity / idempotency**\n"
        "Replaying a record with the same `(user_id, source, source_sample_id)` "
        "does not create a duplicate. Matching identities are classified as "
        "`inserted`, `updated`, or `unchanged`.\n\n"
        "User identity is resolved server-side as `personal-primary` — clients must "
        "not send `user_id`. Invalid individual records are reported as rejections "
        "without blocking unrelated valid records.\n\n"
        "Phase 1 workouts must be Strava (`source_name == \"Strava\"`) with "
        '`sport == "running"`.\n\n'
        "**Examples**\n"
        "- Success: status `processed` with per-entity inserted counts.\n"
        "- Partial: status `partial` with sanitized rejections "
        "(e.g. `INVALID_UNIT`, `UNSUPPORTED_WORKOUT_SOURCE`).\n"
        "- Unauthorized: missing/invalid `Authorization: Bearer <INGEST_API_KEY>`."
    ),
    responses={
        200: {
            "description": (
                "Ingestion report. `processed` when all records succeed; "
                "`partial` when some records are rejected."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "success": {
                            "summary": "All records processed",
                            "value": {
                                "batch_id": "550e8400-e29b-41d4-a716-446655440000",
                                "status": "processed",
                                "schema_version": 1,
                                "data_start": "2026-07-30T00:00:00Z",
                                "data_end": "2026-08-10T20:02:50Z",
                                "summary": {
                                    "glucose_samples": {
                                        "received": 2,
                                        "inserted": 2,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "workouts": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "sleep_sessions": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "weight_measurements": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "meal_events": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                },
                                "rejections": [],
                                "rejections_truncated": False,
                            },
                        },
                        "partial": {
                            "summary": "Partial success with rejections",
                            "value": {
                                "batch_id": "550e8400-e29b-41d4-a716-446655440000",
                                "status": "partial",
                                "schema_version": 1,
                                "data_start": "2026-07-30T00:00:00Z",
                                "data_end": "2026-08-10T20:02:50Z",
                                "summary": {
                                    "glucose_samples": {
                                        "received": 2,
                                        "inserted": 2,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "workouts": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "sleep_sessions": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                    "weight_measurements": {
                                        "received": 1,
                                        "inserted": 0,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 1,
                                    },
                                    "meal_events": {
                                        "received": 1,
                                        "inserted": 1,
                                        "updated": 0,
                                        "unchanged": 0,
                                        "rejected": 0,
                                    },
                                },
                                "rejections": [
                                    {
                                        "entity_type": "weight_measurements",
                                        "index": 0,
                                        "source_sample_id": "example-id",
                                        "code": "INVALID_UNIT",
                                        "message": "Weight unit must be kg",
                                    }
                                ],
                                "rejections_truncated": False,
                            },
                        },
                    }
                }
            },
        },
        401: {
            "description": "Missing or invalid ingest API key",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Invalid or missing ingestion credentials",
                        },
                        "request_id": "00000000-0000-0000-0000-000000000001",
                    }
                }
            },
        },
        400: {
            "description": "Unsupported schema version or invalid top-level timestamps",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "UNSUPPORTED_SCHEMA_VERSION",
                            "message": "Unsupported export schema version",
                            "details": {"supported_versions": [1]},
                        },
                        "request_id": "00000000-0000-0000-0000-000000000001",
                    }
                }
            },
        },
        500: {
            "description": "Unexpected ingestion failure",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "INGESTION_FAILED",
                            "message": "Ingestion could not be completed",
                        },
                        "request_id": "00000000-0000-0000-0000-000000000001",
                    }
                }
            },
        },
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "minimal": {
                            "summary": "Valid minimal ingestion request",
                            "value": {
                                "schema_version": 1,
                                "exported_at": "2026-08-10T20:02:50.510Z",
                                "data_start": "2026-07-30T00:00:00.000Z",
                                "data_end": "2026-08-10T20:02:50.369Z",
                                "glucose_samples": [],
                                "workouts": [],
                                "sleep_sessions": [],
                                "weight_measurements": [],
                                "meal_events": [],
                            },
                        }
                    }
                }
            }
        }
    },
)
async def ingest_export_batch(
    body: HealthExportPayload,
    request: Request,
) -> IngestBatchResponse:
    return await ingest_batch(
        session_factory=get_session_factory(),
        payload=body,
        request_id=request.state.request_id,
    )
