"""Transport-independent query request schemas."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import ALLOWED_GLUCOSE_RESOLUTIONS
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.common import UtcDateTime


class RangeQuery(BaseModel):
    """Common half-open [start, end) range query."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": (
                "Time range is half-open [start, end): include records at exactly start, "
                "exclude records at exactly end. All timestamps are UTC. "
                "Maximum permitted range is 365 days."
            )
        },
    )

    start: UtcDateTime = Field(description="Inclusive range start (UTC)")
    end: UtcDateTime = Field(description="Exclusive range end (UTC)")
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Requested row cap (default 5000, hard max 20000)",
    )

    def enforce_bounds(self) -> None:
        settings = get_settings()
        if self.end <= self.start:
            raise AppError(
                code="INVALID_RANGE",
                message="end must be strictly after start.",
                hint="Use a half-open [start, end) window.",
                status_code=400,
            )
        if self.end - self.start > timedelta(days=settings.max_lookback_days):
            raise AppError(
                code="RANGE_TOO_WIDE",
                message=f"Requested range exceeds the {settings.max_lookback_days}-day limit.",
                hint="Use a smaller date range.",
                status_code=400,
            )
        if self.limit is not None and self.limit > settings.hard_row_cap:
            raise AppError(
                code="INVALID_REQUEST",
                message=f"limit cannot exceed the hard row cap of {settings.hard_row_cap}.",
                status_code=400,
            )

    def effective_limit(self) -> int:
        settings = get_settings()
        if self.limit is None:
            return settings.default_row_cap
        return min(self.limit, settings.hard_row_cap)


class GlucoseSeriesQuery(RangeQuery):
    resolution: Literal["raw", "5m", "15m", "1h", "1d"] = Field(
        default="raw",
        description="Aggregation resolution. Missing buckets are omitted (no interpolation).",
        json_schema_extra={"enum": list(ALLOWED_GLUCOSE_RESOLUTIONS)},
    )


class RunsSeriesQuery(RangeQuery):
    pass


class SleepSeriesQuery(RangeQuery):
    stages: list[str] | None = Field(
        default=None,
        description="Optional stage filter. If omitted, all stored stages are returned.",
    )


class WeightSeriesQuery(RangeQuery):
    pass


class MealsQuery(RangeQuery):
    pass
