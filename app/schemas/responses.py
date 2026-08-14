"""Query API response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CoverageCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    first_at: datetime | None = None
    last_at: datetime | None = None


class CoverageMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    glucose: CoverageCategory
    meals: CoverageCategory
    workouts: CoverageCategory
    sleep_intervals: CoverageCategory
    weight_measurements: CoverageCategory


class CoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    start: datetime
    end: datetime
    timezone: str
    coverage: CoverageMap


class GlucoseRawPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    value_mg_dl: float


class GlucoseBucketPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    mean_mg_dl: float
    min_mg_dl: float
    max_mg_dl: float
    sample_count: int


class GlucoseSeriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    start: datetime
    end: datetime
    timezone: str
    resolution: Literal["raw", "5m", "15m", "hourly"]
    aggregation: Literal["mean_min_max"] | None = None
    source_record_count: int
    returned_point_count: int
    truncated: bool = False
    data_fresh_through: datetime | None = None
    points: list[GlucoseRawPoint | GlucoseBucketPoint] = Field(default_factory=list)


class GlucoseSummaryStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int
    first_at: datetime | None = None
    last_at: datetime | None = None
    min_mg_dl: float | None = None
    max_mg_dl: float | None = None
    mean_mg_dl: float | None = None
    median_mg_dl: float | None = None


class GlucoseDailySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: date
    sample_count: int
    first_at: datetime
    last_at: datetime
    min_mg_dl: float
    max_mg_dl: float
    mean_mg_dl: float
    median_mg_dl: float


class GlucoseSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    start: datetime
    end: datetime
    timezone: str
    bucket: Literal["overall", "daily"]
    summary: GlucoseSummaryStats | None = None
    days: list[GlucoseDailySummary] | None = None


class PagedResponse[TItem](BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    start: datetime
    end: datetime
    timezone: str
    record_count: int
    truncated: bool = False
    next_cursor: str | None = None
    data_fresh_through: datetime | None = None
    items: list[TItem] = Field(default_factory=list)


class MealItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    meal_completed_at: datetime
    foods: list[str]
    source: str


class MealsResponse(PagedResponse[MealItem]):
    pass


class WorkoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start_time: datetime
    end_time: datetime
    sport: str
    distance_meters: float | None = None
    duration_minutes: float
    source: str


class WorkoutsResponse(PagedResponse[WorkoutItem]):
    pass


class SleepIntervalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    stage: str
    source: str


class SleepIntervalsResponse(PagedResponse[SleepIntervalItem]):
    pass


class WeightMeasurementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    measured_at: datetime
    value_kg: float
    source: str


class WeightMeasurementsResponse(PagedResponse[WeightMeasurementItem]):
    pass
