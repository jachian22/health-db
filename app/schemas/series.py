"""Series response item schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GlucosePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    t: datetime = Field(description="Sample timestamp (UTC)")
    v: float = Field(description="Glucose value")
    unit: str
    trend: str | None = None
    source_sample_id: str | None = None


class RunPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    sport: str
    distance_m: float | None = None
    active_energy_kcal: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    source_sample_id: str | None = None


class SleepPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    duration_s: int
    sleep_stage_summary: dict[str, Any] | None = None
    source_sample_id: str | None = None


class WeightPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    t: datetime
    v: float
    unit: str
    source_sample_id: str | None = None


class MealPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meal_start: datetime
    meal_end: datetime
    meal_completed_at: datetime | None = None
    notes: str | None = None
    foods: list[Any] | dict[str, Any] | None = None
    source_sample_id: str | None = None
    # Convenience: prefer completion for future anchors
    anchor: datetime | None = Field(
        default=None,
        description="Canonical meal anchor — meal_completed_at if set, else meal_end",
    )
