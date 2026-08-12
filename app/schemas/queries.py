"""Query API request validation helpers (transport-independent)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core import (
    ALLOWED_GLUCOSE_RESOLUTIONS,
    DEFAULT_MEAL_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_MEAL_LIMIT,
    RESOLUTION_MAX_DAYS,
)
from app.core.errors import AppError
from app.schemas.common import ensure_utc


def parse_timezone(name: str | None) -> str:
    """Return a validated IANA timezone name (default America/New_York)."""
    tz_name = (name or DEFAULT_QUERY_TIMEZONE).strip() or DEFAULT_QUERY_TIMEZONE
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise AppError(
            code="INVALID_TIMEZONE",
            message="timezone must be a valid IANA timezone name",
            hint=f"Example: {DEFAULT_QUERY_TIMEZONE}",
            status_code=422,
        ) from exc
    return tz_name


def parse_bound_timestamp(value: datetime, *, field_name: str) -> datetime:
    """Require timezone-aware ISO-8601 timestamps; normalize to UTC."""
    if value.tzinfo is None:
        raise AppError(
            code="INVALID_TIME_RANGE",
            message=f"{field_name} must include timezone information",
            hint="Use full ISO-8601 timestamps such as 2026-08-01T00:00:00Z",
            status_code=422,
        )
    return ensure_utc(value)


def validate_time_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start_utc = parse_bound_timestamp(start, field_name="start")
    end_utc = parse_bound_timestamp(end, field_name="end")
    if end_utc <= start_utc:
        raise AppError(
            code="INVALID_TIME_RANGE",
            message="end must be later than start",
            status_code=422,
        )
    return start_utc, end_utc


def validate_glucose_resolution(resolution: str) -> str:
    if resolution not in ALLOWED_GLUCOSE_RESOLUTIONS:
        raise AppError(
            code="INVALID_RESOLUTION",
            message="resolution must be one of: raw, 5m, 15m, hourly",
            status_code=422,
        )
    return resolution


def enforce_glucose_range_limit(start: datetime, end: datetime, resolution: str) -> None:
    max_days = RESOLUTION_MAX_DAYS[resolution]
    if end - start > timedelta(days=max_days):
        label = {
            "raw": "Raw",
            "5m": "5m",
            "15m": "15m",
            "hourly": "Hourly",
        }[resolution]
        raise AppError(
            code="RANGE_TOO_LARGE",
            message=f"{label} glucose queries are limited to {max_days} days",
            status_code=422,
            details={"max_days": max_days},
        )


def validate_summary_bucket(bucket: str) -> str:
    if bucket not in {"overall", "daily"}:
        raise AppError(
            code="INVALID_BUCKET",
            message="bucket must be one of: overall, daily",
            status_code=422,
        )
    return bucket


def validate_meal_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_MEAL_LIMIT
    if limit < 1:
        raise AppError(
            code="INVALID_LIMIT",
            message="limit must be a positive integer",
            status_code=422,
        )
    if limit > MAX_MEAL_LIMIT:
        raise AppError(
            code="INVALID_LIMIT",
            message=f"limit cannot exceed {MAX_MEAL_LIMIT}",
            status_code=422,
            details={"max_limit": MAX_MEAL_LIMIT},
        )
    return limit
