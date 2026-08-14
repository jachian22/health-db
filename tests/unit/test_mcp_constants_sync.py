"""MCP mirrors Query API day/page limits; drift must fail tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core import (
    ALLOWED_GLUCOSE_RESOLUTIONS,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_PAGE_LIMIT,
    MAX_SLEEP_RANGE_DAYS,
    MAX_WEIGHT_RANGE_DAYS,
    MAX_WORKOUT_RANGE_DAYS,
    RESOLUTION_MAX_DAYS,
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
