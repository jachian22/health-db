"""Tool mapping onto Query API routes and structured results."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from mcp import Client

from mcp_service.tools import (
    CONTEXT_SNAPSHOT_DESCRIPTION,
    COVERAGE_DESCRIPTION,
    GLUCOSE_SERIES_DESCRIPTION,
    GLUCOSE_SUMMARY_DESCRIPTION,
    LAST_LOGGED_MEAL_DESCRIPTION,
    MEALS_DESCRIPTION,
    PERSONAL_TIMELINE_DESCRIPTION,
    SLEEP_INTERVALS_DESCRIPTION,
    WEIGHT_MEASUREMENTS_DESCRIPTION,
    WORKOUTS_DESCRIPTION,
)
from tests.conftest import (
    TEST_READ_KEY,
    UNIQUE_FOOD,
    UNIQUE_GLUCOSE,
    UNIQUE_KG,
    UNIQUE_SLEEP_ID,
    UNIQUE_STAGE,
    UNIQUE_WEIGHT_ID,
    UNIQUE_WORKOUT_ID,
    FakeQueryClient,
    assert_no_secrets,
    empty_last_logged_meal,
    empty_sleep_intervals,
)


def _error_payload(result) -> dict:
    assert result.is_error is True
    text = result.content[0].text
    start = text.find("{")
    return json.loads(text[start:]) if start >= 0 else {"message": text}


@pytest.mark.asyncio
async def test_lists_exactly_ten_tools(mcp_server):
    async with Client(mcp_server) as client:
        listed = await client.list_tools()
    names = [tool.name for tool in listed.tools]
    assert names == [
        "get_data_coverage",
        "get_glucose_series",
        "get_glucose_summary",
        "get_meals",
        "get_workouts",
        "get_sleep_intervals",
        "get_weight_measurements",
        "get_last_logged_meal",
        "build_context_snapshot",
        "get_personal_timeline",
    ]
    by_name = {tool.name: tool for tool in listed.tools}
    assert "Call this first when exploring an unfamiliar date range" in by_name[
        "get_data_coverage"
    ].description
    assert "raw: maximum 7 days" in by_name["get_glucose_series"].description
    assert "does not provide medical advice" in by_name["get_glucose_summary"].description
    assert "Meal notes are intentionally excluded" in by_name["get_meals"].description
    assert by_name["get_data_coverage"].description == COVERAGE_DESCRIPTION
    assert by_name["get_workouts"].description == WORKOUTS_DESCRIPTION
    assert by_name["get_sleep_intervals"].description == SLEEP_INTERVALS_DESCRIPTION
    assert by_name["get_weight_measurements"].description == WEIGHT_MEASUREMENTS_DESCRIPTION
    assert by_name["get_last_logged_meal"].description == LAST_LOGGED_MEAL_DESCRIPTION
    assert by_name["build_context_snapshot"].description == CONTEXT_SNAPSHOT_DESCRIPTION
    assert by_name["get_personal_timeline"].description == PERSONAL_TIMELINE_DESCRIPTION
    timeline_schema = by_name["get_personal_timeline"].inputSchema
    timeline_props = timeline_schema.get("properties") or {}
    assert set(timeline_props) == {"start", "end", "timezone"}
    assert "resolution" not in timeline_props
    assert "limit" not in timeline_props
    assert "cursor" not in timeline_props
    assert "bucket" not in timeline_props
    assert "latest logged meal" in LAST_LOGGED_MEAL_DESCRIPTION.lower()
    assert "foods are included" in LAST_LOGGED_MEAL_DESCRIPTION.lower()
    assert "meal notes are excluded" in LAST_LOGGED_MEAL_DESCRIPTION.lower()
    assert "does not establish fasting" in LAST_LOGGED_MEAL_DESCRIPTION.lower()
    assert "no medical advice" in LAST_LOGGED_MEAL_DESCRIPTION.lower()
    assert "bounded, evidence-only context" in CONTEXT_SNAPSHOT_DESCRIPTION.lower()
    assert "does not return a glucose series" in CONTEXT_SNAPSHOT_DESCRIPTION.lower()
    assert "does not confirm fasting" in CONTEXT_SNAPSHOT_DESCRIPTION.lower()
    assert "15m: maximum 90 days" in GLUCOSE_SERIES_DESCRIPTION
    assert "Daily grouping" in GLUCOSE_SUMMARY_DESCRIPTION
    assert "next_cursor" in MEALS_DESCRIPTION
    assert "overlap" in WORKOUTS_DESCRIPTION
    assert "interval overlap" in COVERAGE_DESCRIPTION
    assert "may differ" not in COVERAGE_DESCRIPTION
    assert "may differ" not in WORKOUTS_DESCRIPTION
    assert "may differ" not in SLEEP_INTERVALS_DESCRIPTION
    assert "not sessionized" in SLEEP_INTERVALS_DESCRIPTION
    assert "value_kg" in WEIGHT_MEASUREMENTS_DESCRIPTION


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
    assert data["resolution"] == "5m"
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
    assert data["bucket"] == "daily"
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


_LIST_ENVELOPE = (
    "start",
    "end",
    "timezone",
    "record_count",
    "truncated",
    "next_cursor",
    "data_fresh_through",
    "items",
)


@pytest.mark.asyncio
async def test_get_workouts_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    cursor = "opaque-workout-cursor"
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_workouts",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "limit": 25,
                "cursor": cursor,
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "workouts"
    kwargs = fake_query_client.calls[0][1]
    assert kwargs["limit"] == 25
    assert kwargs["cursor"] == cursor
    data = result.structured_content
    for key in _LIST_ENVELOPE:
        assert key in data
    item = data["items"][0]
    assert item["id"] == UNIQUE_WORKOUT_ID
    assert item["sport"] == "running"
    assert "average_heart_rate" not in item
    assert "active_energy_kcal" not in item
    assert "source_name" not in item
    assert_no_secrets(json.dumps(data))


@pytest.mark.asyncio
async def test_get_sleep_intervals_empty_envelope(
    mcp_server, fake_query_client: FakeQueryClient
):
    fake_query_client.sleep_intervals = empty_sleep_intervals()
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_sleep_intervals",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    assert result.is_error is False
    data = result.structured_content
    assert data["items"] == []
    assert data["record_count"] == 0
    assert data["truncated"] is False
    assert data["next_cursor"] is None
    assert data["data_fresh_through"] is None
    for key in _LIST_ENVELOPE:
        assert key in data


@pytest.mark.asyncio
async def test_get_sleep_intervals_preserves_raw_stage(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_sleep_intervals",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    data = result.structured_content
    assert data["items"][0]["id"] == UNIQUE_SLEEP_ID
    assert data["items"][0]["stage"] == UNIQUE_STAGE
    assert "quality" not in data["items"][0]


@pytest.mark.asyncio
async def test_get_weight_measurements_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_weight_measurements",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "weight_measurements"
    data = result.structured_content
    for key in _LIST_ENVELOPE:
        assert key in data
    item = data["items"][0]
    assert item["id"] == UNIQUE_WEIGHT_ID
    assert item["value_kg"] == UNIQUE_KG
    assert "unit" not in item
    assert "lb" not in json.dumps(data)


@pytest.mark.asyncio
async def test_get_last_logged_meal_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_last_logged_meal",
            {
                "anchor": "2026-08-15T14:00:00Z",
                "timezone": "America/New_York",
                "lookback_days": 14,
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "last_logged_meal"
    kwargs = fake_query_client.calls[0][1]
    assert kwargs == {
        "anchor": datetime(2026, 8, 15, 14, 0, tzinfo=UTC),
        "timezone": "America/New_York",
        "lookback_days": 14,
    }
    data = result.structured_content
    assert data["meal"]["foods"] == [UNIQUE_FOOD]
    assert data["lookback_days"] == 30
    assert "notes" not in data["meal"]
    assert_no_secrets(json.dumps(data))


@pytest.mark.asyncio
async def test_get_last_logged_meal_missing_passes_through_nulls(
    mcp_server, fake_query_client: FakeQueryClient
):
    fake_query_client.last_logged_meal = empty_last_logged_meal()
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_last_logged_meal",
            {"anchor": "2026-08-15T14:00:00Z"},
        )
    assert result.is_error is False
    data = result.structured_content
    assert data["meal"] is None
    assert data["derived"]["minutes_since_last_logged_meal"] is None
    assert data["derived"]["basis"] is None


@pytest.mark.asyncio
async def test_build_context_snapshot_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "build_context_snapshot",
            {
                "anchor": "2026-08-15T14:00:00Z",
                "meal_lookback_days": 10,
                "sleep_lookback_hours": 12,
                "glucose_lookback_hours": 6,
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][0] == "context_snapshot"
    kwargs = fake_query_client.calls[0][1]
    assert kwargs == {
        "anchor": datetime(2026, 8, 15, 14, 0, tzinfo=UTC),
        "timezone": "America/New_York",
        "meal_lookback_days": 10,
        "sleep_lookback_hours": 12,
        "glucose_lookback_hours": 6,
    }
    data = result.structured_content
    assert data["last_logged_meal"]["foods"] == [UNIQUE_FOOD]
    assert "points" not in data
    assert "stage" not in data["recent_sleep_intervals"]
    assert_no_secrets(json.dumps(data))


@pytest.mark.asyncio
async def test_get_personal_timeline_maps_to_query_client(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_personal_timeline",
            {
                "start": "2026-08-10T04:00:00Z",
                "end": "2026-08-13T04:00:00Z",
                "timezone": "America/New_York",
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls == [
        (
            "personal_timeline",
            {
                "start": datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
                "end": datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
                "timezone": "America/New_York",
            },
        )
    ]
    data = result.structured_content
    assert data["glucose_resolution"] == "15m"
    assert data["glucose"]["aggregation"] == "mean_min_max"
    assert data["glucose"]["truncated"] is False
    assert data["meals"][0]["foods"] == [UNIQUE_FOOD]
    assert "notes" not in data["meals"][0]
    assert data["sleep_intervals"][0]["stage"] == UNIQUE_STAGE
    assert "resolution" not in data["glucose"]
    assert "next_cursor" not in data
    assert "record_count" not in data
    assert "truncated" not in data
    assert_no_secrets(json.dumps(data))
