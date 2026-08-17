"""Health Query API client tests — mocked HTTP only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from health_ml.clients.health_api import (
    GLUCOSE_SERIES_PATH,
    SLEEP_INTERVALS_PATH,
    WEIGHT_MEASUREMENTS_PATH,
    WORKOUTS_PATH,
    iter_windows,
)
from health_ml.errors import HealthAPIError, InvalidRangeError
from tests.conftest import (
    END,
    START,
    TEST_READ_KEY,
    client_for,
    glucose_series_body,
    iso,
    paged_body,
)

T0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)


def test_successful_glucose_response_maps_to_canonical_records():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": iso(T0), "value_mg_dl": 96.0}],
                start=START,
                end=START + timedelta(days=1),
            ),
        )

    client = client_for(handler)
    records = client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert len(records) == 1
    assert records[0].timestamp == T0
    assert records[0].glucose_mg_dl == 96.0
    assert records[0].trend is None
    assert records[0].source is None
    request = seen[0]
    assert request.url.path == GLUCOSE_SERIES_PATH
    assert request.url.params["resolution"] == "raw"
    assert request.headers["authorization"] == f"Bearer {TEST_READ_KEY}"


def test_empty_glucose_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=glucose_series_body([]))

    client = client_for(handler)
    assert client.get_glucose(START, START + timedelta(days=1)) == []
    client.close()


def test_authentication_failure_is_not_retried():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            401,
            json={"error": {"code": "UNAUTHORIZED", "message": f"bad {TEST_READ_KEY}"}},
        )

    client = client_for(handler, max_retries=3)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "AUTHENTICATION_FAILED"
    assert TEST_READ_KEY not in raised.value.message
    assert attempts["count"] == 1


def test_malformed_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"
    assert "not-json" not in raised.value.message


def test_malformed_schema_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_meals(START, END)
    client.close()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"


def test_transient_failure_is_retried_then_succeeds():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, json={"error": {"code": "DOWN", "message": "down"}})
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": iso(T0), "value_mg_dl": 101.0}],
                start=START,
                end=START + timedelta(days=1),
            ),
        )

    client = client_for(handler, max_retries=2)
    records = client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert attempts["count"] == 2
    assert records[0].glucose_mg_dl == 101.0


def test_timeout_is_retried_then_fails():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.TimeoutException("took too long")

    client = client_for(handler, max_retries=2)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "UPSTREAM_TIMEOUT"
    assert attempts["count"] == 3


def test_non_retryable_validation_error():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
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

    client = client_for(handler, max_retries=3)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "RANGE_TOO_LARGE"
    assert attempts["count"] == 1
    assert raised.value.extra["max_days"] == 7


def test_meals_pagination_is_followed():
    seen_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        seen_cursors.append(cursor)
        if cursor is None:
            return httpx.Response(
                200,
                json=paged_body(
                    items=[
                        {
                            "id": "meal-1",
                            "meal_completed_at": iso(T0),
                            "foods": ["rice"],
                            "source": "manual",
                        }
                    ],
                    next_cursor="page-2",
                    truncated=True,
                ),
            )
        assert cursor == "page-2"
        return httpx.Response(
            200,
            json=paged_body(
                items=[
                    {
                        "id": "meal-2",
                        "meal_completed_at": iso(T0 + timedelta(hours=4)),
                        "foods": ["eggs"],
                        "source": "manual",
                    }
                ]
            ),
        )

    client = client_for(handler)
    meals = client.get_meals(START, END)
    client.close()
    assert [meal.meal_id for meal in meals] == ["meal-1", "meal-2"]
    assert meals[0].timestamp == T0
    assert seen_cursors == [None, "page-2"]


def test_truncated_page_without_cursor_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=paged_body(items=[], truncated=True, next_cursor=None))

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_meals(START, END)
    client.close()
    assert raised.value.code == "RESULT_TOO_LARGE"


def test_glucose_range_is_split_into_seven_day_windows():
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        windows.append((request.url.params["start"], request.url.params["end"]))
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": iso(start), "value_mg_dl": 90.0}],
                start=start,
                end=datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00")),
            ),
        )

    client = client_for(handler)
    records = client.get_glucose(START, END)
    client.close()
    expected = list(iter_windows(START, END, 7))
    assert len(windows) == len(expected)
    assert len(records) == len(expected)
    assert records[0].glucose_mg_dl == 90.0


def test_sleep_uses_one_unwindowed_request_and_keeps_original_interval():
    calls: list[tuple[str, str]] = []
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 20, tzinfo=UTC)
    item = {
        "id": "sleep-span",
        "start_time": iso(start - timedelta(hours=2)),
        "end_time": iso(start + timedelta(hours=4)),
        "duration_minutes": 360.0,
        "stage": "asleep",
        "source": "apple_health",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == SLEEP_INTERVALS_PATH
        calls.append((request.url.params["start"], request.url.params["end"]))
        return httpx.Response(200, json=paged_body(items=[item], start=start, end=end))

    client = client_for(handler)
    intervals = client.get_sleep(start, end)
    client.close()
    assert calls == [(iso(start), iso(end))]
    assert intervals[0].start == start - timedelta(hours=2)
    assert intervals[0].end == start + timedelta(hours=4)


def test_sleep_range_too_large_is_not_retried_or_split():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        assert request.url.path == SLEEP_INTERVALS_PATH
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "RANGE_TOO_LARGE",
                    "message": "Sleep queries are limited to 90 days",
                    "details": {"max_days": 90},
                }
            },
        )

    client = client_for(handler, max_retries=3)
    with pytest.raises(HealthAPIError) as raised:
        client.get_sleep(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    client.close()
    assert raised.value.code == "RANGE_TOO_LARGE"
    assert attempts["count"] == 1


def test_workouts_map_unavailable_fields_to_null():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == WORKOUTS_PATH
        return httpx.Response(
            200,
            json=paged_body(
                items=[
                    {
                        "id": "workout-1",
                        "start_time": iso(T0),
                        "end_time": iso(T0 + timedelta(hours=1)),
                        "sport": "running",
                        "distance_meters": 5000.0,
                        "duration_minutes": 60.0,
                        "source": "apple_health",
                    }
                ]
            ),
        )

    client = client_for(handler)
    workouts = client.get_workouts(START, END)
    client.close()
    assert workouts[0].distance_meters == 5000.0
    assert workouts[0].active_energy is None
    assert workouts[0].average_hr is None
    assert workouts[0].max_hr is None


def test_weight_empty_page():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == WEIGHT_MEASUREMENTS_PATH
        return httpx.Response(200, json=paged_body(items=[]))

    client = client_for(handler)
    assert client.get_weight(START, END) == []
    client.close()


def test_naive_range_is_rejected():
    client = client_for(lambda request: httpx.Response(500))
    with pytest.raises(InvalidRangeError, match="timezone-aware"):
        client.get_glucose(datetime(2026, 8, 1), END)
    client.close()


def test_same_window_duplicate_id_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        item = {
            "id": "meal-dup",
            "meal_completed_at": iso(T0),
            "foods": ["rice"],
            "source": "manual",
        }
        return httpx.Response(200, json=paged_body(items=[item, item]))

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_meals(START, END)
    client.close()
    assert raised.value.code == "DUPLICATE_RECORD"


def test_glucose_never_calls_personal_timeline():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": iso(T0), "value_mg_dl": 96.0}],
                start=START,
                end=START + timedelta(days=1),
            ),
        )

    client = client_for(handler)
    client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert seen == [GLUCOSE_SERIES_PATH]
    assert "/v1/query/personal-timeline" not in seen


def test_glucose_windows_are_half_open_and_abut():
    windows = list(iter_windows(START, END, 7))
    assert windows[0][0] == START
    assert windows[-1][1] == END
    for previous, current in zip(windows, windows[1:], strict=False):
        assert previous[1] == current[0]
        assert previous[0] <= previous[1]
    assert all(window_end > window_start for window_start, window_end in windows)


def test_non_numeric_glucose_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": iso(T0), "value_mg_dl": "high"}],
                start=START,
                end=START + timedelta(days=1),
            ),
        )

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"


def test_naive_glucose_timestamp_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=glucose_series_body(
                [{"timestamp": "2026-08-05T16:00:00", "value_mg_dl": 96.0}],
                start=START,
                end=START + timedelta(days=1),
            ),
        )

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_glucose(START, START + timedelta(days=1))
    client.close()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"


def test_invalid_sleep_interval_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=paged_body(
                items=[
                    {
                        "id": "sleep-bad",
                        "start_time": iso(T0 + timedelta(hours=2)),
                        "end_time": iso(T0),
                        "duration_minutes": 0.0,
                        "stage": "asleep",
                        "source": "apple_health",
                    }
                ]
            ),
        )

    client = client_for(handler)
    with pytest.raises(HealthAPIError) as raised:
        client.get_sleep(START, END)
    client.close()
    assert raised.value.code == "UPSTREAM_RESPONSE_ERROR"
