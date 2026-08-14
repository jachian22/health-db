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


LAST_MEAL_DERIVED_BASIS = "anchor minus meal_completed_at of the latest logged meal"

LAST_MEAL_LIMITS_FOUND = (
    "Based only on the latest logged meal at or before the anchor time.",
    "Time since last logged meal does not confirm fasting or account for unlogged food or caloric intake.",
    "This response reports recorded data and transparent calculations only; it does not provide medical advice.",
)

LAST_MEAL_LIMITS_MISSING = (
    "No logged meal was found within the requested lookback window.",
    "Absence of a logged meal does not establish fasting.",
    "This response reports recorded data and transparent calculations only; it does not provide medical advice.",
)

SNAPSHOT_LIMITS = (
    "Time since last logged meal is based only on meal records that were logged.",
    "It does not confirm fasting or account for unlogged food or caloric intake.",
    "Sleep entries are raw synced intervals, not a sleep session or sleep-quality assessment.",
    "This response reports recorded data and transparent calculations only; it does not diagnose, explain symptoms, assess safety, or provide medical advice.",
)

TIMELINE_LIMITS = (
    "The response is read-only historical data.",
    "Meals may include logged foods; notes are excluded.",
    "Sleep entries are raw synced intervals, not sleep sessions or a sleep-quality assessment.",
    "The glucose series is aggregated at 15-minute resolution.",
    "The timeline reports records only and does not provide diagnosis, causal explanations, safety assessment, or medical advice.",
)


class LastMealDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes_since_last_logged_meal: float | None = None
    basis: str | None = None


class LastLoggedMealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    anchor: datetime
    timezone: str
    lookback_days: int
    meal: MealItem | None = None
    derived: LastMealDerived
    limits: list[str]


class RecentSleepIntervals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_count: int
    first_start_time: datetime | None = None
    last_end_time: datetime | None = None
    sources: list[str] = Field(default_factory=list)


class UnavailableItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "last_logged_meal",
        "most_recent_workout",
        "recent_sleep_intervals",
        "most_recent_weight_measurement",
        "glucose_coverage",
        "glucose_summary",
    ]
    reason: Literal["no_record_in_lookback", "no_samples_in_window"]


class ContextSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    anchor: datetime
    timezone: str
    meal_lookback_days: int
    sleep_lookback_hours: int
    glucose_lookback_hours: int
    last_logged_meal: MealItem | None = None
    most_recent_workout: WorkoutItem | None = None
    recent_sleep_intervals: RecentSleepIntervals
    most_recent_weight_measurement: WeightMeasurementItem | None = None
    glucose_coverage: CoverageCategory
    glucose_summary: GlucoseSummaryStats
    derived: LastMealDerived
    unavailable: list[UnavailableItem] = Field(default_factory=list)
    limits: list[str]


class TimelineGlucoseSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregation: Literal["mean_min_max"]
    source_record_count: int
    returned_point_count: int
    truncated: bool = False
    data_fresh_through: datetime | None = None
    points: list[GlucoseBucketPoint] = Field(default_factory=list)


class PersonalTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    start: datetime
    end: datetime
    timezone: str
    glucose_resolution: Literal["15m"]
    meals: list[MealItem] = Field(default_factory=list)
    workouts: list[WorkoutItem] = Field(default_factory=list)
    sleep_intervals: list[SleepIntervalItem] = Field(default_factory=list)
    weight_measurements: list[WeightMeasurementItem] = Field(default_factory=list)
    glucose: TimelineGlucoseSeries
    coverage: CoverageMap
    limits: list[str]
