"""Read API response schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDateTime


class QueryMeta(BaseModel):
    requested_start: UtcDateTime
    requested_end: UtcDateTime
    actual_first_record_at: UtcDateTime | None = None
    actual_last_record_at: UtcDateTime | None = None
    row_count: int
    timezone: str = "UTC"


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[Any]
    meta: QueryMeta
    warnings: list[str] = Field(default_factory=list)
    next_cursor: str | None = None


class GlucoseRawPoint(BaseModel):
    timestamp: UtcDateTime
    value_mg_dl: Decimal | float
    source_name: str | None = None
    source_sample_id: str


class GlucoseBucketPoint(BaseModel):
    bucket_start: UtcDateTime
    bucket_end: UtcDateTime
    count: int
    min_mg_dl: Decimal | float
    max_mg_dl: Decimal | float
    avg_mg_dl: Decimal | float


class RunPoint(BaseModel):
    source_name: str | None = None
    source_sample_id: str
    sport: str
    start_time: UtcDateTime
    end_time: UtcDateTime
    duration_seconds: int
    distance_meters: Decimal | float | None = None
    active_energy_kcal: Decimal | float | None = None
    average_heart_rate: Decimal | float | None = None
    maximum_heart_rate: Decimal | float | None = None


class SleepPoint(BaseModel):
    source_name: str | None = None
    source_sample_id: str
    start_time: UtcDateTime
    end_time: UtcDateTime
    duration_seconds: int
    stage: str


class WeightPoint(BaseModel):
    timestamp: UtcDateTime
    value_kg: Decimal | float
    source_name: str | None = None
    source_sample_id: str


class MealPoint(BaseModel):
    source: str
    source_sample_id: str
    meal_completed_at: UtcDateTime
    foods: list[str]
    notes: str | None = None
