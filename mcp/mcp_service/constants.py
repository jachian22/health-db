"""Query API limits mirrored for user-friendly MCP tool errors.

The Query API remains the source of truth and re-validates every request.
"""

from __future__ import annotations

DEFAULT_QUERY_TIMEZONE = "America/New_York"
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500
DEFAULT_MEAL_LIMIT = DEFAULT_PAGE_LIMIT
MAX_MEAL_LIMIT = MAX_PAGE_LIMIT

MAX_WORKOUT_RANGE_DAYS = 365
MAX_SLEEP_RANGE_DAYS = 90
MAX_WEIGHT_RANGE_DAYS = 365

DEFAULT_MEAL_LOOKBACK_DAYS = 30
MAX_MEAL_LOOKBACK_DAYS = 30

DEFAULT_SLEEP_LOOKBACK_HOURS = 24
MAX_SLEEP_LOOKBACK_HOURS = 36

DEFAULT_GLUCOSE_LOOKBACK_HOURS = 24
MAX_GLUCOSE_LOOKBACK_HOURS = 48

SNAPSHOT_WORKOUT_LOOKBACK_DAYS = 14
SNAPSHOT_WEIGHT_LOOKBACK_DAYS = 30

ALLOWED_GLUCOSE_RESOLUTIONS = ("raw", "5m", "15m", "hourly")
ALLOWED_SUMMARY_BUCKETS = ("overall", "daily")

RESOLUTION_MAX_DAYS: dict[str, int] = {
    "raw": 7,
    "5m": 31,
    "15m": 90,
    "hourly": 365,
}

RESOLUTION_LABELS: dict[str, str] = {
    "raw": "Raw",
    "5m": "5m",
    "15m": "15m",
    "hourly": "Hourly",
}
