"""Query API response models and MCP tool input validation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from mcp_service.constants import (
    DEFAULT_MEAL_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_MEAL_LIMIT,
    RESOLUTION_LABELS,
    RESOLUTION_MAX_DAYS,
)
from mcp_service.errors import ToolError


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageCategory(QueryModel):
    count: int
    first_at: datetime | None = None
    last_at: datetime | None = None


class CoverageMap(QueryModel):
    glucose: CoverageCategory
    meals: CoverageCategory
    workouts: CoverageCategory
    sleep_intervals: CoverageCategory
    weight_measurements: CoverageCategory


class CoverageResponse(QueryModel):
    request_id: str
    start: datetime
    end: datetime
    timezone: str
    coverage: CoverageMap


class GlucoseRawPoint(QueryModel):
    timestamp: datetime
    value_mg_dl: float


class GlucoseBucketPoint(QueryModel):
    start: datetime
    end: datetime
    mean_mg_dl: float
    min_mg_dl: float
    max_mg_dl: float
    sample_count: int


class GlucoseSeriesResponse(QueryModel):
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


class GlucoseSummaryStats(QueryModel):
    sample_count: int
    first_at: datetime | None = None
    last_at: datetime | None = None
    min_mg_dl: float | None = None
    max_mg_dl: float | None = None
    mean_mg_dl: float | None = None
    median_mg_dl: float | None = None


class GlucoseDailySummary(QueryModel):
    local_date: date
    sample_count: int
    first_at: datetime
    last_at: datetime
    min_mg_dl: float
    max_mg_dl: float
    mean_mg_dl: float
    median_mg_dl: float


class GlucoseSummaryResponse(QueryModel):
    request_id: str
    start: datetime
    end: datetime
    timezone: str
    bucket: Literal["overall", "daily"]
    summary: GlucoseSummaryStats | None = None
    days: list[GlucoseDailySummary] | None = None


class MealItem(QueryModel):
    id: str
    meal_completed_at: datetime
    foods: list[str]
    source: str


class MealsResponse(QueryModel):
    request_id: str
    start: datetime
    end: datetime
    timezone: str
    record_count: int
    truncated: bool = False
    next_cursor: str | None = None
    data_fresh_through: datetime | None = None
    items: list[MealItem] = Field(default_factory=list)


def to_iso8601(value: datetime) -> str:
    text = value.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def parse_timezone(name: str | None) -> str:
    tz_name = (name or DEFAULT_QUERY_TIMEZONE).strip() or DEFAULT_QUERY_TIMEZONE
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ToolError(
            code="INVALID_TIMEZONE",
            message="timezone must be a valid IANA timezone name",
            hint=f"Example: {DEFAULT_QUERY_TIMEZONE}",
        ) from exc
    return tz_name


def parse_bound_timestamp(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ToolError(
            code="INVALID_TIME_RANGE",
            message=f"{field_name} must include timezone information",
            hint="Use full ISO-8601 timestamps such as 2026-08-01T00:00:00Z",
        )
    return value.astimezone(UTC)


def validate_time_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start_aware = parse_bound_timestamp(start, field_name="start")
    end_aware = parse_bound_timestamp(end, field_name="end")
    if end_aware <= start_aware:
        raise ToolError(
            code="INVALID_TIME_RANGE",
            message="end must be later than start",
        )
    return start_aware, end_aware


def enforce_glucose_range_limit(start: datetime, end: datetime, resolution: str) -> None:
    max_days = RESOLUTION_MAX_DAYS[resolution]
    if end - start > timedelta(days=max_days):
        label = RESOLUTION_LABELS[resolution]
        raise ToolError(
            code="RANGE_TOO_LARGE",
            message=f"{label} glucose queries are limited to {max_days} days",
            max_days=max_days,
        )


def validate_meal_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_MEAL_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ToolError(
            code="INVALID_LIMIT",
            message="limit must be a positive integer",
        )
    if limit > MAX_MEAL_LIMIT:
        raise ToolError(
            code="INVALID_LIMIT",
            message=f"limit cannot exceed {MAX_MEAL_LIMIT}",
            max_limit=MAX_MEAL_LIMIT,
        )
    return limit


def summary_record_count(result: GlucoseSummaryResponse) -> int:
    if result.summary is not None:
        return result.summary.sample_count
    if result.days is not None:
        return sum(day.sample_count for day in result.days)
    return 0


def coverage_record_count(result: CoverageResponse) -> int:
    coverage = result.coverage
    return (
        coverage.glucose.count
        + coverage.meals.count
        + coverage.workouts.count
        + coverage.sleep_intervals.count
        + coverage.weight_measurements.count
    )
