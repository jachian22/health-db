"""iOS HealthKit export payload models (source contract).

Entity-level validation happens in the ingestion service so one bad record
rejects only that record — not the entire batch or unrelated entity types.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core import ALLOWED_SLEEP_STAGES
from app.schemas.common import NonEmptyStr, UtcDateTime


class GlucoseSampleIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    sample_time: UtcDateTime
    value: float
    unit: str
    trend: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("unit")
    @classmethod
    def unit_must_be_mg_dl(cls, value: str) -> str:
        if value != "mg/dL":
            raise ValueError("INVALID_UNIT: Expected mg/dL.")
        return value

    @field_validator("value")
    @classmethod
    def value_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("INVALID_REQUEST: Glucose value must be positive.")
        return value


class WorkoutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    sport: NonEmptyStr
    start_time: UtcDateTime
    end_time: UtcDateTime
    distance_meters: float | None = None
    active_energy_kcal: float | None = None
    average_heart_rate: float | None = None
    maximum_heart_rate: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workout(self) -> WorkoutIn:
        if self.sport != "running":
            raise ValueError('INVALID_WORKOUT: Phase 1 accepts sport == "running" only.')
        if self.end_time <= self.start_time:
            raise ValueError("INVALID_WORKOUT: end_time must be after start_time.")
        if self.distance_meters is not None and self.distance_meters <= 0:
            raise ValueError("INVALID_WORKOUT: distance_meters must be positive when supplied.")
        return self


class SleepIntervalIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    start_time: UtcDateTime
    end_time: UtcDateTime
    stage: NonEmptyStr
    metadata: dict[str, Any] = Field(default_factory=dict)

    stage_warning: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def normalize_stage(self) -> SleepIntervalIn:
        if self.end_time <= self.start_time:
            raise ValueError("INVALID_TIMESTAMP: end_time must be after start_time.")
        stage = self.stage.strip().lower()
        if stage not in ALLOWED_SLEEP_STAGES:
            self.stage = "unknown"
            self.stage_warning = f"Unknown sleep stage '{stage}' mapped to 'unknown'."
        else:
            self.stage = stage
        return self


class WeightMeasurementIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    measured_at: UtcDateTime
    value: float
    unit: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("unit")
    @classmethod
    def unit_must_be_kg(cls, value: str) -> str:
        if value != "kg":
            raise ValueError("INVALID_UNIT: Expected kg.")
        return value

    @field_validator("value")
    @classmethod
    def value_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("INVALID_REQUEST: Weight value must be positive.")
        return value


class MealEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    meal_completed_at: UtcDateTime
    foods: list[str] = Field(default_factory=list)
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("foods", mode="before")
    @classmethod
    def normalize_foods(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return value


class HealthExportPayload(BaseModel):
    """Outer export envelope. Entity arrays stay as raw dicts for per-record validation."""

    model_config = ConfigDict(extra="ignore")

    complete: bool
    schema_version: int
    exported_at: UtcDateTime
    data_start: UtcDateTime
    data_end: UtcDateTime
    errors: list[str] = Field(default_factory=list)
    glucose_samples: list[dict[str, Any]] = Field(default_factory=list)
    workouts: list[dict[str, Any]] = Field(default_factory=list)
    sleep_sessions: list[dict[str, Any]] = Field(default_factory=list)
    weight_measurements: list[dict[str, Any]] = Field(default_factory=list)
    meal_events: list[dict[str, Any]] = Field(default_factory=list)

    def enforce_phase1_contract(self) -> None:
        """Raise AppError for outer-envelope violations (call from the ingest path)."""
        from app.core.errors import AppError

        if self.schema_version != 1:
            raise AppError(
                code="UNSUPPORTED_SCHEMA_VERSION",
                message=f"Unsupported schema_version {self.schema_version}.",
                hint="Phase 1 accepts schema_version == 1 only.",
                status_code=400,
            )
        if not self.complete:
            raise AppError(
                code="INCOMPLETE_EXPORT",
                message="Incomplete exports are not accepted in Phase 1.",
                hint="Set complete=true after a full export window is ready.",
                status_code=400,
            )
        if self.data_end <= self.data_start:
            raise AppError(
                code="INVALID_TIMESTAMP",
                message="data_end must be strictly after data_start.",
                status_code=400,
            )
