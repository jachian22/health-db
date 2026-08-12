"""Shared constants and helpers."""

from __future__ import annotations

ALLOWED_GLUCOSE_RESOLUTIONS = ("raw", "5m", "15m", "hourly")

ALLOWED_SLEEP_STAGES = frozenset(
    {"asleep", "core", "deep", "rem", "awake", "unspecified", "unknown"}
)

RESOLUTION_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "hourly": 3600,
}

# Hard maximum span (days) per glucose resolution — reject rather than alter.
RESOLUTION_MAX_DAYS: dict[str, int] = {
    "raw": 7,
    "5m": 31,
    "15m": 90,
    "hourly": 365,
}

DEFAULT_QUERY_TIMEZONE = "America/New_York"
DEFAULT_MEAL_LIMIT = 100
MAX_MEAL_LIMIT = 500
