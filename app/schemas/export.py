"""iOS HealthKit export payload models (source contract).

Entity-level validation happens in the ingestion service so one bad record
rejects only that record — not the entire batch or unrelated entity types.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import NonEmptyStr, UtcDateTime


class GlucoseSampleIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "source": "apple_health",
                    "source_sample_id": "C3398F90-B255-4826-A25B-E4494471B1B5",
                    "source_name": "Stelo",
                    "sample_time": "2026-08-01T02:05:51.019Z",
                    "value": 95,
                    "unit": "mg/dL",
                    "metadata": {"source_app": "Stelo"},
                }
            ]
        },
    )

    source: NonEmptyStr = Field(description="Provenance system identifier, e.g. apple_health")
    source_name: str | None = Field(default=None, description="Human-readable source app/device")
    source_sample_id: NonEmptyStr = Field(description="Stable source identity for upserts")
    sample_time: UtcDateTime = Field(description="Timezone-aware sample timestamp (UTC)")
    value: Decimal = Field(description="Glucose value; stored as value_mg_dl", gt=0)
    unit: str = Field(description="Must be mg/dL for schema version 1")
    trend: str | None = Field(default=None, description="Optional CGM trend label")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("unit")
    @classmethod
    def unit_must_be_mg_dl(cls, value: str) -> str:
        if value != "mg/dL":
            raise ValueError("INVALID_UNIT: Glucose unit must be mg/dL")
        return value


class WorkoutIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "source": "apple_health",
                    "source_sample_id": "E7B39A9A-A759-4342-AC71-658040D9AB9E",
                    "source_name": "Strava",
                    "start_time": "2026-08-10T12:11:31.000Z",
                    "end_time": "2026-08-10T12:57:00.000Z",
                    "sport": "running",
                    "distance_meters": 3647.8,
                    "active_energy_kcal": None,
                    "average_heart_rate": None,
                    "maximum_heart_rate": None,
                    "metadata": {"source_app": "Strava"},
                }
            ]
        },
    )

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    sport: NonEmptyStr = Field(description="Phase 1 accepts running")
    start_time: UtcDateTime
    end_time: UtcDateTime
    distance_meters: Decimal | None = Field(
        default=None, description="Distance in meters; zero or positive when present"
    )
    active_energy_kcal: Decimal | None = Field(
        default=None, description="Active energy in kcal; zero or positive when present"
    )
    average_heart_rate: Decimal | None = Field(
        default=None, description="Average heart rate in bpm; zero or positive when present"
    )
    maximum_heart_rate: Decimal | None = Field(
        default=None, description="Maximum heart rate in bpm; zero or positive when present"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workout(self) -> WorkoutIn:
        if self.source_name != "Strava":
            raise ValueError(
                "UNSUPPORTED_WORKOUT_SOURCE: Only Strava workouts are accepted"
            )
        if self.sport != "running":
            raise ValueError('INVALID_WORKOUT: Phase 1 accepts sport == "running" only.')
        if self.end_time <= self.start_time:
            raise ValueError("INVALID_WORKOUT: end_time must be after start_time.")
        for field_name in (
            "distance_meters",
            "active_energy_kcal",
            "average_heart_rate",
            "maximum_heart_rate",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(
                    f"INVALID_WORKOUT: {field_name} must be zero or positive when supplied."
                )
        return self


class SleepIntervalIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "source": "apple_health",
                    "source_sample_id": "B55D98A3-095F-4B39-9578-D9D91CA3E6CE",
                    "source_name": "Jason’s Apple Watch",
                    "start_time": "2026-07-30T04:30:36.010Z",
                    "end_time": "2026-07-30T04:50:40.707Z",
                    "stage": "core",
                    "metadata": {"source_app": "Jason’s Apple Watch"},
                }
            ]
        },
    )

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    start_time: UtcDateTime
    end_time: UtcDateTime
    stage: NonEmptyStr = Field(
        description="Raw sleep stage label (core, deep, rem, awake, asleep, unknown, …)"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval(self) -> SleepIntervalIn:
        if self.end_time <= self.start_time:
            raise ValueError("INVALID_TIMESTAMP: end_time must be after start_time.")
        return self


class WeightMeasurementIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "source": "apple_health",
                    "source_sample_id": "42D1E6F7-7353-4AEF-9BF9-22375050E285",
                    "source_name": "Health",
                    "measured_at": "2026-08-10T12:00:00.000Z",
                    "value": 92.714280428,
                    "unit": "kg",
                    "metadata": {"source_app": "Health"},
                }
            ]
        },
    )

    source: NonEmptyStr
    source_name: str | None = None
    source_sample_id: NonEmptyStr
    measured_at: UtcDateTime
    value: Decimal = Field(description="Weight value; stored as value_kg", gt=0)
    unit: str = Field(description="Must be kg; pounds are not accepted")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("unit")
    @classmethod
    def unit_must_be_kg(cls, value: str) -> str:
        if value != "kg":
            raise ValueError("INVALID_UNIT: Weight unit must be kg")
        return value


class MealEventIn(BaseModel):
    """Strict meal contract: completion-time only (no meal_start / meal_end)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "source": "manual",
                    "source_sample_id": "meal-d86ca362-6c61-4512-bf09-de76f1c58b73",
                    "meal_completed_at": "2026-08-10T17:15:00.000Z",
                    "foods": [
                        "One scoop Equip chocolate protein powder",
                        "16 fluid ounces of A2 whole milk (Costco)",
                    ],
                    "notes": None,
                    "metadata": {},
                }
            ]
        },
    )

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
        if not isinstance(value, list):
            raise ValueError("INVALID_REQUEST: foods must be a list of strings")
        if not all(isinstance(item, str) for item in value):
            raise ValueError("INVALID_REQUEST: foods must be a list of strings")
        return value


class HealthExportPayload(BaseModel):
    """iOS export envelope accepted directly as POST /v1/ingest/batch body."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "schema_version": 1,
                    "exported_at": "2026-08-10T20:02:50.510Z",
                    "data_start": "2026-07-30T00:00:00.000Z",
                    "data_end": "2026-08-10T20:02:50.369Z",
                    "glucose_samples": [],
                    "workouts": [],
                    "sleep_sessions": [],
                    "weight_measurements": [],
                    "meal_events": [],
                }
            ]
        },
    )

    schema_version: int = Field(description="Export schema version; Phase 1 accepts 1 only")
    exported_at: UtcDateTime = Field(description="Timezone-aware export timestamp")
    data_start: UtcDateTime
    data_end: UtcDateTime
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
                message="Unsupported export schema version",
                status_code=400,
                details={"supported_versions": [1]},
            )
        if self.data_end <= self.data_start:
            raise AppError(
                code="INVALID_TIMESTAMP",
                message="data_end must be strictly after data_start.",
                status_code=400,
            )
