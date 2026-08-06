"""Event lookup response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MealEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_sample_id: str
    meal_start: datetime
    meal_end: datetime
    meal_completed_at: datetime | None = None
    notes: str | None = None
    foods: list[Any] | dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    deleted_at: datetime | None = None


class RunEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_sample_id: str
    start_time: datetime
    end_time: datetime
    sport: str
    distance_m: float | None = None
    active_energy_kcal: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    deleted_at: datetime | None = None


class GlucoseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_sample_id: str
    sample_time: datetime
    value: float
    unit: str
    trend: str | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    deleted_at: datetime | None = None
