"""Query API limits mirrored for user-friendly MCP tool errors.

The Query API remains the source of truth and re-validates every request.
"""

from __future__ import annotations

DEFAULT_QUERY_TIMEZONE = "America/New_York"
DEFAULT_MEAL_LIMIT = 100
MAX_MEAL_LIMIT = 500

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
