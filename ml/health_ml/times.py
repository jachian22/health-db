"""Timezone-aware range helpers shared by the client, snapshot builder, and diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

from health_ml.errors import InvalidRangeError


def to_iso8601(value: datetime) -> str:
    text = value.astimezone(UTC).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def require_aware_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise InvalidRangeError("start and end must be timezone-aware ISO-8601 timestamps")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if end_utc <= start_utc:
        raise InvalidRangeError("end must be later than start")
    return start_utc, end_utc


def point_in_range(timestamp: datetime, start: datetime, end: datetime) -> bool:
    """Half-open point inclusion: start <= timestamp < end."""
    return start <= timestamp < end


def interval_overlaps(
    interval_start: datetime,
    interval_end: datetime,
    start: datetime,
    end: datetime,
) -> bool:
    """Overlap inclusion: interval.start < end AND interval.end > start."""
    return interval_start < end and interval_end > start


def interval_extends_beyond_bounds(
    interval_start: datetime,
    interval_end: datetime,
    start: datetime,
    end: datetime,
) -> bool:
    """True when the stored interval is not fully contained in [start, end)."""
    return interval_start < start or interval_end > end
