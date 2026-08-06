"""Ingestion request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_GLUCOSE_UNITS = {"mg/dL", "mmol/L"}
ALLOWED_WEIGHT_UNITS = {"kg", "lb"}


class IngestSampleBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_name: str = Field(min_length=1, max_length=64)
    source_type: str = Field(default="app", max_length=32)
    source_sample_id: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None
    deleted_at: datetime | None = None


class GlucoseIngest(IngestSampleBase):
    sample_time: datetime
    value: float
    unit: str
    trend: str | None = None

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, v: str) -> str:
        if v not in ALLOWED_GLUCOSE_UNITS:
            raise ValueError(f"Unsupported glucose unit '{v}'. Allowed: {sorted(ALLOWED_GLUCOSE_UNITS)}")
        return v


class WorkoutIngest(IngestSampleBase):
    start_time: datetime
    end_time: datetime
    sport: str = Field(min_length=1, max_length=64)
    distance_m: float | None = None
    active_energy_kcal: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None

    @model_validator(mode="after")
    def validate_times(self) -> WorkoutIngest:
        if self.start_time >= self.end_time:
            raise ValueError("workout start_time must be before end_time")
        return self


class SleepIngest(IngestSampleBase):
    start_time: datetime
    end_time: datetime
    duration_s: int | None = None
    sleep_stage_summary: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_and_derive(self) -> SleepIngest:
        if self.start_time >= self.end_time:
            raise ValueError("sleep start_time must be before end_time")
        if self.duration_s is None:
            self.duration_s = int((self.end_time - self.start_time).total_seconds())
        if self.duration_s < 0:
            raise ValueError("duration_s must be non-negative")
        return self


class WeightIngest(IngestSampleBase):
    measured_at: datetime
    value: float
    unit: str

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, v: str) -> str:
        if v not in ALLOWED_WEIGHT_UNITS:
            raise ValueError(f"Unsupported weight unit '{v}'. Allowed: {sorted(ALLOWED_WEIGHT_UNITS)}")
        return v


class MealIngest(IngestSampleBase):
    meal_start: datetime
    meal_end: datetime
    meal_completed_at: datetime | None = None
    notes: str | None = None
    foods: list[Any] | dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_times(self) -> MealIngest:
        if self.meal_start > self.meal_end:
            raise ValueError("meal_start must be <= meal_end")
        # Prefer explicit completion; otherwise leave null for Phase 1
        return self


class SyncStateIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(min_length=1, max_length=64)
    source_name: str = Field(min_length=1, max_length=64)
    anchor: str | None = None
    last_synced_at: datetime | None = None
    last_seen_at: datetime | None = None


class BatchIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128, description="External user identifier")
    glucose_samples: list[GlucoseIngest] = Field(default_factory=list)
    workouts: list[WorkoutIngest] = Field(default_factory=list)
    sleep_sessions: list[SleepIngest] = Field(default_factory=list)
    weight_measurements: list[WeightIngest] = Field(default_factory=list)
    meal_events: list[MealIngest] = Field(default_factory=list)
    sync_state: list[SyncStateIngest] = Field(default_factory=list)


class EntityUpsertCounts(BaseModel):
    inserted: int = 0
    updated: int = 0
    tombstoned: int = 0


class BatchIngestResponse(BaseModel):
    user_id: str
    glucose_samples: EntityUpsertCounts
    workouts: EntityUpsertCounts
    sleep_sessions: EntityUpsertCounts
    weight_measurements: EntityUpsertCounts
    meal_events: EntityUpsertCounts
    sync_state: EntityUpsertCounts
