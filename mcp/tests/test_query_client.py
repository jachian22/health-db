"""Query API client HTTP mapping and upstream error translation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from mcp import Client

from mcp_service.errors import QueryAPIError
from mcp_service.query_api_client import (
    COVERAGE_PATH,
    GLUCOSE_SERIES_PATH,
    GLUCOSE_SUMMARY_PATH,
    MEALS_PATH,
    READY_PATH,
    HealthDBQueryAPIClient,
)
from mcp_service.tools import build_mcp_server
from tests.conftest import (
    TEST_READ_KEY,
    FakeQueryClient,
    assert_no_secrets,
    default_coverage,
    make_settings,
    query_error,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 12, tzinfo=UTC)


def _client_for(handler) -> HealthDBQueryAPIClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="http://query-api.test",
        headers={
            "Authorization": f"Bearer {TEST_READ_KEY}",
            "Accept": "application/json",
        },
    )
    return HealthDBQueryAPIClient(make_settings(), http_client=http)


@pytest.mark.asyncio
async def test_coverage_calls_correct_route_with_read_key():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=default_coverage().model_dump(mode="json"))

    client = _client_for(handler)
    result = await client.get_coverage(start=START, end=END, timezone="America/New_York")
    await client.aclose()
    assert result.coverage.glucose.count == 2
    request = seen[0]
    assert request.url.path == COVERAGE_PATH
    assert request.url.params["start"].endswith("Z") or "2026-08-01" in request.url.params["start"]
    assert request.url.params["timezone"] == "America/New_York"
    assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"
    assert request.headers["accept"] == "application/json"


@pytest.mark.asyncio
async def test_series_summary_meals_routes():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == GLUCOSE_SERIES_PATH:
            body = {
                "request_id": "x",
                "start": START.isoformat(),
                "end": END.isoformat(),
                "timezone": "America/New_York",
                "resolution": request.url.params.get("resolution"),
                "aggregation": "mean_min_max",
                "source_record_count": 0,
                "returned_point_count": 0,
                "truncated": False,
                "points": [],
            }
            return httpx.Response(200, json=body)
        if request.url.path == GLUCOSE_SUMMARY_PATH:
            return httpx.Response(
                200,
                json={
                    "request_id": "x",
                    "start": START.isoformat(),
                    "end": END.isoformat(),
                    "timezone": "America/New_York",
                    "bucket": request.url.params.get("bucket"),
                    "summary": {"sample_count": 0},
                },
            )
        if request.url.path == MEALS_PATH:
            assert request.url.params["limit"] == "25"
            assert request.url.params["cursor"] == "abc"
            return httpx.Response(
                200,
                json={
                    "request_id": "x",
                    "start": START.isoformat(),
                    "end": END.isoformat(),
                    "timezone": "America/New_York",
                    "record_count": 0,
                    "truncated": False,
                    "items": [],
                },
            )
        return httpx.Response(404, json={"error": {"code": "NO", "message": "no"}})

    client = _client_for(handler)
    await client.get_glucose_series(
        start=START, end=END, resolution="hourly", timezone="America/New_York"
    )
    await client.get_glucose_summary(
        start=START, end=END, bucket="overall", timezone="America/New_York"
    )
    await client.get_meals(
        start=START, end=END, timezone="America/New_York", limit=25, cursor="abc"
    )
    await client.aclose()
    assert paths == [GLUCOSE_SERIES_PATH, GLUCOSE_SUMMARY_PATH, MEALS_PATH]


@pytest.mark.asyncio
async def test_does_not_forward_caller_authorization():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"
        assert "mcp" not in request.headers["authorization"].lower()
        return httpx.Response(200, json=default_coverage().model_dump(mode="json"))

    client = _client_for(handler)
    await client.get_coverage(start=START, end=END, timezone="America/New_York")
    await client.aclose()


@pytest.mark.asyncio
async def test_query_api_200_returns_structured_tool_data():
    fake = FakeQueryClient()
    mcp = build_mcp_server(make_settings(), fake)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_data_coverage",
            {"start": "2026-08-01T00:00:00Z", "end": "2026-08-12T00:00:00Z"},
        )
    assert result.is_error is False
    assert result.structured_content["coverage"]["glucose"]["count"] == 2


def _tool_error_body(result) -> dict:
    text = result.content[0].text
    return json.loads(text[text.find("{") :])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "code_fragment"),
    [
        (
            query_error("RANGE_TOO_LARGE", "Raw glucose queries are limited to 7 days", max_days=7),
            "RANGE_TOO_LARGE",
        ),
        (query_error("UPSTREAM_RATE_LIMITED", "The health data service is rate-limiting requests"), "UPSTREAM_RATE_LIMITED"),
        (query_error("UPSTREAM_UNAVAILABLE", "The health data service is unavailable"), "UPSTREAM_UNAVAILABLE"),
        (query_error("UPSTREAM_TIMEOUT", "The health data service timed out"), "UPSTREAM_TIMEOUT"),
        (
            query_error("UPSTREAM_RESPONSE_ERROR", "The health data service returned an unexpected response"),
            "UPSTREAM_RESPONSE_ERROR",
        ),
    ],
)
async def test_upstream_errors_map_to_safe_tool_errors(exc: QueryAPIError, code_fragment: str):
    fake = FakeQueryClient(error=exc)
    mcp = build_mcp_server(make_settings(), fake)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_glucose_series",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-02T00:00:00Z",
                "resolution": "raw",
            },
        )
    assert result.is_error is True
    body = _tool_error_body(result)
    assert body["code"] == code_fragment
    assert TEST_READ_KEY not in result.content[0].text
    assert "traceback" not in result.content[0].text.lower()
    assert_no_secrets(result.content[0].text)
    if exc.code == "RANGE_TOO_LARGE":
        assert body["max_days"] == 7


@pytest.mark.asyncio
async def test_http_status_mapping():
    cases = [
        (401, "UPSTREAM_UNAVAILABLE", "unavailable"),
        (429, "UPSTREAM_RATE_LIMITED", "rate-limiting"),
        (500, "UPSTREAM_UNAVAILABLE", "unavailable"),
        (503, "UPSTREAM_UNAVAILABLE", "unavailable"),
        (
            422,
            "RANGE_TOO_LARGE",
            "limited to 7 days",
        ),
    ]
    for status, code, fragment in cases:

        def handler(request: httpx.Request, status=status) -> httpx.Response:
            if status == 422:
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "RANGE_TOO_LARGE",
                            "message": "Raw glucose queries are limited to 7 days",
                            "details": {"max_days": 7},
                        }
                    },
                )
            return httpx.Response(
                status,
                json={"error": {"code": "LEAK", "message": "raw body with secret test-read-key"}},
            )

        client = _client_for(handler)
        with pytest.raises(QueryAPIError) as raised:
            await client.get_coverage(start=START, end=END, timezone="America/New_York")
        await client.aclose()
        assert raised.value.code == code
        assert fragment in raised.value.message
        assert TEST_READ_KEY not in raised.value.message
        assert "raw body" not in raised.value.message


@pytest.mark.asyncio
async def test_timeout_maps_to_upstream_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("took too long")

    client = _client_for(handler)
    with pytest.raises(QueryAPIError) as raised:
        await client.get_coverage(start=START, end=END, timezone="America/New_York")
    await client.aclose()
    assert raised.value.code == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
async def test_malformed_upstream_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = _client_for(handler)
    with pytest.raises(QueryAPIError) as raised:
        await client.get_coverage(start=START, end=END, timezone="America/New_York")
    await client.aclose()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"
    assert "not-json" not in raised.value.message


@pytest.mark.asyncio
async def test_malformed_upstream_schema():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = _client_for(handler)
    with pytest.raises(QueryAPIError) as raised:
        await client.get_coverage(start=START, end=END, timezone="America/New_York")
    await client.aclose()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"
    assert "Field required" not in raised.value.message
    assert TEST_READ_KEY not in raised.value.message


@pytest.mark.asyncio
async def test_check_ready_does_not_send_read_key():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client = _client_for(handler)
    ok = await client.check_ready()
    await client.aclose()
    assert ok is True
    assert seen[0].url.path == READY_PATH
    assert "authorization" not in seen[0].headers
