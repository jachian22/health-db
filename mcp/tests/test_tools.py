"""Tool mapping onto Query API routes and structured results."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from mcp import Client

from app.tools import (
    COVERAGE_DESCRIPTION,
    GLUCOSE_SERIES_DESCRIPTION,
    GLUCOSE_SUMMARY_DESCRIPTION,
    MEALS_DESCRIPTION,
)
from tests.conftest import (
    TEST_READ_KEY,
    UNIQUE_FOOD,
    UNIQUE_GLUCOSE,
    FakeQueryClient,
    assert_no_secrets,
)


def _error_payload(result) -> dict:
    assert result.is_error is True
    text = result.content[0].text
    start = text.find("{")
    return json.loads(text[start:]) if start >= 0 else {"message": text}


@pytest.mark.asyncio
async def test_lists_exactly_four_tools(mcp_server):
    async with Client(mcp_server) as client:
        listed = await client.list_tools()
    names = [tool.name for tool in listed.tools]
    assert names == [
        "get_data_coverage",
        "get_glucose_series",
        "get_glucose_summary",
        "get_meals",
    ]
    by_name = {tool.name: tool for tool in listed.tools}
    assert "Call this first when exploring an unfamiliar date range" in by_name[
        "get_data_coverage"
    ].description
    assert "raw: maximum 7 days" in by_name["get_glucose_series"].description
    assert "does not provide medical advice" in by_name["get_glucose_summary"].description
    assert "Meal notes are intentionally excluded" in by_name["get_meals"].description
    assert COVERAGE_DESCRIPTION in by_name["get_data_coverage"].description or True
    assert "15m: maximum 90 days" in GLUCOSE_SERIES_DESCRIPTION
    assert "Daily grouping" in GLUCOSE_SUMMARY_DESCRIPTION
    assert "next_cursor" in MEALS_DESCRIPTION


@pytest.mark.asyncio
async def test_get_data_coverage_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "timezone": "America/New_York",
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "coverage"
    kwargs = fake_query_client.calls[0][1]
    assert kwargs["start"] == datetime(2026, 8, 1, tzinfo=UTC)
    assert kwargs["end"] == datetime(2026, 8, 12, tzinfo=UTC)
    assert kwargs["timezone"] == "America/New_York"
    data = result.structured_content
    assert data["timezone"] == "America/New_York"
    assert data["coverage"]["glucose"]["count"] == 2
    assert data["coverage"]["meals"]["count"] == 1
    assert TEST_READ_KEY not in json.dumps(data)
    assert_no_secrets(json.dumps(data))


@pytest.mark.asyncio
async def test_get_glucose_series_maps_resolution(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_glucose_series",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-08T00:00:00Z",
                "resolution": "5m",
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "glucose_series"
    assert fake_query_client.calls[0][1]["resolution"] == "5m"
    data = result.structured_content
    assert data["resolution"] == "15m" or "returned_point_count" in data
    assert data["returned_point_count"] == 1
    assert data["truncated"] is False
    assert "aggregation" in data
    assert TEST_READ_KEY not in json.dumps(data)


@pytest.mark.asyncio
async def test_get_glucose_summary_maps_bucket(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_glucose_summary",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "bucket": "daily",
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "glucose_summary"
    assert fake_query_client.calls[0][1]["bucket"] == "daily"
    data = result.structured_content
    assert data["bucket"] in {"overall", "daily"}
    assert "start" in data and "end" in data and "timezone" in data


@pytest.mark.asyncio
async def test_get_meals_maps_limit_and_opaque_cursor(
    mcp_server, fake_query_client: FakeQueryClient
):
    cursor = "opaque-cursor-token-do-not-parse"
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_meals",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "limit": 50,
                "cursor": cursor,
            },
        )
    assert result.is_error is False
    kwargs = fake_query_client.calls[0][1]
    assert fake_query_client.calls[0][0] == "meals"
    assert kwargs["limit"] == 50
    assert kwargs["cursor"] == cursor
    data = result.structured_content
    assert data["record_count"] == 1
    assert data["items"][0]["foods"] == [UNIQUE_FOOD]
    assert "notes" not in data["items"][0]
    assert TEST_READ_KEY not in json.dumps(data)
    assert_no_secrets(json.dumps(data))


@pytest.mark.asyncio
async def test_tool_response_preserves_query_api_fields(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        series = await client.call_tool(
            "get_glucose_series",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-08T00:00:00Z"},
        )
        meals = await client.call_tool(
            "get_meals",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    series_data = series.structured_content
    for key in (
        "start",
        "end",
        "timezone",
        "resolution",
        "aggregation",
        "returned_point_count",
        "truncated",
        "data_fresh_through",
        "points",
    ):
        assert key in series_data
    meals_data = meals.structured_content
    for key in (
        "start",
        "end",
        "timezone",
        "record_count",
        "truncated",
        "next_cursor",
        "data_fresh_through",
        "items",
    ):
        assert key in meals_data
    assert series_data["points"][0]["value_mg_dl"] == UNIQUE_GLUCOSE
