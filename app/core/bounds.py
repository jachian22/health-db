"""Time-range and row-count bounds for agent-safe queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings, get_settings
from app.core.errors import (
    InvalidRangeError,
    RangeTooWideError,
    TooManyRowsError,
    UnsupportedResolutionError,
)

ALLOWED_RESOLUTIONS = ("raw", "1m", "5m", "15m", "1h", "1d")

RESOLUTION_SECONDS: dict[str, int | None] = {
    "raw": None,
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_range(
    start: datetime | None,
    end: datetime | None,
    *,
    settings: Settings | None = None,
    require_both: bool = True,
) -> tuple[datetime, datetime]:
    """Validate and normalize a query time range."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)

    if require_both and (start is None or end is None):
        raise InvalidRangeError("Both 'start' and 'end' are required")

    if start is None and end is None:
        end = now
        start = end - timedelta(days=settings.default_lookback_days)
    elif start is None:
        assert end is not None
        end = ensure_utc(end)
        start = end - timedelta(days=settings.default_lookback_days)
    elif end is None:
        start = ensure_utc(start)
        end = now
    else:
        start = ensure_utc(start)
        end = ensure_utc(end)

    if start >= end:
        raise InvalidRangeError("'start' must be strictly before 'end'")

    span = end - start
    if span > timedelta(days=settings.max_lookback_days):
        raise RangeTooWideError(
            f"Requested range spans {span.days} days",
            max_days=settings.max_lookback_days,
        )

    return start, end


def validate_resolution(resolution: str | None) -> str:
    res = resolution or "raw"
    if res not in ALLOWED_RESOLUTIONS:
        raise UnsupportedResolutionError(res, list(ALLOWED_RESOLUTIONS))
    return res


def enforce_row_limit(count: int, *, settings: Settings | None = None, limit: int | None = None) -> None:
    settings = settings or get_settings()
    max_rows = limit if limit is not None else settings.max_rows_per_response
    if count > max_rows:
        raise TooManyRowsError(count, max_rows)


def clamp_limit(limit: int | None, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if limit is None:
        return min(200, settings.max_rows_per_response)
    if limit < 1:
        raise InvalidRangeError("'limit' must be >= 1", hint="Use a positive integer limit")
    return min(limit, settings.max_rows_per_response)
