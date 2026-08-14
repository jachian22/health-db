"""Query API request validation helpers (transport-independent)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core import (
    ALLOWED_GLUCOSE_RESOLUTIONS,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_GLUCOSE_POINTS,
    MAX_PAGE_LIMIT,
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


def enforce_glucose_point_limit(count: int) -> None:
    if count > MAX_GLUCOSE_POINTS:
        raise AppError(
            code="RESULT_TOO_LARGE",
            message=(
                f"Glucose query matched more than {MAX_GLUCOSE_POINTS} points; "
                "narrow the time range or use a coarser resolution"
            ),
            status_code=422,
            details={"max_points": MAX_GLUCOSE_POINTS},
        )


def validate_summary_bucket(bucket: str) -> str:
    if bucket not in {"overall", "daily"}:
        raise AppError(
            code="INVALID_BUCKET",
            message="bucket must be one of: overall, daily",
            status_code=422,
        )
    return bucket


def enforce_max_range_days(
    start: datetime,
    end: datetime,
    *,
    max_days: int,
    label: str,
) -> None:
    if end - start > timedelta(days=max_days):
        raise AppError(
            code="RANGE_TOO_LARGE",
            message=f"{label} queries are limited to {max_days} days",
            status_code=422,
            details={"max_days": max_days},
        )


def enforce_max_range_hours(
    start: datetime,
    end: datetime,
    *,
    max_hours: int,
    label: str,
) -> None:
    if end - start > timedelta(hours=max_hours):
        raise AppError(
            code="RANGE_TOO_LARGE",
            message=f"{label} queries are limited to {max_hours} hours",
            status_code=422,
            details={"max_hours": max_hours},
        )


LOOKBACK_QUERY_FIELDS = frozenset(
    {
        "lookback_days",
        "meal_lookback_days",
        "sleep_lookback_hours",
        "glucose_lookback_hours",
    }
)


def remap_lookback_validation(errors: list[Any]) -> AppError | None:
    """Map FastAPI/Pydantic lookback parse failures to INVALID_LOOKBACK (HTTP 422)."""
    for err in errors:
        loc = err.get("loc") or ()
        field = next((part for part in loc if part in LOOKBACK_QUERY_FIELDS), None)
        if field is not None:
            return AppError(
                code="INVALID_LOOKBACK",
                message=f"{field} must be a positive integer",
                status_code=422,
            )
    return None


def validate_lookback(
    value: str | int | None,
    *,
    default: int,
    max_value: int,
    unit: str,
    field_name: str,
    label: str,
) -> int:
    """Parse a positive integer lookback. Reject non-integers before FastAPI coercion."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AppError(
            code="INVALID_LOOKBACK",
            message=f"{field_name} must be a positive integer",
            status_code=422,
        )
    if isinstance(value, int):
        parsed = value
    else:
        text = value.strip()
        if text == "":
            return default
        negative = text.startswith("-")
        digits = text[1:] if negative else text
        if not digits.isdigit():
            raise AppError(
                code="INVALID_LOOKBACK",
                message=f"{field_name} must be a positive integer",
                status_code=422,
            )
        parsed = int(text)
    if parsed <= 0:
        raise AppError(
            code="INVALID_LOOKBACK",
            message=f"{field_name} must be a positive integer",
            status_code=422,
        )
    if parsed > max_value:
        detail_key = "max_days" if unit == "days" else "max_hours"
        raise AppError(
            code="RANGE_TOO_LARGE",
            message=f"{label} is limited to {max_value} {unit}",
            status_code=422,
            details={detail_key: max_value},
        )
    return parsed


def validate_page_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise AppError(
            code="INVALID_LIMIT",
            message="limit must be a positive integer",
            status_code=422,
        )
    if limit > MAX_PAGE_LIMIT:
        raise AppError(
            code="INVALID_LIMIT",
            message=f"limit cannot exceed {MAX_PAGE_LIMIT}",
            status_code=422,
            details={"max_limit": MAX_PAGE_LIMIT},
        )
    return limit
