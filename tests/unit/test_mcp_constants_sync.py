"""MCP mirrors Query API day/page limits; drift must fail tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core import (
    ALLOWED_GLUCOSE_RESOLUTIONS,
    DEFAULT_GLUCOSE_LOOKBACK_HOURS,
    DEFAULT_MEAL_LOOKBACK_DAYS,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    DEFAULT_SLEEP_LOOKBACK_HOURS,
    MAX_GLUCOSE_LOOKBACK_HOURS,
    MAX_MEAL_LOOKBACK_DAYS,
    MAX_PAGE_LIMIT,
    MAX_SLEEP_LOOKBACK_HOURS,
    MAX_SLEEP_RANGE_DAYS,
    MAX_TIMELINE_ITEMS_PER_CATEGORY,
    MAX_TIMELINE_RANGE_HOURS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
    RESOLUTION_MAX_DAYS,
    SNAPSHOT_WEIGHT_LOOKBACK_DAYS,
    SNAPSHOT_WORKOUT_LOOKBACK_DAYS,
)

MCP_CONSTANTS = (
    Path(__file__).resolve().parents[2] / "mcp" / "mcp_service" / "constants.py"
)


def _load_mcp_constants():
    spec = importlib.util.spec_from_file_location("mcp_constants_sync", MCP_CONSTANTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_limit_constants_match_query_api():
    mcp = _load_mcp_constants()
    assert mcp.DEFAULT_QUERY_TIMEZONE == DEFAULT_QUERY_TIMEZONE
    assert mcp.DEFAULT_PAGE_LIMIT == DEFAULT_PAGE_LIMIT
    assert mcp.MAX_PAGE_LIMIT == MAX_PAGE_LIMIT
    assert mcp.MAX_WORKOUT_RANGE_DAYS == MAX_WORKOUT_RANGE_DAYS
    assert mcp.MAX_SLEEP_RANGE_DAYS == MAX_SLEEP_RANGE_DAYS
    assert mcp.MAX_WEIGHT_RANGE_DAYS == MAX_WEIGHT_RANGE_DAYS
    assert mcp.RESOLUTION_MAX_DAYS == RESOLUTION_MAX_DAYS
    assert mcp.ALLOWED_GLUCOSE_RESOLUTIONS == ALLOWED_GLUCOSE_RESOLUTIONS
    assert mcp.DEFAULT_MEAL_LOOKBACK_DAYS == DEFAULT_MEAL_LOOKBACK_DAYS
    assert mcp.MAX_MEAL_LOOKBACK_DAYS == MAX_MEAL_LOOKBACK_DAYS
    assert mcp.DEFAULT_SLEEP_LOOKBACK_HOURS == DEFAULT_SLEEP_LOOKBACK_HOURS
    assert mcp.MAX_SLEEP_LOOKBACK_HOURS == MAX_SLEEP_LOOKBACK_HOURS
    assert mcp.DEFAULT_GLUCOSE_LOOKBACK_HOURS == DEFAULT_GLUCOSE_LOOKBACK_HOURS
    assert mcp.MAX_GLUCOSE_LOOKBACK_HOURS == MAX_GLUCOSE_LOOKBACK_HOURS
    assert mcp.SNAPSHOT_WORKOUT_LOOKBACK_DAYS == SNAPSHOT_WORKOUT_LOOKBACK_DAYS
    assert mcp.SNAPSHOT_WEIGHT_LOOKBACK_DAYS == SNAPSHOT_WEIGHT_LOOKBACK_DAYS
    assert mcp.MAX_TIMELINE_RANGE_HOURS == MAX_TIMELINE_RANGE_HOURS
    assert mcp.MAX_TIMELINE_ITEMS_PER_CATEGORY == MAX_TIMELINE_ITEMS_PER_CATEGORY
