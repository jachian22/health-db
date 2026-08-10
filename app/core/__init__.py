"""Shared constants and helpers."""

from __future__ import annotations

ALLOWED_GLUCOSE_RESOLUTIONS = ("raw", "5m", "15m", "1h", "1d")

ALLOWED_SLEEP_STAGES = frozenset(
    {"asleep", "core", "deep", "rem", "awake", "unspecified", "unknown"}
)

RESOLUTION_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}
