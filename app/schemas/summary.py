"""Summary response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class DailyBucket(BaseModel):
    day: date
    glucose_avg: float | None = None
    glucose_min: float | None = None
    glucose_max: float | None = None
    glucose_count: int = 0
    run_count: int = 0
    run_distance_m: float | None = None
    sleep_duration_s: int | None = None
    sleep_count: int = 0
    meal_count: int = 0
    weight_avg: float | None = None
    weight_unit: str | None = None


class WeeklyBucket(BaseModel):
    week_start: date
    glucose_avg: float | None = None
    run_count: int = 0
    run_distance_m: float | None = None
    sleep_duration_s_avg: float | None = None
    meal_count: int = 0


class GlucoseSummary(BaseModel):
    start: datetime
    end: datetime
    count: int
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    by_day: list[dict[str, Any]] = Field(default_factory=list)


class RunsSummary(BaseModel):
    start: datetime
    end: datetime
    count: int
    total_distance_m: float | None = None
    total_active_energy_kcal: float | None = None
    by_sport: dict[str, int] = Field(default_factory=dict)
    by_day: list[dict[str, Any]] = Field(default_factory=list)


class SleepSummary(BaseModel):
    start: datetime
    end: datetime
    count: int
    total_duration_s: int = 0
    avg_duration_s: float | None = None
    by_day: list[dict[str, Any]] = Field(default_factory=list)
