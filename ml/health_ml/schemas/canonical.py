"""Canonical ML-layer schemas.

These are not database schemas and not Query API schemas. They are the stable
contract consumed by future ML code. Timestamps are timezone-aware and stored
as UTC. Fields the current Query API does not expose are nullable.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def require_finite(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


class CanonicalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlucoseRecord(CanonicalRecord):
    timestamp: datetime
    glucose_mg_dl: float
    trend: str | None = None
    source: str | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @field_validator("glucose_mg_dl")
    @classmethod
    def glucose_finite(cls, value: float) -> float:
        return require_finite(value, field_name="glucose_mg_dl")


class MealRecord(CanonicalRecord):
    meal_id: str
    timestamp: datetime
    foods: list[str]
    source: str

    @field_validator("meal_id", "source")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")


class WorkoutRecord(CanonicalRecord):
    workout_id: str
    start: datetime
    end: datetime
    sport: str
    distance_meters: float | None = None
    active_energy: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    source: str

    @field_validator("workout_id", "sport", "source")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("start", "end")
    @classmethod
    def interval_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @field_validator("distance_meters", "active_energy", "average_hr", "max_hr")
    @classmethod
    def optional_finite(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return require_finite(value, field_name="numeric field")

    @model_validator(mode="after")
    def start_before_end(self) -> WorkoutRecord:
        if self.end <= self.start:
            raise ValueError("workout end must be later than start")
        return self


class SleepInterval(CanonicalRecord):
    sleep_id: str
    start: datetime
    end: datetime
    stage: str
    source: str

    @field_validator("sleep_id", "stage", "source")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("start", "end")
    @classmethod
    def interval_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @model_validator(mode="after")
    def start_before_end(self) -> SleepInterval:
        if self.end <= self.start:
            raise ValueError("sleep end must be later than start")
        return self


class WeightRecord(CanonicalRecord):
    weight_id: str
    timestamp: datetime
    weight_kg: float
    source: str

    @field_validator("weight_id", "source")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @field_validator("weight_kg")
    @classmethod
    def weight_finite(cls, value: float) -> float:
        number = require_finite(value, field_name="weight_kg")
        if number <= 0:
            raise ValueError("weight_kg must be positive")
        return number
