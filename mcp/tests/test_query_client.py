"""Query API client HTTP mapping and upstream error translation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from mcp import Client

from mcp_service.errors import QueryAPIError
from mcp_service.query_api_client import (
    CONTEXT_SNAPSHOT_PATH,
    COVERAGE_PATH,
    GLUCOSE_SERIES_PATH,
    GLUCOSE_SUMMARY_PATH,
    LAST_LOGGED_MEAL_PATH,
    MEALS_PATH,
    PERSONAL_TIMELINE_PATH,
    READY_PATH,
    SLEEP_INTERVALS_PATH,
    WEIGHT_MEASUREMENTS_PATH,
    WORKOUTS_PATH,
    HealthDBQueryAPIClient,
)
from mcp_service.tools import build_mcp_server
from tests.conftest import (
    ANCHOR,
    TEST_READ_KEY,
    FakeQueryClient,
    assert_no_secrets,
    default_context_snapshot,
    default_coverage,
    default_last_logged_meal,
    default_personal_timeline,
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
        if request.url.path in {
            WORKOUTS_PATH,
            SLEEP_INTERVALS_PATH,
            WEIGHT_MEASUREMENTS_PATH,
        }:
            assert request.url.params["limit"] == "10"
            assert request.url.params["cursor"] == "page-2"
            assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"
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
    await client.get_workouts(
        start=START, end=END, timezone="America/New_York", limit=10, cursor="page-2"
    )
    await client.get_sleep_intervals(
        start=START, end=END, timezone="America/New_York", limit=10, cursor="page-2"
    )
    await client.get_weight_measurements(
        start=START, end=END, timezone="America/New_York", limit=10, cursor="page-2"
    )
    await client.aclose()
    assert paths == [
        GLUCOSE_SERIES_PATH,
        GLUCOSE_SUMMARY_PATH,
        MEALS_PATH,
        WORKOUTS_PATH,
        SLEEP_INTERVALS_PATH,
        WEIGHT_MEASUREMENTS_PATH,
    ]


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
        (
            query_error(
                "RESULT_TOO_LARGE",
                "Personal timeline matched more than 2000 sleep_intervals records; narrow the time range",
                max_items=2000,
                category="sleep_intervals",
            ),
            "RESULT_TOO_LARGE",
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
    if exc.code == "RESULT_TOO_LARGE":
        assert body["max_items"] == 2000
        assert body["category"] == "sleep_intervals"


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


@pytest.mark.asyncio
async def test_last_logged_meal_and_snapshot_routes():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"
        if request.url.path == LAST_LOGGED_MEAL_PATH:
            assert request.url.params["anchor"].endswith("Z") or "2026-08-15" in request.url.params["anchor"]
            assert request.url.params["lookback_days"] == "14"
            return httpx.Response(200, json=default_last_logged_meal().model_dump(mode="json"))
        if request.url.path == CONTEXT_SNAPSHOT_PATH:
            assert request.url.params["meal_lookback_days"] == "10"
            assert request.url.params["sleep_lookback_hours"] == "12"
            assert request.url.params["glucose_lookback_hours"] == "6"
            return httpx.Response(200, json=default_context_snapshot().model_dump(mode="json"))
        return httpx.Response(404, json={"error": {"code": "NO", "message": "no"}})

    client = _client_for(handler)
    meal = await client.get_last_logged_meal(
        anchor=ANCHOR, timezone="America/New_York", lookback_days=14
    )
    snapshot = await client.get_context_snapshot(
        anchor=ANCHOR,
        timezone="America/New_York",
        meal_lookback_days=10,
        sleep_lookback_hours=12,
        glucose_lookback_hours=6,
    )
    await client.aclose()
    assert meal.meal is not None
    assert "points" not in snapshot.model_dump()
    assert [request.url.path for request in seen] == [
        LAST_LOGGED_MEAL_PATH,
        CONTEXT_SNAPSHOT_PATH,
    ]


@pytest.mark.asyncio
async def test_last_logged_meal_maps_422_and_401():
    def handler_422(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "INVALID_LOOKBACK",
                    "message": "lookback_days must be a positive integer",
                }
            },
        )

    client = _client_for(handler_422)
    with pytest.raises(QueryAPIError) as raised:
        await client.get_last_logged_meal(
            anchor=ANCHOR, timezone="America/New_York", lookback_days=1
        )
    await client.aclose()
    assert raised.value.code == "INVALID_LOOKBACK"
    assert TEST_READ_KEY not in raised.value.message

    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "LEAK", "message": "raw body with secret test-read-key"}},
        )

    client = _client_for(handler_401)
    with pytest.raises(QueryAPIError) as raised_401:
        await client.get_context_snapshot(
            anchor=ANCHOR,
            timezone="America/New_York",
            meal_lookback_days=30,
            sleep_lookback_hours=24,
            glucose_lookback_hours=24,
        )
    await client.aclose()
    assert raised_401.value.code == "UPSTREAM_UNAVAILABLE"
    assert TEST_READ_KEY not in raised_401.value.message
    assert "raw body" not in raised_401.value.message


@pytest.mark.asyncio
async def test_personal_timeline_route_iso_z_and_result_too_large():
    seen: list[httpx.Request] = []

    def handler_ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=default_personal_timeline().model_dump(mode="json")
        )

    client = _client_for(handler_ok)
    result = await client.get_personal_timeline(
        start=datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
        end=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
        timezone="America/New_York",
    )
    await client.aclose()
    assert result.glucose_resolution == "15m"
    request = seen[0]
    assert request.url.path == PERSONAL_TIMELINE_PATH
    assert request.url.params["start"].endswith("Z") or "2026-08-10" in request.url.params["start"]
    assert request.url.params["end"].endswith("Z") or "2026-08-13" in request.url.params["end"]
    assert request.url.params["timezone"] == "America/New_York"
    assert "resolution" not in request.url.params
    assert "limit" not in request.url.params
    assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"

    def handler_422(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "RESULT_TOO_LARGE",
                    "message": (
                        "Personal timeline matched more than 2000 sleep_intervals "
                        "records; narrow the time range"
                    ),
                    "details": {"max_items": 2000, "category": "sleep_intervals"},
                }
            },
        )

    client = _client_for(handler_422)
    with pytest.raises(QueryAPIError) as raised:
        await client.get_personal_timeline(
            start=datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
            end=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
            timezone="America/New_York",
        )
    await client.aclose()
    assert raised.value.code == "RESULT_TOO_LARGE"
    assert raised.value.extra["max_items"] == 2000
    assert raised.value.extra["category"] == "sleep_intervals"
    assert TEST_READ_KEY not in raised.value.message
