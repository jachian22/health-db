"""Privacy-safe logging."""

from __future__ import annotations

import logging

import pytest
from mcp import Client

from tests.conftest import (
    TEST_INGEST_KEY,
    TEST_MCP_KEY,
    TEST_READ_KEY,
    UNIQUE_FOOD,
    UNIQUE_GLUCOSE,
    UNIQUE_KG,
    UNIQUE_SLEEP_ID,
    UNIQUE_SOURCE_ID,
    UNIQUE_STAGE,
    UNIQUE_WEIGHT_ID,
    UNIQUE_WORKOUT_ID,
    FakeQueryClient,
)


@pytest.mark.asyncio
async def test_logs_safe_metadata_not_health_values(
    mcp_server, fake_query_client: FakeQueryClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="mcp_service")
    async with Client(mcp_server) as client:
        await client.call_tool(
            "get_glucose_series",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-08T00:00:00Z",
                "resolution": "15m",
            },
        )
        await client.call_tool(
            "get_meals",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
        await client.call_tool(
            "get_workouts",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
        await client.call_tool(
            "get_sleep_intervals",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
        await client.call_tool(
            "get_weight_measurements",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    text = caplog.text
    assert "get_glucose_series" in text
    assert "get_meals" in text
    assert "get_workouts" in text
    assert "get_sleep_intervals" in text
    assert "get_weight_measurements" in text
    assert "latency_ms=" in text
    assert "outcome=ok" in text
    assert "http_status=" not in text
    assert "record_count=" in text
    assert TEST_MCP_KEY not in text
    assert TEST_READ_KEY not in text
    assert TEST_INGEST_KEY not in text
    assert "Authorization" not in text
    assert str(UNIQUE_GLUCOSE) not in text
    assert UNIQUE_FOOD not in text
    assert UNIQUE_SOURCE_ID not in text
    assert UNIQUE_WORKOUT_ID not in text
    assert UNIQUE_SLEEP_ID not in text
    assert UNIQUE_WEIGHT_ID not in text
    assert UNIQUE_STAGE not in text
    assert str(UNIQUE_KG) not in text
    assert "310" not in text
    assert "148" not in text
    assert "postgresql" not in text.lower()
