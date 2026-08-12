"""Tool input validation before Query API calls."""

from __future__ import annotations

import json

import pytest
from mcp import Client

from tests.conftest import FakeQueryClient


def _payload(result) -> dict:
    text = result.content[0].text
    start = text.find("{")
    if start < 0:
        return {"message": text}
    return json.loads(text[start:])


@pytest.mark.asyncio
async def test_missing_start_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {"end": "2026-08-12T00:00:00Z"},
        )
    assert result.is_error is True
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_missing_end_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {"start": "2026-08-01T00:00:00Z"},
        )
    assert result.is_error is True
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_naive_start_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-01T00:00:00",
                "end": "2026-08-12T00:00:00Z",
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body.get("code") == "INVALID_TIME_RANGE"
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_end_not_after_start_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-12T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body["code"] == "INVALID_TIME_RANGE"
    assert "later than start" in body["message"]
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_invalid_timezone_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "timezone": "Not/A_Zone",
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body["code"] == "INVALID_TIMEZONE"
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_invalid_glucose_resolution_rejected(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_glucose_series",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-02T00:00:00Z",
                "resolution": "1d",
            },
        )
    assert result.is_error is True
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_oversized_raw_request_rejected_before_upstream(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_glucose_series",
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-09T00:00:00Z",
                "resolution": "raw",
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body["code"] == "RANGE_TOO_LARGE"
    assert body["max_days"] == 7
    assert "7 days" in body["message"]
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_invalid_summary_bucket_rejected(
    mcp_server, fake_query_client: FakeQueryClient
):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_glucose_summary",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "bucket": "weekly",
            },
        )
    assert result.is_error is True
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_meal_limit_below_one_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_meals",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "limit": 0,
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body.get("code") in {"INVALID_LIMIT", None} or "limit" in result.content[0].text.lower()
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_meal_limit_above_500_rejected(mcp_server, fake_query_client: FakeQueryClient):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_meals",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "limit": 501,
            },
        )
    assert result.is_error is True
    body = _payload(result)
    assert body.get("code") == "INVALID_LIMIT" or "501" in result.content[0].text
    assert fake_query_client.calls == []


@pytest.mark.asyncio
async def test_cursor_passed_through_opaque(mcp_server, fake_query_client: FakeQueryClient):
    cursor = "  weird/cursor+value==  "
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_meals",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
                "cursor": cursor,
            },
        )
    assert result.is_error is False
    assert fake_query_client.calls[0][1]["cursor"] == cursor
