"""Ingestion request/response schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDateTime
from app.schemas.export import HealthExportPayload


class IngestBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: HealthExportPayload


class EntityResultCounts(BaseModel):
    received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0


class SourceWindow(BaseModel):
    data_start: UtcDateTime
    data_end: UtcDateTime


class IngestRejection(BaseModel):
    entity_type: str
    source_sample_id: str | None = None
    code: str
    message: str


class IngestResults(BaseModel):
    glucose_samples: EntityResultCounts
    workouts: EntityResultCounts
    sleep_sessions: EntityResultCounts
    weight_measurements: EntityResultCounts
    meal_events: EntityResultCounts


class IngestBatchResponse(BaseModel):
    batch_id: uuid.UUID
    request_id: uuid.UUID
    status: str
    schema_version: int
    source_window: SourceWindow
    results: IngestResults
    warnings: list[str] = Field(default_factory=list)
    rejections: list[IngestRejection] = Field(default_factory=list)
