"""Ingestion request/response schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDateTime


class EntityResultCounts(BaseModel):
    received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0


class IngestRejection(BaseModel):
    entity_type: str
    index: int
    source_sample_id: str | None = None
    code: str
    message: str


class IngestSummary(BaseModel):
    glucose_samples: EntityResultCounts
    workouts: EntityResultCounts
    sleep_sessions: EntityResultCounts
    weight_measurements: EntityResultCounts
    meal_events: EntityResultCounts


class IngestBatchResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
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
                }
            ]
        }
    )

    batch_id: uuid.UUID
    status: str = Field(description="processed | partial | failed")
    schema_version: int
    data_start: UtcDateTime
    data_end: UtcDateTime
    summary: IngestSummary
    rejections: list[IngestRejection] = Field(default_factory=list)
    rejections_truncated: bool = False
