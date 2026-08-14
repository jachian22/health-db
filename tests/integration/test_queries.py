"""Integration tests for authenticated Query API v1 (GET /v1/query/*)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.api.v1.query import logger as query_logger
from app.services.query_service import _duration_minutes


async def _seed(client: AsyncClient, ingest_headers: dict, seed_body: dict) -> None:
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=seed_body)
    assert resp.status_code == 200, resp.text


def _q(**params: str) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())


# --- Authentication ---


WINDOWED_AUTH_PATHS = (
    "/v1/query/coverage",
    "/v1/query/personal-timeline",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WINDOWED_AUTH_PATHS)
async def test_query_missing_auth_returns_401(client: AsyncClient, path: str):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z")
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "request_id" in resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WINDOWED_AUTH_PATHS)
async def test_query_invalid_read_key_returns_401(client: AsyncClient, path: str):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert "wrong-key" not in resp.text
    assert "test-read-key" not in resp.text
    assert "test-ingest-key" not in resp.text


@pytest.mark.asyncio
async def test_query_valid_read_key_succeeds(
    client: AsyncClient,
    read_headers: dict,
):
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WINDOWED_AUTH_PATHS)
async def test_ingest_key_rejected_by_query_api(
    client: AsyncClient,
    ingest_headers: dict,
    path: str,
):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
        headers=ingest_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_read_key_rejected_by_ingest(
    client: AsyncClient,
    read_headers: dict,
    ingest_body: dict,
):
    resp = await client.post("/v1/ingest/batch", headers=read_headers, json=ingest_body)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


# --- Coverage ---


@pytest.mark.asyncio
async def test_coverage_accurate_counts_and_empty_categories(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-08T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "America/New_York"
    assert "request_id" in body
    cov = body["coverage"]
    assert set(cov) == {
        "glucose",
        "meals",
        "workouts",
        "sleep_intervals",
        "weight_measurements",
    }
    assert cov["glucose"]["count"] == 2
    assert cov["glucose"]["first_at"].startswith("2026-08-05T14:15:00")
    assert cov["glucose"]["last_at"].startswith("2026-08-05T14:30:00")
    assert cov["meals"]["count"] == 1
    assert cov["workouts"]["count"] == 1
    assert cov["sleep_intervals"]["count"] == 2
    assert cov["weight_measurements"]["count"] == 1

    empty = await client.get(
        "/v1/query/coverage?"
        + _q(start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z"),
        headers=read_headers,
    )
    assert empty.status_code == 200
    for cat in empty.json()["coverage"].values():
        assert cat == {"count": 0, "first_at": None, "last_at": None}


@pytest.mark.asyncio
async def test_coverage_range_boundaries(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    # Half-open: sample at exactly end is excluded
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-05T14:15:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["coverage"]["glucose"]["count"] == 0

    included = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-05T14:15:00Z", end="2026-08-05T14:16:00Z"),
        headers=read_headers,
    )
    assert included.json()["coverage"]["glucose"]["count"] == 1


@pytest.mark.asyncio
async def test_coverage_workouts_and_sleep_use_overlap_not_start_in_window(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout(
                    "cov-wo-touch-start",
                    "2026-08-05T10:00:00.000Z",
                    "2026-08-05T12:00:00.000Z",
                ),
                _workout(
                    "cov-wo-overlap-start",
                    "2026-08-05T11:00:00.000Z",
                    "2026-08-05T13:00:00.000Z",
                ),
                _workout(
                    "cov-wo-inside",
                    "2026-08-05T13:00:00.000Z",
                    "2026-08-05T14:00:00.000Z",
                ),
                _workout(
                    "cov-wo-at-end",
                    "2026-08-05T18:00:00.000Z",
                    "2026-08-05T19:00:00.000Z",
                ),
            ],
            sleep_sessions=[
                _sleep(
                    "cov-sl-touch-start",
                    "2026-08-05T10:00:00.000Z",
                    "2026-08-05T12:00:00.000Z",
                    "awake",
                ),
                _sleep(
                    "cov-sl-overlap-start",
                    "2026-08-05T11:00:00.000Z",
                    "2026-08-05T13:00:00.000Z",
                    "core",
                ),
                _sleep(
                    "cov-sl-inside",
                    "2026-08-05T13:00:00.000Z",
                    "2026-08-05T14:00:00.000Z",
                    "deep",
                ),
                _sleep(
                    "cov-sl-at-end",
                    "2026-08-05T18:00:00.000Z",
                    "2026-08-05T19:00:00.000Z",
                    "rem",
                ),
            ],
        ),
    )
    window = _q(start="2026-08-05T12:00:00Z", end="2026-08-05T18:00:00Z")
    coverage = await client.get("/v1/query/coverage?" + window, headers=read_headers)
    workouts = await client.get("/v1/query/workouts?" + window, headers=read_headers)
    sleep = await client.get("/v1/query/sleep-intervals?" + window, headers=read_headers)
    assert coverage.status_code == workouts.status_code == sleep.status_code == 200

    cov = coverage.json()["coverage"]
    assert cov["workouts"]["count"] == 2
    assert cov["workouts"]["first_at"].startswith("2026-08-05T11:00:00")
    assert cov["workouts"]["last_at"].startswith("2026-08-05T13:00:00")
    assert cov["sleep_intervals"]["count"] == 2
    assert cov["sleep_intervals"]["first_at"].startswith("2026-08-05T11:00:00")
    assert cov["sleep_intervals"]["last_at"].startswith("2026-08-05T13:00:00")

    assert workouts.json()["record_count"] == cov["workouts"]["count"]
    assert [item["id"] for item in workouts.json()["items"]] == [
        "cov-wo-overlap-start",
        "cov-wo-inside",
    ]
    assert sleep.json()["record_count"] == cov["sleep_intervals"]["count"]
    assert [item["id"] for item in sleep.json()["items"]] == [
        "cov-sl-overlap-start",
        "cov-sl-inside",
    ]


@pytest.mark.asyncio
async def test_coverage_interval_cross_window_span_matches_list(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout(
                    "wo-left",
                    "2026-08-05T12:00:00.000Z",
                    "2026-08-05T13:00:00.000Z",
                ),
                _workout(
                    "wo-span",
                    "2026-08-05T14:00:00.000Z",
                    "2026-08-05T16:00:00.000Z",
                ),
                _workout(
                    "wo-right",
                    "2026-08-05T17:00:00.000Z",
                    "2026-08-05T18:00:00.000Z",
                ),
            ],
            sleep_sessions=[
                _sleep(
                    "sl-span",
                    "2026-08-05T14:00:00.000Z",
                    "2026-08-05T16:00:00.000Z",
                    "core",
                ),
            ],
        ),
    )
    left = _q(start="2026-08-05T12:00:00Z", end="2026-08-05T15:00:00Z")
    right = _q(start="2026-08-05T15:00:00Z", end="2026-08-05T18:00:00Z")

    left_cov = (await client.get("/v1/query/coverage?" + left, headers=read_headers)).json()
    right_cov = (await client.get("/v1/query/coverage?" + right, headers=read_headers)).json()
    left_wo = (await client.get("/v1/query/workouts?" + left, headers=read_headers)).json()
    right_wo = (await client.get("/v1/query/workouts?" + right, headers=read_headers)).json()
    left_sl = (await client.get("/v1/query/sleep-intervals?" + left, headers=read_headers)).json()
    right_sl = (await client.get("/v1/query/sleep-intervals?" + right, headers=read_headers)).json()

    assert left_cov["coverage"]["workouts"]["count"] == left_wo["record_count"] == 2
    assert right_cov["coverage"]["workouts"]["count"] == right_wo["record_count"] == 2
    assert {item["id"] for item in left_wo["items"]} == {"wo-left", "wo-span"}
    assert {item["id"] for item in right_wo["items"]} == {"wo-span", "wo-right"}
    assert left_cov["coverage"]["workouts"]["first_at"].startswith("2026-08-05T12:00:00")
    assert left_cov["coverage"]["workouts"]["last_at"].startswith("2026-08-05T14:00:00")
    assert right_cov["coverage"]["workouts"]["first_at"].startswith("2026-08-05T14:00:00")
    assert right_cov["coverage"]["workouts"]["last_at"].startswith("2026-08-05T17:00:00")

    assert left_cov["coverage"]["sleep_intervals"]["count"] == left_sl["record_count"] == 1
    assert right_cov["coverage"]["sleep_intervals"]["count"] == right_sl["record_count"] == 1
    assert left_sl["items"][0]["id"] == right_sl["items"][0]["id"] == "sl-span"
    assert left_cov["coverage"]["sleep_intervals"]["first_at"].startswith("2026-08-05T14:00:00")
    assert left_cov["coverage"]["sleep_intervals"]["last_at"].startswith("2026-08-05T14:00:00")


@pytest.mark.asyncio
async def test_invalid_time_range_rejected(client: AsyncClient, read_headers: dict):
    bad = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-08T00:00:00Z", end="2026-08-01T00:00:00Z"),
        headers=read_headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
async def test_caller_cannot_select_user(
    client: AsyncClient,
    read_headers: dict,
    ingest_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-08T00:00:00Z",
            user_id="someone-else",
        ),
        headers=read_headers,
    )
    # Extra query params are ignored; fixed personal-primary is used.
    assert resp.status_code == 200
    assert resp.json()["coverage"]["glucose"]["count"] == 2
    assert "user_id" not in resp.text


# --- Glucose series ---


@pytest.mark.asyncio
async def test_glucose_raw_series(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-08T00:00:00Z",
            resolution="raw",
        ),
        headers=read_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "raw"
    assert body["aggregation"] is None
    assert body["source_record_count"] == 2
    assert body["returned_point_count"] == 2
    assert body["truncated"] is False
    times = [p["timestamp"] for p in body["points"]]
    assert times == sorted(times)
    assert body["points"][0]["value_mg_dl"] == 96
    assert body["points"][1]["value_mg_dl"] == 102
    for point in body["points"]:
        assert set(point) == {"timestamp", "value_mg_dl"}
    forbidden = (
        "source_sample_id",
        "user_id",
        "metadata",
        "health_source",
        "batch",
        "notes",
    )
    text = resp.text
    for token in forbidden:
        assert token not in text


@pytest.mark.asyncio
async def test_glucose_aggregations(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    body = {
        "schema_version": 1,
        "exported_at": "2026-08-05T20:00:00Z",
        "data_start": "2026-08-05T14:00:00Z",
        "data_end": "2026-08-05T16:00:00Z",
        "glucose_samples": [
            {
                "source": "apple_health",
                "source_sample_id": "g1",
                "source_name": "Stelo",
                "sample_time": "2026-08-05T14:16:00Z",
                "value": 90,
                "unit": "mg/dL",
                "metadata": {},
            },
            {
                "source": "apple_health",
                "source_sample_id": "g2",
                "source_name": "Stelo",
                "sample_time": "2026-08-05T14:18:00Z",
                "value": 100,
                "unit": "mg/dL",
                "metadata": {},
            },
            {
                "source": "apple_health",
                "source_sample_id": "g3",
                "source_name": "Stelo",
                "sample_time": "2026-08-05T14:22:00Z",
                "value": 110,
                "unit": "mg/dL",
                "metadata": {},
            },
            {
                "source": "apple_health",
                "source_sample_id": "g4",
                "source_name": "Stelo",
                "sample_time": "2026-08-05T15:05:00Z",
                "value": 120,
                "unit": "mg/dL",
                "metadata": {},
            },
        ],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
    }
    await _seed(client, ingest_headers, body)

    five = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-05T14:00:00Z",
            end="2026-08-05T16:00:00Z",
            resolution="5m",
        ),
        headers=read_headers,
    )
    assert five.status_code == 200
    fbody = five.json()
    assert fbody["aggregation"] == "mean_min_max"
    assert fbody["source_record_count"] == 4
    # 14:15–14:20 bucket: 90,100 → mean 95; 14:20–14:25: 110; 15:05–15:10: 120
    buckets = {p["start"]: p for p in fbody["points"]}
    b1 = buckets["2026-08-05T14:15:00Z"]
    assert b1["sample_count"] == 2
    assert b1["min_mg_dl"] == 90
    assert b1["max_mg_dl"] == 100
    assert b1["mean_mg_dl"] == 95.0
    assert b1["end"] == "2026-08-05T14:20:00Z"

    fifteen = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-05T14:00:00Z",
            end="2026-08-05T16:00:00Z",
            resolution="15m",
        ),
        headers=read_headers,
    )
    assert fifteen.status_code == 200
    tbody = fifteen.json()
    # 14:15–14:30: 90,100,110
    b15 = {p["start"]: p for p in tbody["points"]}["2026-08-05T14:15:00Z"]
    assert b15["sample_count"] == 3
    assert b15["min_mg_dl"] == 90
    assert b15["max_mg_dl"] == 110
    assert b15["mean_mg_dl"] == 100.0

    hourly = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-05T14:00:00Z",
            end="2026-08-05T16:00:00Z",
            resolution="hourly",
        ),
        headers=read_headers,
    )
    assert hourly.status_code == 200
    hbody = hourly.json()
    h14 = {p["start"]: p for p in hbody["points"]}["2026-08-05T14:00:00Z"]
    assert h14["sample_count"] == 3
    assert h14["end"] == "2026-08-05T15:00:00Z"


@pytest.mark.asyncio
async def test_glucose_empty_and_invalid_resolution(
    client: AsyncClient,
    read_headers: dict,
):
    empty = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2025-01-01T00:00:00Z",
            end="2025-01-02T00:00:00Z",
            resolution="raw",
        ),
        headers=read_headers,
    )
    assert empty.status_code == 200
    body = empty.json()
    assert body["points"] == []
    assert body["source_record_count"] == 0
    assert body["returned_point_count"] == 0
    assert body["data_fresh_through"] is None

    bad = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z",
            resolution="1d",
        ),
        headers=read_headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_RESOLUTION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "days", "expect_code"),
    [
        ("raw", 8, "RANGE_TOO_LARGE"),
        ("5m", 32, "RANGE_TOO_LARGE"),
        ("15m", 91, "RANGE_TOO_LARGE"),
        ("hourly", 366, "RANGE_TOO_LARGE"),
    ],
)
async def test_glucose_range_limits(
    client: AsyncClient,
    read_headers: dict,
    resolution: str,
    days: int,
    expect_code: str,
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=days)
    resp = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start=start.isoformat().replace("+00:00", "Z"),
            end=end.isoformat().replace("+00:00", "Z"),
            resolution=resolution,
        ),
        headers=read_headers,
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == expect_code
    assert "max_days" in (err.get("details") or {})


# --- Glucose summary ---


@pytest.mark.asyncio
async def test_glucose_summary_overall(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/glucose/summary?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-08T00:00:00Z",
            bucket="overall",
        ),
        headers=read_headers,
    )
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["sample_count"] == 2
    assert summary["min_mg_dl"] == 96
    assert summary["max_mg_dl"] == 102
    assert summary["mean_mg_dl"] == 99.0
    assert summary["median_mg_dl"] == 99.0
    assert summary["first_at"].startswith("2026-08-05T14:15:00")
    assert summary["last_at"].startswith("2026-08-05T14:30:00")
    assert "time_in_range" not in resp.text
    assert "risk" not in resp.text
    assert "diagnosis" not in resp.text
    assert "recommendation" not in resp.text


@pytest.mark.asyncio
async def test_glucose_summary_empty(client: AsyncClient, read_headers: dict):
    resp = await client.get(
        "/v1/query/glucose/summary?"
        + _q(start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["sample_count"] == 0
    assert summary["first_at"] is None
    assert summary["mean_mg_dl"] is None


@pytest.mark.asyncio
async def test_glucose_summary_daily_timezone_and_dst(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    # 2026-03-08 is US DST spring-forward in America/New_York.
    # 05:30 UTC = 00:30 EST (still standard); 07:30 UTC = 03:30 EDT.
    body = {
        "schema_version": 1,
        "exported_at": "2026-03-09T12:00:00Z",
        "data_start": "2026-03-08T00:00:00Z",
        "data_end": "2026-03-09T12:00:00Z",
        "glucose_samples": [
            {
                "source": "apple_health",
                "source_sample_id": "dst-1",
                "source_name": "Stelo",
                "sample_time": "2026-03-08T05:30:00Z",
                "value": 80,
                "unit": "mg/dL",
                "metadata": {},
            },
            {
                "source": "apple_health",
                "source_sample_id": "dst-2",
                "source_name": "Stelo",
                "sample_time": "2026-03-08T07:30:00Z",
                "value": 90,
                "unit": "mg/dL",
                "metadata": {},
            },
            {
                "source": "apple_health",
                "source_sample_id": "dst-3",
                "source_name": "Stelo",
                "sample_time": "2026-03-09T15:00:00Z",
                "value": 100,
                "unit": "mg/dL",
                "metadata": {},
            },
        ],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
    }
    await _seed(client, ingest_headers, body)

    resp = await client.get(
        "/v1/query/glucose/summary?"
        + _q(
            start="2026-03-08T00:00:00Z",
            end="2026-03-10T00:00:00Z",
            bucket="daily",
            timezone="America/New_York",
        ),
        headers=read_headers,
    )
    assert resp.status_code == 200
    days = {d["local_date"]: d for d in resp.json()["days"]}
    assert "2026-03-08" in days
    assert "2026-03-09" in days
    assert days["2026-03-08"]["sample_count"] == 2
    assert days["2026-03-09"]["sample_count"] == 1
    # Source timestamps remain UTC
    assert days["2026-03-08"]["first_at"].endswith("+00:00") or days["2026-03-08"][
        "first_at"
    ].endswith("Z")

    override = await client.get(
        "/v1/query/glucose/summary?"
        + _q(
            start="2026-03-08T00:00:00Z",
            end="2026-03-10T00:00:00Z",
            bucket="daily",
            timezone="UTC",
        ),
        headers=read_headers,
    )
    assert override.status_code == 200
    assert override.json()["timezone"] == "UTC"

    bad_tz = await client.get(
        "/v1/query/glucose/summary?"
        + _q(
            start="2026-03-08T00:00:00Z",
            end="2026-03-10T00:00:00Z",
            timezone="Not/A_Zone",
        ),
        headers=read_headers,
    )
    assert bad_tz.status_code == 422
    assert bad_tz.json()["error"]["code"] == "INVALID_TIMEZONE"


# --- Meals ---


@pytest.mark.asyncio
async def test_meals_order_foods_no_notes_pagination(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    meals = []
    for i in range(3):
        meals.append(
            {
                "source": "manual",
                "source_sample_id": f"meal-page-{i}",
                "meal_completed_at": f"2026-08-0{5 + i}T12:00:00.000Z",
                "foods": [f"food-{i}"],
                "notes": f"secret-note-{i}",
            }
        )
    # Historical / backfilled meal with earlier completion time
    meals.append(
        {
            "source": "manual",
            "source_sample_id": "meal-backfill",
            "meal_completed_at": "2026-08-02T09:00:00.000Z",
            "foods": ["backfill-oatmeal"],
            "notes": "should-not-appear",
        }
    )
    body = {
        "schema_version": 1,
        "exported_at": "2026-08-10T12:00:00Z",
        "data_start": "2026-08-01T00:00:00Z",
        "data_end": "2026-08-10T12:00:00Z",
        "glucose_samples": [],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": meals,
    }
    await _seed(client, ingest_headers, body)

    resp = await client.get(
        "/v1/query/meals?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-12T00:00:00Z", limit="2"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    page1 = resp.json()
    assert page1["record_count"] == 2
    assert page1["truncated"] is True
    assert page1["next_cursor"]
    times = [item["meal_completed_at"] for item in page1["items"]]
    assert times == sorted(times)
    assert page1["items"][0]["id"] == "meal-backfill"
    assert page1["items"][0]["foods"] == ["backfill-oatmeal"]
    assert "notes" not in page1["items"][0]
    assert "secret-note" not in resp.text
    assert "metadata" not in resp.text

    page2 = await client.get(
        "/v1/query/meals?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-12T00:00:00Z",
            limit="2",
            cursor=page1["next_cursor"],
        ),
        headers=read_headers,
    )
    assert page2.status_code == 200
    p2 = page2.json()
    assert p2["record_count"] == 2
    assert p2["truncated"] is False
    assert p2["next_cursor"] is None
    ids = [item["id"] for item in page1["items"] + p2["items"]]
    assert ids == [
        "meal-backfill",
        "meal-page-0",
        "meal-page-1",
        "meal-page-2",
    ]


@pytest.mark.asyncio
async def test_meals_empty_default_limit_and_max(
    client: AsyncClient,
    read_headers: dict,
    ingest_headers: dict,
    query_seed_body: dict,
):
    empty = await client.get(
        "/v1/query/meals?"
        + _q(start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z"),
        headers=read_headers,
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["record_count"] == 0

    await _seed(client, ingest_headers, query_seed_body)
    ok = await client.get(
        "/v1/query/meals?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-08T00:00:00Z"),
        headers=read_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["items"][0]["source"] == "manual"

    too_big = await client.get(
        "/v1/query/meals?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-08T00:00:00Z",
            limit="501",
        ),
        headers=read_headers,
    )
    assert too_big.status_code == 422
    assert too_big.json()["error"]["code"] == "INVALID_LIMIT"

    bad_cursor = await client.get(
        "/v1/query/meals?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-08T00:00:00Z",
            cursor="not-a-valid-cursor",
        ),
        headers=read_headers,
    )
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["error"]["code"] == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_meal_cursor_rejects_range_mismatch(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    meals = [
        {
            "source": "manual",
            "source_sample_id": f"meal-c-{i}",
            "meal_completed_at": f"2026-08-0{5 + i}T12:00:00.000Z",
            "foods": [f"food-{i}"],
            "notes": "nope",
        }
        for i in range(3)
    ]
    body = {
        "schema_version": 1,
        "exported_at": "2026-08-10T12:00:00Z",
        "data_start": "2026-08-01T00:00:00Z",
        "data_end": "2026-08-10T12:00:00Z",
        "glucose_samples": [],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": meals,
    }
    await _seed(client, ingest_headers, body)
    page1 = await client.get(
        "/v1/query/meals?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-12T00:00:00Z", limit="1"),
        headers=read_headers,
    )
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    mismatched = await client.get(
        "/v1/query/meals?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-11T00:00:00Z",
            limit="1",
            cursor=cursor,
        ),
        headers=read_headers,
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_glucose_result_too_large(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("app.services.query_service.MAX_GLUCOSE_POINTS", 2)
    monkeypatch.setattr("app.schemas.queries.MAX_GLUCOSE_POINTS", 2)
    samples = []
    start = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(4):
        samples.append(
            {
                "source": "apple_health",
                "source_sample_id": f"cap-{i}",
                "source_name": "Stelo",
                "sample_time": (start + timedelta(minutes=i * 5))
                .isoformat()
                .replace("+00:00", "Z"),
                "value": 90 + i,
                "unit": "mg/dL",
                "metadata": {},
            }
        )
    body = {
        "schema_version": 1,
        "exported_at": "2026-08-01T12:00:00Z",
        "data_start": "2026-08-01T00:00:00Z",
        "data_end": "2026-08-01T12:00:00Z",
        "glucose_samples": samples,
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
    }
    await _seed(client, ingest_headers, body)
    resp = await client.get(
        "/v1/query/glucose/series?"
        + _q(
            start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z",
            resolution="raw",
        ),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "RESULT_TOO_LARGE"
    assert resp.json()["error"]["details"]["max_points"] == 2


# --- Read-only / security / regression ---


@pytest.mark.asyncio
async def test_query_endpoints_get_only(client: AsyncClient, read_headers: dict):
    for path in (
        "/v1/query/coverage",
        "/v1/query/glucose/series",
        "/v1/query/glucose/summary",
        "/v1/query/meals",
        "/v1/query/workouts",
        "/v1/query/sleep-intervals",
        "/v1/query/weight-measurements",
        "/v1/query/last-logged-meal",
        "/v1/query/context-snapshot",
        "/v1/query/personal-timeline",
    ):
        suffix = (
            _q(anchor="2026-08-15T14:00:00Z")
            if path in {"/v1/query/last-logged-meal", "/v1/query/context-snapshot"}
            else _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z")
        )
        resp = await client.post(
            path + "?" + suffix,
            headers=read_headers,
        )
        assert resp.status_code == 405


@pytest.mark.asyncio
async def test_health_ready_unchanged(client: AsyncClient):
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_query_failed_sanitized(
    client: AsyncClient,
    read_headers: dict,
):
    with patch(
        "app.services.query_service.HealthDataQueryService.resolve_personal_user",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection to postgresql://secret@db failed"),
    ):
        resp = await client.get(
            "/v1/query/coverage?"
            + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
            headers=read_headers,
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "QUERY_FAILED"
    assert "postgresql" not in resp.text.lower()
    assert "secret" not in resp.text
    assert "RuntimeError" not in resp.text


@pytest.mark.asyncio
async def test_fixture_meals_notes_absent_from_query(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/meals?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-08T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    assert "normal dinner" not in resp.text
    item = resp.json()["items"][0]
    assert "notes" not in item
    assert item["foods"] == ["rice", "chicken"]


# --- Workouts / sleep / weight list endpoints ---


NEW_LIST_PATHS = (
    "/v1/query/workouts",
    "/v1/query/sleep-intervals",
    "/v1/query/weight-measurements",
)


def _export_body(**overrides: object) -> dict:
    body: dict = {
        "schema_version": 1,
        "exported_at": "2026-08-10T12:00:00Z",
        "data_start": "2026-07-01T00:00:00Z",
        "data_end": "2026-08-10T12:00:00Z",
        "glucose_samples": [],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
    }
    body.update(overrides)
    return body


def _workout(
    sample_id: str,
    start: str,
    end: str,
    *,
    source_name: str = "Strava",
    distance: float | None = 5000,
    kcal: float | None = 310,
    avg_hr: float | None = 148,
    max_hr: float | None = 172,
) -> dict:
    return {
        "source": "apple_health",
        "source_sample_id": sample_id,
        "source_name": source_name,
        "start_time": start,
        "end_time": end,
        "sport": "running",
        "distance_meters": distance,
        "active_energy_kcal": kcal,
        "average_heart_rate": avg_hr,
        "maximum_heart_rate": max_hr,
        "metadata": {"source_app": source_name, "secret": "do-not-leak"},
    }


def _sleep(sample_id: str, start: str, end: str, stage: str) -> dict:
    return {
        "source": "apple_health",
        "source_sample_id": sample_id,
        "source_name": "Apple Watch",
        "start_time": start,
        "end_time": end,
        "stage": stage,
        "metadata": {"source_app": "Apple Watch"},
    }


def _weight(sample_id: str, measured_at: str, value: float) -> dict:
    return {
        "source": "apple_health",
        "source_sample_id": sample_id,
        "source_name": "Health",
        "measured_at": measured_at,
        "value": value,
        "unit": "kg",
        "metadata": {"source_app": "Health"},
    }


def _meal(
    sample_id: str,
    completed_at: str,
    *,
    foods: list[str] | None = None,
    notes: str | None = "secret-note-do-not-leak",
) -> dict:
    return {
        "source": "manual",
        "source_sample_id": sample_id,
        "source_name": "Health",
        "meal_completed_at": completed_at,
        "foods": foods if foods is not None else ["rice"],
        "notes": notes,
        "metadata": {"secret": "do-not-leak"},
    }


def _glucose(sample_id: str, sample_time: str, value: float) -> dict:
    return {
        "source": "apple_health",
        "source_sample_id": sample_id,
        "source_name": "Stelo",
        "sample_time": sample_time,
        "value": value,
        "unit": "mg/dL",
        "metadata": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_missing_auth_returns_401(client: AsyncClient, path: str):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z")
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_wrong_key_returns_401(client: AsyncClient, path: str):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "wrong-key" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_ingest_key_returns_401(
    client: AsyncClient, ingest_headers: dict, path: str
):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
        headers=ingest_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_empty_window_succeeds(client: AsyncClient, read_headers: dict, path: str):
    resp = await client.get(
        path + "?" + _q(start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["record_count"] == 0
    assert body["truncated"] is False
    assert body["next_cursor"] is None
    assert body["data_fresh_through"] is None
    assert body["timezone"] == "America/New_York"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_naive_timestamp_rejected(
    client: AsyncClient, read_headers: dict, path: str
):
    resp = await client.get(
        path + "?" + _q(start="2026-08-01T00:00:00", end="2026-08-02T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", NEW_LIST_PATHS)
async def test_list_end_not_after_start_rejected(
    client: AsyncClient, read_headers: dict, path: str
):
    resp = await client.get(
        path + "?" + _q(start="2026-08-02T00:00:00Z", end="2026-08-01T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "over_end", "ok_end", "max_days"),
    [
        (
            "/v1/query/workouts",
            "2027-08-02T00:00:00Z",
            "2027-08-01T00:00:00Z",
            365,
        ),
        (
            "/v1/query/weight-measurements",
            "2027-08-02T00:00:00Z",
            "2027-08-01T00:00:00Z",
            365,
        ),
        (
            "/v1/query/sleep-intervals",
            "2026-10-31T00:00:00Z",
            "2026-10-30T00:00:00Z",
            90,
        ),
    ],
)
async def test_list_window_limits(
    client: AsyncClient,
    read_headers: dict,
    path: str,
    over_end: str,
    ok_end: str,
    max_days: int,
):
    start = "2026-08-01T00:00:00Z"
    too_big = await client.get(
        path + "?" + _q(start=start, end=over_end),
        headers=read_headers,
    )
    assert too_big.status_code == 422
    assert too_big.json()["error"]["code"] == "RANGE_TOO_LARGE"
    assert too_big.json()["error"]["details"]["max_days"] == max_days

    ok = await client.get(
        path + "?" + _q(start=start, end=ok_end),
        headers=read_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["items"] == []


@pytest.mark.asyncio
async def test_workout_overlap_boundaries_and_unclipped_timestamps(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout(
                    "wo-before-touch",
                    "2026-08-05T10:00:00.000Z",
                    "2026-08-05T12:00:00.000Z",
                ),
                _workout(
                    "wo-overlap-start",
                    "2026-08-05T11:00:00.000Z",
                    "2026-08-05T13:00:00.000Z",
                ),
                _workout(
                    "wo-inside",
                    "2026-08-05T13:00:00.000Z",
                    "2026-08-05T14:00:00.000Z",
                ),
                _workout(
                    "wo-at-end",
                    "2026-08-05T18:00:00.000Z",
                    "2026-08-05T19:00:00.000Z",
                ),
            ]
        ),
    )
    resp = await client.get(
        "/v1/query/workouts?"
        + _q(start="2026-08-05T12:00:00Z", end="2026-08-05T18:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert ids == ["wo-overlap-start", "wo-inside"]
    overlap = body["items"][0]
    assert overlap["start_time"].startswith("2026-08-05T11:00:00")
    assert overlap["end_time"].startswith("2026-08-05T13:00:00")
    assert overlap["sport"] == "running"
    assert overlap["distance_meters"] == 5000
    assert overlap["duration_minutes"] == 120.0
    assert overlap["source"] == "apple_health"
    assert overlap["id"] == "wo-overlap-start"
    for forbidden in (
        "average_heart_rate",
        "maximum_heart_rate",
        "active_energy_kcal",
        "metadata",
        "source_name",
        "user_id",
        "health_source_id",
        "notes",
    ):
        assert forbidden not in overlap
    assert "148" not in resp.text
    assert "172" not in resp.text
    assert "do-not-leak" not in resp.text
    assert body["data_fresh_through"].startswith("2026-08-05T14:00:00")

    first_page = await client.get(
        "/v1/query/workouts?"
        + _q(start="2026-08-05T12:00:00Z", end="2026-08-05T18:00:00Z", limit="1"),
        headers=read_headers,
    )
    assert first_page.status_code == 200
    page = first_page.json()
    assert page["truncated"] is True
    assert [item["id"] for item in page["items"]] == ["wo-overlap-start"]
    assert page["data_fresh_through"].startswith("2026-08-05T13:00:00")


@pytest.mark.asyncio
async def test_workout_sensitive_fields_absent_from_seed(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/workouts?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-08T00:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["record_count"] == 1
    item = resp.json()["items"][0]
    assert "average_heart_rate" not in item
    assert "maximum_heart_rate" not in item
    assert "active_energy_kcal" not in item
    assert "310" not in resp.text
    assert "148" not in resp.text
    assert "172" not in resp.text


@pytest.mark.asyncio
async def test_overlapping_apple_health_run_is_not_listed_when_strava_exists(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    strava_id = "strava-canonical-run"
    apple_id = "apple-health-duplicate-run"
    ingest = await client.post(
        "/v1/ingest/batch",
        headers=ingest_headers,
        json=_export_body(
            workouts=[
                _workout(
                    apple_id,
                    "2026-08-05T12:05:00.000Z",
                    "2026-08-05T12:55:00.000Z",
                    source_name="Apple Watch",
                    distance=1111,
                ),
                _workout(
                    strava_id,
                    "2026-08-05T12:00:00.000Z",
                    "2026-08-05T13:00:00.000Z",
                    distance=5000,
                ),
            ]
        ),
    )
    assert ingest.status_code == 200
    workouts_summary = ingest.json()["summary"]["workouts"]
    assert workouts_summary["received"] == 2
    assert workouts_summary["inserted"] == 1
    assert workouts_summary["rejected"] == 1
    assert any(
        r["code"] == "UNSUPPORTED_WORKOUT_SOURCE" and r["source_sample_id"] == apple_id
        for r in ingest.json()["rejections"]
    )

    window = _q(start="2026-08-05T12:00:00Z", end="2026-08-05T13:00:00Z")
    listed = await client.get("/v1/query/workouts?" + window, headers=read_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["record_count"] == 1
    item = body["items"][0]
    assert item["id"] == strava_id
    assert item["source"] == "apple_health"
    assert item["distance_meters"] == 5000
    assert "source_name" not in item
    assert apple_id not in listed.text
    assert "Apple Watch" not in listed.text
    assert "1111" not in listed.text

    coverage = await client.get("/v1/query/coverage?" + window, headers=read_headers)
    assert coverage.status_code == 200
    assert coverage.json()["coverage"]["workouts"]["count"] == 1


@pytest.mark.asyncio
async def test_sleep_overlap_preserves_adjacent_raw_intervals(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    query_seed_body: dict,
):
    await _seed(client, ingest_headers, query_seed_body)
    resp = await client.get(
        "/v1/query/sleep-intervals?"
        + _q(start="2026-08-06T00:00:00Z", end="2026-08-06T02:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [
        "cccccccc-1111-2222-3333-444444444401",
        "cccccccc-1111-2222-3333-444444444402",
    ]
    assert items[0]["stage"] == "core"
    assert items[1]["stage"] == "deep"
    assert items[0]["start_time"].startswith("2026-08-05T23:10:00")
    assert items[0]["end_time"].startswith("2026-08-06T00:30:00")
    assert items[1]["start_time"].startswith("2026-08-06T00:30:00")
    assert items[0]["duration_minutes"] == 80.0
    assert items[1]["duration_minutes"] == 40.0

    await _seed(
        client,
        ingest_headers,
        _export_body(
            sleep_sessions=[
                _sleep(
                    "sl-touch-start",
                    "2026-08-05T10:00:00.000Z",
                    "2026-08-05T12:00:00.000Z",
                    "awake",
                ),
                _sleep(
                    "sl-at-end",
                    "2026-08-05T18:00:00.000Z",
                    "2026-08-05T19:00:00.000Z",
                    "rem",
                ),
            ]
        ),
    )
    bounded = await client.get(
        "/v1/query/sleep-intervals?"
        + _q(start="2026-08-05T12:00:00Z", end="2026-08-05T18:00:00Z"),
        headers=read_headers,
    )
    assert bounded.status_code == 200
    assert bounded.json()["items"] == []


@pytest.mark.asyncio
async def test_weight_point_window_boundaries(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            weight_measurements=[
                _weight("wt-before", "2026-08-04T07:59:59.000Z", 70.0),
                _weight("wt-start", "2026-08-04T08:00:00.000Z", 71.25),
                _weight("wt-end", "2026-08-04T09:00:00.000Z", 72.0),
            ]
        ),
    )
    resp = await client.get(
        "/v1/query/weight-measurements?"
        + _q(start="2026-08-04T08:00:00Z", end="2026-08-04T09:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["id"] for item in items] == ["wt-start"]
    assert items[0]["value_kg"] == 71.25
    assert items[0]["measured_at"].startswith("2026-08-04T08:00:00")
    assert items[0]["source"] == "apple_health"
    assert "unit" not in items[0]


@pytest.mark.asyncio
async def test_list_pagination_stable_order_and_cursor_isolation(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
):
    workouts = [
        _workout(
            f"wo-page-{i}",
            f"2026-08-05T0{6 + i}:00:00.000Z",
            f"2026-08-05T0{6 + i}:30:00.000Z",
            kcal=None,
            avg_hr=None,
            max_hr=None,
        )
        for i in range(3)
    ]
    sleeps = [
        _sleep(
            f"sl-page-{i}",
            f"2026-08-05T0{6 + i}:00:00.000Z",
            f"2026-08-05T0{6 + i}:20:00.000Z",
            "core",
        )
        for i in range(3)
    ]
    weights = [
        _weight(f"wt-page-{i}", f"2026-08-05T0{6 + i}:00:00.000Z", 70.0 + i)
        for i in range(3)
    ]
    await _seed(
        client,
        ingest_headers,
        _export_body(workouts=workouts, sleep_sessions=sleeps, weight_measurements=weights),
    )

    page_params = {
        "start": "2026-08-05T00:00:00Z",
        "end": "2026-08-06T00:00:00Z",
        "limit": "1",
    }

    wo1 = await client.get("/v1/query/workouts", params=page_params, headers=read_headers)
    sl1 = await client.get(
        "/v1/query/sleep-intervals", params=page_params, headers=read_headers
    )
    wt1 = await client.get(
        "/v1/query/weight-measurements", params=page_params, headers=read_headers
    )
    assert wo1.status_code == sl1.status_code == wt1.status_code == 200
    assert wo1.json()["truncated"] is True
    assert sl1.json()["truncated"] is True
    assert wt1.json()["truncated"] is True
    assert wo1.json()["items"][0]["id"] == "wo-page-0"
    assert sl1.json()["items"][0]["id"] == "sl-page-0"
    assert wt1.json()["items"][0]["id"] == "wt-page-0"

    wo_cursor = wo1.json()["next_cursor"]
    sl_cursor = sl1.json()["next_cursor"]
    wt_cursor = wt1.json()["next_cursor"]

    wo2 = await client.get(
        "/v1/query/workouts",
        params={**page_params, "cursor": wo_cursor},
        headers=read_headers,
    )
    assert wo2.status_code == 200
    assert wo2.json()["items"][0]["id"] == "wo-page-1"

    crossed = await client.get(
        "/v1/query/sleep-intervals",
        params={**page_params, "cursor": wo_cursor},
        headers=read_headers,
    )
    assert crossed.status_code == 422
    assert crossed.json()["error"]["code"] == "INVALID_CURSOR"

    mismatched = await client.get(
        "/v1/query/workouts",
        params={
            "start": "2026-08-05T00:00:00Z",
            "end": "2026-08-05T12:00:00Z",
            "limit": "1",
            "cursor": wo_cursor,
        },
        headers=read_headers,
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "INVALID_CURSOR"

    bad = await client.get(
        "/v1/query/weight-measurements",
        params={**page_params, "cursor": "not-a-valid-cursor"},
        headers=read_headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_CURSOR"

    meal_with_wo = await client.get(
        "/v1/query/meals",
        params={**page_params, "cursor": wo_cursor},
        headers=read_headers,
    )
    assert meal_with_wo.status_code == 422
    assert meal_with_wo.json()["error"]["code"] == "INVALID_CURSOR"

    wo3 = await client.get(
        "/v1/query/workouts",
        params={**page_params, "cursor": wo2.json()["next_cursor"]},
        headers=read_headers,
    )
    assert [
        wo1.json()["items"][0]["id"],
        wo2.json()["items"][0]["id"],
        wo3.json()["items"][0]["id"],
    ] == ["wo-page-0", "wo-page-1", "wo-page-2"]
    assert wo3.json()["truncated"] is False
    assert wo3.json()["next_cursor"] is None

    sl2 = await client.get(
        "/v1/query/sleep-intervals",
        params={**page_params, "cursor": sl_cursor},
        headers=read_headers,
    )
    wt2 = await client.get(
        "/v1/query/weight-measurements",
        params={**page_params, "cursor": wt_cursor},
        headers=read_headers,
    )
    assert sl2.json()["items"][0]["id"] == "sl-page-1"
    assert wt2.json()["items"][0]["id"] == "wt-page-1"


# --- M2: last logged meal / context snapshot ---


M2_ANCHOR = "2026-08-15T14:00:00Z"
M2_ANCHOR_DT = datetime(2026, 8, 15, 14, 0, tzinfo=UTC)
M2_FOOD = "SECRET_M2_FOOD_xyz"
LAST_MEAL_PATH = "/v1/query/last-logged-meal"
SNAPSHOT_PATH = "/v1/query/context-snapshot"
M2_SENSITIVE_KEYS = (
    "notes",
    "metadata",
    "user_id",
    "source_name",
    "ingested_at",
    "updated_at",
    "deleted_at",
    "health_source_id",
    "average_heart_rate",
    "maximum_heart_rate",
    "active_energy_kcal",
    "stage",
    "points",
)


def _last_meal_url(**params: str) -> str:
    query = {"anchor": M2_ANCHOR, **params}
    return LAST_MEAL_PATH + "?" + _q(**query)


def _snapshot_url(**params: str) -> str:
    query = {"anchor": M2_ANCHOR, **params}
    return SNAPSHOT_PATH + "?" + _q(**query)


@pytest.mark.asyncio
@pytest.mark.parametrize("path_fn", [_last_meal_url, _snapshot_url])
async def test_m2_missing_auth_returns_401(client: AsyncClient, path_fn):
    resp = await client.get(path_fn())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize("path_fn", [_last_meal_url, _snapshot_url])
async def test_m2_wrong_key_returns_401(client: AsyncClient, path_fn):
    resp = await client.get(path_fn(), headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "wrong-key" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path_fn", [_last_meal_url, _snapshot_url])
async def test_m2_ingest_key_returns_401(
    client: AsyncClient, ingest_headers: dict, path_fn
):
    resp = await client.get(path_fn(), headers=ingest_headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize("path_fn", [_last_meal_url, _snapshot_url])
async def test_m2_naive_anchor_rejected(client: AsyncClient, read_headers: dict, path_fn):
    resp = await client.get(
        path_fn().replace(M2_ANCHOR, "2026-08-15T14:00:00"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
@pytest.mark.parametrize("path_fn", [_last_meal_url, _snapshot_url])
async def test_m2_invalid_timezone_rejected(
    client: AsyncClient, read_headers: dict, path_fn
):
    resp = await client.get(path_fn(timezone="Not/A_Zone"), headers=read_headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIMEZONE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_fn", "param"),
    [
        (_last_meal_url, "lookback_days"),
        (_snapshot_url, "meal_lookback_days"),
        (_snapshot_url, "sleep_lookback_hours"),
        (_snapshot_url, "glucose_lookback_hours"),
    ],
)
@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "true"])
async def test_m2_invalid_lookback_rejected(
    client: AsyncClient, read_headers: dict, path_fn, param: str, bad: str
):
    resp = await client.get(path_fn(**{param: bad}), headers=read_headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_LOOKBACK"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_fn", "param", "too_big"),
    [
        (_last_meal_url, "lookback_days", "31"),
        (_snapshot_url, "meal_lookback_days", "31"),
        (_snapshot_url, "sleep_lookback_hours", "37"),
        (_snapshot_url, "glucose_lookback_hours", "49"),
    ],
)
async def test_m2_lookback_above_max_rejected(
    client: AsyncClient, read_headers: dict, path_fn, param: str, too_big: str
):
    resp = await client.get(path_fn(**{param: too_big}), headers=read_headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "RANGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_last_logged_meal_boundaries_latest_and_ties(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-at-anchor", "2026-08-15T14:00:00.000Z", foods=[M2_FOOD]),
                _meal("meal-after-anchor", "2026-08-15T14:00:01.000Z", foods=["after"]),
                _meal("meal-at-lookback", "2026-07-16T14:00:00.000Z", foods=["bound"]),
                _meal("meal-before-lookback", "2026-07-16T13:59:59.000Z", foods=["stale"]),
                _meal("meal-older", "2026-08-13T12:00:00.000Z", foods=["older"]),
            ]
        ),
    )
    included = await client.get(_last_meal_url(), headers=read_headers)
    assert included.status_code == 200
    body = included.json()
    assert body["meal"]["id"] == "meal-at-anchor"
    assert body["meal"]["foods"] == [M2_FOOD]
    assert body["lookback_days"] == 30
    assert body["timezone"] == "America/New_York"
    assert "next_cursor" not in body
    assert "truncated" not in body
    for key in M2_SENSITIVE_KEYS:
        assert key not in body["meal"]
        assert key not in body
    assert "secret-note" not in included.text
    meal_at = datetime.fromisoformat(body["meal"]["meal_completed_at"].replace("Z", "+00:00"))
    assert body["derived"]["minutes_since_last_logged_meal"] == _duration_minutes(
        meal_at, M2_ANCHOR_DT
    )
    assert (
        body["derived"]["basis"]
        == "anchor minus meal_completed_at of the latest logged meal"
    )
    limits = " ".join(body["limits"]).lower()
    assert "logged meal" in limits
    assert "fasting" in limits
    assert "medical advice" in limits

    only_bound = await client.get(
        _last_meal_url(lookback_days="30"),
        headers=read_headers,
    )
    assert only_bound.json()["meal"]["id"] == "meal-at-anchor"

    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-a", "2026-08-14T12:00:00.000Z", foods=["a"]),
                _meal("meal-z", "2026-08-14T12:00:00.000Z", foods=["z"]),
            ]
        ),
    )
    tied = await client.get(
        _last_meal_url(anchor="2026-08-14T12:00:00Z"),
        headers=read_headers,
    )
    assert tied.status_code == 200
    assert tied.json()["meal"]["id"] == "meal-z"


@pytest.mark.asyncio
async def test_last_logged_meal_lookback_bound_included(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-at-lookback", "2026-07-16T14:00:00.000Z", foods=["bound"]),
            ]
        ),
    )
    resp = await client.get(_last_meal_url(), headers=read_headers)
    assert resp.status_code == 200
    assert resp.json()["meal"]["id"] == "meal-at-lookback"


@pytest.mark.asyncio
async def test_last_logged_meal_before_lookback_and_after_anchor_excluded(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-after-anchor", "2026-08-15T14:00:01.000Z"),
                _meal("meal-before-lookback", "2026-07-16T13:59:59.000Z"),
            ]
        ),
    )
    resp = await client.get(_last_meal_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["meal"] is None
    assert body["derived"] == {
        "minutes_since_last_logged_meal": None,
        "basis": None,
    }
    limits = " ".join(body["limits"]).lower()
    assert "no logged meal" in limits
    assert "fasting" in limits


@pytest.mark.asyncio
async def test_last_logged_meal_empty_window(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(_last_meal_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["meal"] is None
    assert body["derived"]["minutes_since_last_logged_meal"] is None
    assert body["derived"]["basis"] is None
    assert body["lookback_days"] == 30


@pytest.mark.asyncio
async def test_last_logged_meal_query_failed_sanitized(
    client: AsyncClient, read_headers: dict
):
    with patch(
        "app.services.query_service.HealthDataQueryService.resolve_personal_user",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection to postgresql://secret@db failed"),
    ):
        resp = await client.get(_last_meal_url(), headers=read_headers)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "QUERY_FAILED"
    assert "postgresql" not in resp.text.lower()
    assert "secret" not in resp.text


@pytest.mark.asyncio
async def test_snapshot_meal_matches_last_logged_meal(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("snap-meal", "2026-08-13T23:42:00.000Z", foods=[M2_FOOD]),
            ]
        ),
    )
    last_meal = await client.get(_last_meal_url(), headers=read_headers)
    snapshot = await client.get(_snapshot_url(), headers=read_headers)
    assert last_meal.status_code == snapshot.status_code == 200
    assert snapshot.json()["last_logged_meal"] == last_meal.json()["meal"]
    assert snapshot.json()["derived"] == last_meal.json()["derived"]
    assert snapshot.json()["last_logged_meal"]["foods"] == [M2_FOOD]


@pytest.mark.asyncio
async def test_snapshot_workout_bounds_and_ties(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout(
                    "wo-after-anchor",
                    "2026-08-15T13:00:00.000Z",
                    "2026-08-15T14:00:01.000Z",
                ),
                _workout(
                    "wo-at-anchor",
                    "2026-08-15T13:00:00.000Z",
                    "2026-08-15T14:00:00.000Z",
                ),
                _workout(
                    "wo-at-14d",
                    "2026-08-01T13:00:00.000Z",
                    "2026-08-01T14:00:00.000Z",
                ),
                _workout(
                    "wo-before-14d",
                    "2026-08-01T12:00:00.000Z",
                    "2026-08-01T13:59:59.000Z",
                ),
            ]
        ),
    )
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["most_recent_workout"]["id"] == "wo-at-anchor"
    assert "average_heart_rate" not in body["most_recent_workout"]
    assert "active_energy_kcal" not in body["most_recent_workout"]
    assert "source_name" not in body["most_recent_workout"]

    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout("wo-a", "2026-08-10T12:00:00.000Z", "2026-08-10T13:00:00.000Z"),
                _workout("wo-z", "2026-08-10T12:00:00.000Z", "2026-08-10T13:00:00.000Z"),
            ]
        ),
    )
    tied = await client.get(
        _snapshot_url(anchor="2026-08-10T13:00:00Z"), headers=read_headers
    )
    assert tied.json()["most_recent_workout"]["id"] == "wo-z"

    only_bound = await client.get(
        _snapshot_url(anchor="2026-08-15T14:00:00Z"),
        headers=read_headers,
    )
    # After the second seed, wo-at-anchor is gone (truncate between tests? No, same test,
    # truncate is per client fixture, second seed adds more workouts).
    # The second seed does NOT truncate - ingest upserts. First workouts remain.
    # Latest completed at the original anchor is still wo-at-anchor.
    assert only_bound.json()["most_recent_workout"]["id"] == "wo-at-anchor"


@pytest.mark.asyncio
async def test_snapshot_workout_14d_bound_included_and_before_excluded(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            workouts=[
                _workout(
                    "wo-at-14d",
                    "2026-08-01T13:00:00.000Z",
                    "2026-08-01T14:00:00.000Z",
                ),
                _workout(
                    "wo-before-14d",
                    "2026-08-01T12:00:00.000Z",
                    "2026-08-01T13:59:59.000Z",
                ),
                _workout(
                    "wo-after-anchor",
                    "2026-08-15T13:30:00.000Z",
                    "2026-08-15T14:00:01.000Z",
                ),
            ]
        ),
    )
    included = await client.get(_snapshot_url(), headers=read_headers)
    assert included.status_code == 200
    assert included.json()["most_recent_workout"]["id"] == "wo-at-14d"

    before_only = await client.get(
        SNAPSHOT_PATH + "?" + _q(anchor="2026-08-01T13:59:59Z"),
        headers=read_headers,
    )
    assert before_only.json()["most_recent_workout"]["id"] == "wo-before-14d"


@pytest.mark.asyncio
async def test_snapshot_weight_latest_closed_bounds(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            weight_measurements=[
                _weight("wt-at-anchor", "2026-08-15T14:00:00.000Z", 80.0),
                _weight("wt-at-30d", "2026-07-16T14:00:00.000Z", 81.0),
                _weight("wt-before-30d", "2026-07-16T13:59:59.000Z", 82.0),
            ]
        ),
    )
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 200
    item = resp.json()["most_recent_weight_measurement"]
    assert item["id"] == "wt-at-anchor"
    assert item["value_kg"] == 80.0
    assert "source_name" not in item
    assert "unit" not in item


@pytest.mark.asyncio
async def test_snapshot_weight_exactly_30d_included(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            weight_measurements=[
                _weight("wt-at-30d", "2026-07-16T14:00:00.000Z", 81.0),
            ]
        ),
    )
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.json()["most_recent_weight_measurement"]["id"] == "wt-at-30d"


@pytest.mark.asyncio
async def test_snapshot_stale_weight_unavailable(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            weight_measurements=[
                _weight("wt-stale", "2026-07-16T13:59:59.000Z", 82.0),
            ]
        ),
    )
    resp = await client.get(_snapshot_url(), headers=read_headers)
    body = resp.json()
    assert body["most_recent_weight_measurement"] is None
    assert {
        "category": "most_recent_weight_measurement",
        "reason": "no_record_in_lookback",
    } in body["unavailable"]


@pytest.mark.asyncio
async def test_snapshot_sleep_overlap_and_aggregate(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            sleep_sessions=[
                _sleep(
                    "sl-overlap",
                    "2026-08-14T13:00:00.000Z",
                    "2026-08-14T15:00:00.000Z",
                    "core",
                ),
                _sleep(
                    "sl-end-at-window-start",
                    "2026-08-14T12:00:00.000Z",
                    "2026-08-14T14:00:00.000Z",
                    "awake",
                ),
                _sleep(
                    "sl-start-at-anchor",
                    "2026-08-15T14:00:00.000Z",
                    "2026-08-15T15:00:00.000Z",
                    "rem",
                ),
                _sleep(
                    "sl-late",
                    "2026-08-15T02:13:00.000Z",
                    "2026-08-15T09:01:00.000Z",
                    "deep",
                ),
            ]
        ),
    )
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 200
    sleep = resp.json()["recent_sleep_intervals"]
    assert sleep["record_count"] == 2
    assert sleep["first_start_time"].startswith("2026-08-14T13:00:00")
    assert sleep["last_end_time"].startswith("2026-08-15T09:01:00")
    assert sleep["sources"] == ["apple_health"]
    assert "stage" not in sleep
    assert "items" not in sleep
    assert '"stage"' not in resp.text


@pytest.mark.asyncio
async def test_snapshot_sleep_counts_beyond_list_page(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    intervals = [
        _sleep(
            f"sl-many-{i}",
            f"2026-08-14T18:{i:02d}:00.000Z" if i < 60 else f"2026-08-14T19:{i - 60:02d}:00.000Z",
            f"2026-08-14T18:{i:02d}:30.000Z" if i < 60 else f"2026-08-14T19:{i - 60:02d}:30.000Z",
            "asleep",
        )
        for i in range(101)
    ]
    # Mix a second source on one row for distinct sorted sources.
    intervals[0]["source"] = "manual"
    await _seed(client, ingest_headers, _export_body(sleep_sessions=intervals))
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 200
    sleep = resp.json()["recent_sleep_intervals"]
    assert sleep["record_count"] == 101
    assert sleep["sources"] == ["apple_health", "manual"]
    assert "items" not in sleep


@pytest.mark.asyncio
async def test_snapshot_empty_sleep_and_glucose_unavailable(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["recent_sleep_intervals"] == {
        "record_count": 0,
        "first_start_time": None,
        "last_end_time": None,
        "sources": [],
    }
    assert body["glucose_summary"]["sample_count"] == 0
    assert body["glucose_summary"]["first_at"] is None
    assert body["glucose_coverage"] == {
        "count": 0,
        "first_at": None,
        "last_at": None,
    }
    assert body["last_logged_meal"] is None
    assert body["most_recent_workout"] is None
    assert body["most_recent_weight_measurement"] is None
    assert body["derived"] == {
        "minutes_since_last_logged_meal": None,
        "basis": None,
    }
    reasons = {(item["category"], item["reason"]) for item in body["unavailable"]}
    assert reasons == {
        ("last_logged_meal", "no_record_in_lookback"),
        ("most_recent_workout", "no_record_in_lookback"),
        ("recent_sleep_intervals", "no_record_in_lookback"),
        ("most_recent_weight_measurement", "no_record_in_lookback"),
        ("glucose_coverage", "no_samples_in_window"),
        ("glucose_summary", "no_samples_in_window"),
    }
    assert "points" not in body
    assert "start" not in body
    assert "end" not in body
    limits = " ".join(body["limits"]).lower()
    assert "fasting" in limits
    assert "raw" in limits and "sleep" in limits
    assert "medical advice" in limits


@pytest.mark.asyncio
async def test_snapshot_partial_data_and_no_sensitive_fields(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[_meal("partial-meal", "2026-08-13T23:42:00.000Z", foods=[M2_FOOD])],
            workouts=[
                _workout(
                    "partial-wo",
                    "2026-08-10T12:00:00.000Z",
                    "2026-08-10T13:00:00.000Z",
                )
            ],
        ),
    )
    records: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _ListHandler()
    handler.setLevel(logging.INFO)
    query_logger.addHandler(handler)
    previous_level = query_logger.level
    previous_disabled = query_logger.disabled
    query_logger.setLevel(logging.INFO)
    query_logger.disabled = False
    try:
        resp = await client.get(_snapshot_url(), headers=read_headers)
    finally:
        query_logger.removeHandler(handler)
        query_logger.setLevel(previous_level)
        query_logger.disabled = previous_disabled
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_logged_meal"]["foods"] == [M2_FOOD]
    assert body["most_recent_workout"]["id"] == "partial-wo"
    assert body["recent_sleep_intervals"]["record_count"] == 0
    assert body["most_recent_weight_measurement"] is None
    assert body["glucose_summary"]["sample_count"] == 0
    cats = {item["category"] for item in body["unavailable"]}
    assert cats == {
        "recent_sleep_intervals",
        "most_recent_weight_measurement",
        "glucose_coverage",
        "glucose_summary",
    }
    assert "points" not in body
    assert "notes" not in resp.text
    assert "source_name" not in resp.text
    assert "average_heart_rate" not in resp.text
    assert "active_energy_kcal" not in resp.text
    assert '"stage"' not in resp.text
    log_text = "\n".join(records)
    assert "query_access" in log_text
    assert "anchor=" in log_text
    assert M2_FOOD not in log_text
    assert "partial-meal" not in log_text
    assert "secret-note" not in log_text


@pytest.mark.asyncio
async def test_snapshot_query_failed_sanitized(client: AsyncClient, read_headers: dict):
    with patch(
        "app.services.query_service.HealthDataQueryService.resolve_personal_user",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection to postgresql://secret@db failed"),
    ):
        resp = await client.get(_snapshot_url(), headers=read_headers)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "QUERY_FAILED"
    assert "postgresql" not in resp.text.lower()
    assert "secret" not in resp.text


# --- M3: personal timeline ---


TIMELINE_PATH = "/v1/query/personal-timeline"
TIMELINE_START = "2026-08-10T04:00:00Z"
TIMELINE_END = "2026-08-13T04:00:00Z"
M3_FOOD = "SECRET_M3_FOOD_xyz"
M3_STAGE = "SECRET_M3_STAGE_xyz"
M3_WEIGHT = 77.125
M3_GLUCOSE = 141.5
M3_SENSITIVE_KEYS = (
    "notes",
    "metadata",
    "user_id",
    "source_name",
    "ingested_at",
    "updated_at",
    "deleted_at",
    "health_source_id",
    "average_heart_rate",
    "maximum_heart_rate",
    "active_energy_kcal",
    "next_cursor",
    "record_count",
    "unavailable",
    "quality",
    "session",
    "readiness",
)


def _timeline_url(**params: str) -> str:
    query = {"start": TIMELINE_START, "end": TIMELINE_END, **params}
    return TIMELINE_PATH + "?" + _q(**query)


def _assert_no_envelope_pagination(body: dict) -> None:
    assert "next_cursor" not in body
    assert "truncated" not in body
    assert "record_count" not in body
    assert "data_fresh_through" not in body


@pytest.mark.asyncio
async def test_timeline_naive_timestamps_rejected(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(
        TIMELINE_PATH
        + "?"
        + _q(start="2026-08-10T04:00:00", end="2026-08-13T04:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
async def test_timeline_invalid_timezone_rejected(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(
        _timeline_url(timezone="Not/A_Zone"), headers=read_headers
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIMEZONE"


@pytest.mark.asyncio
async def test_timeline_end_not_after_start_rejected(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(
        TIMELINE_PATH
        + "?"
        + _q(start="2026-08-13T04:00:00Z", end="2026-08-10T04:00:00Z"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME_RANGE"


@pytest.mark.asyncio
async def test_timeline_exact_72_hours_succeeds(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(_timeline_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["start"].startswith("2026-08-10T04:00:00")
    assert body["end"].startswith("2026-08-13T04:00:00")
    assert body["timezone"] == "America/New_York"
    assert body["glucose_resolution"] == "15m"


@pytest.mark.asyncio
async def test_timeline_72_hours_plus_one_second_rejected(
    client: AsyncClient, read_headers: dict
):
    resp = await client.get(
        TIMELINE_PATH
        + "?"
        + _q(start="2026-08-10T04:00:00Z", end="2026-08-13T04:00:01Z"),
        headers=read_headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "RANGE_TOO_LARGE"
    assert body["error"]["details"] == {"max_hours": 72}


@pytest.mark.asyncio
async def test_timeline_range_uses_elapsed_hours_not_civil_days(
    client: AsyncClient, read_headers: dict
):
    # US Eastern fall-back 2026-11-01: three civil days is 73 elapsed hours.
    too_big = await client.get(
        TIMELINE_PATH
        + "?"
        + _q(start="2026-10-31T12:00:00-04:00", end="2026-11-03T12:00:00-05:00"),
        headers=read_headers,
    )
    assert too_big.status_code == 422
    assert too_big.json()["error"]["code"] == "RANGE_TOO_LARGE"
    assert too_big.json()["error"]["details"]["max_hours"] == 72

    exact = await client.get(
        TIMELINE_PATH
        + "?"
        + _q(start="2026-10-31T12:00:00-04:00", end="2026-11-03T11:00:00-05:00"),
        headers=read_headers,
    )
    assert exact.status_code == 200


@pytest.mark.asyncio
async def test_timeline_selection_boundaries_ordering_and_public_fields(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-at-start", "2026-08-10T04:00:00.000Z", foods=[M3_FOOD]),
                _meal("meal-at-end", "2026-08-13T04:00:00.000Z", foods=["excluded"]),
                _meal("meal-z", "2026-08-11T12:00:00.000Z", foods=["z"]),
                _meal("meal-a", "2026-08-11T12:00:00.000Z", foods=["a"]),
            ],
            workouts=[
                _workout(
                    "wo-overlap",
                    "2026-08-10T02:00:00.000Z",
                    "2026-08-10T06:00:00.000Z",
                ),
                _workout(
                    "wo-end-at-start",
                    "2026-08-10T02:00:00.000Z",
                    "2026-08-10T04:00:00.000Z",
                ),
                _workout(
                    "wo-start-at-end",
                    "2026-08-13T04:00:00.000Z",
                    "2026-08-13T06:00:00.000Z",
                ),
            ],
            sleep_sessions=[
                _sleep(
                    "sl-overlap",
                    "2026-08-10T03:00:00.000Z",
                    "2026-08-10T05:00:00.000Z",
                    M3_STAGE,
                ),
                _sleep(
                    "sl-adjacent-a",
                    "2026-08-11T01:00:00.000Z",
                    "2026-08-11T02:00:00.000Z",
                    "core",
                ),
                _sleep(
                    "sl-adjacent-b",
                    "2026-08-11T02:00:00.000Z",
                    "2026-08-11T03:00:00.000Z",
                    "deep",
                ),
                _sleep(
                    "sl-end-at-start",
                    "2026-08-10T02:00:00.000Z",
                    "2026-08-10T04:00:00.000Z",
                    "awake",
                ),
                _sleep(
                    "sl-start-at-end",
                    "2026-08-13T04:00:00.000Z",
                    "2026-08-13T06:00:00.000Z",
                    "rem",
                ),
            ],
            weight_measurements=[
                _weight("wt-at-start", "2026-08-10T04:00:00.000Z", M3_WEIGHT),
                _weight("wt-at-end", "2026-08-13T04:00:00.000Z", 80.0),
                _weight("wt-z", "2026-08-11T08:00:00.000Z", 70.0),
                _weight("wt-a", "2026-08-11T08:00:00.000Z", 71.0),
            ],
        ),
    )
    resp = await client.get(_timeline_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_envelope_pagination(body)

    assert [item["id"] for item in body["meals"]] == [
        "meal-at-start",
        "meal-a",
        "meal-z",
    ]
    assert body["meals"][0]["foods"] == [M3_FOOD]
    assert "notes" not in body["meals"][0]
    assert "metadata" not in body["meals"][0]
    assert "secret-note" not in resp.text

    assert [item["id"] for item in body["workouts"]] == ["wo-overlap"]
    overlap = body["workouts"][0]
    assert overlap["start_time"].startswith("2026-08-10T02:00:00")
    assert overlap["end_time"].startswith("2026-08-10T06:00:00")
    for key in (
        "average_heart_rate",
        "maximum_heart_rate",
        "active_energy_kcal",
        "metadata",
        "source_name",
        "user_id",
        "health_source_id",
    ):
        assert key not in overlap
    assert "148" not in resp.text
    assert "310" not in resp.text

    assert [item["id"] for item in body["sleep_intervals"]] == [
        "sl-overlap",
        "sl-adjacent-a",
        "sl-adjacent-b",
    ]
    assert body["sleep_intervals"][0]["stage"] == M3_STAGE
    assert body["sleep_intervals"][0]["start_time"].startswith("2026-08-10T03:00:00")
    assert body["sleep_intervals"][0]["end_time"].startswith("2026-08-10T05:00:00")
    assert body["sleep_intervals"][1]["id"] != body["sleep_intervals"][2]["id"]
    for item in body["sleep_intervals"]:
        assert "quality" not in item
        assert "session" not in item
        assert "readiness" not in item

    assert [item["id"] for item in body["weight_measurements"]] == [
        "wt-at-start",
        "wt-a",
        "wt-z",
    ]
    assert body["weight_measurements"][0]["value_kg"] == M3_WEIGHT
    assert "unit" not in body["weight_measurements"][0]
    assert "value_lb" not in body["weight_measurements"][0]

    for key in M3_SENSITIVE_KEYS:
        assert key not in body
    assert "user_id" not in resp.text
    assert "ingested_at" not in resp.text
    assert "deleted_at" not in resp.text

    cov = body["coverage"]
    assert set(cov) == {
        "glucose",
        "meals",
        "workouts",
        "sleep_intervals",
        "weight_measurements",
    }
    assert cov["meals"]["count"] == len(body["meals"])
    assert cov["workouts"]["count"] == len(body["workouts"])
    assert cov["sleep_intervals"]["count"] == len(body["sleep_intervals"])
    assert cov["weight_measurements"]["count"] == len(body["weight_measurements"])
    assert cov["glucose"]["count"] == 0
    assert cov["glucose"]["first_at"] is None
    assert cov["glucose"]["last_at"] is None


@pytest.mark.asyncio
async def test_timeline_glucose_matches_15m_aggregation_and_empty_shape(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    empty = await client.get(_timeline_url(), headers=read_headers)
    assert empty.status_code == 200
    empty_body = empty.json()
    assert empty_body["meals"] == []
    assert empty_body["workouts"] == []
    assert empty_body["sleep_intervals"] == []
    assert empty_body["weight_measurements"] == []
    assert empty_body["glucose_resolution"] == "15m"
    glucose = empty_body["glucose"]
    assert glucose == {
        "aggregation": "mean_min_max",
        "source_record_count": 0,
        "returned_point_count": 0,
        "truncated": False,
        "data_fresh_through": None,
        "points": [],
    }
    assert "resolution" not in glucose
    assert "request_id" not in glucose
    assert "start" not in glucose
    assert "end" not in glucose
    assert "timezone" not in glucose
    _assert_no_envelope_pagination(empty_body)
    for cat in empty_body["coverage"].values():
        assert cat == {"count": 0, "first_at": None, "last_at": None}
    limits = " ".join(empty_body["limits"]).lower()
    assert "read-only" in limits
    assert "notes" in limits
    assert "raw" in limits and "sleep" in limits
    assert "15-minute" in limits
    assert "medical advice" in limits

    await _seed(
        client,
        ingest_headers,
        _export_body(
            glucose_samples=[
                _glucose("g1", "2026-08-11T14:16:00Z", 90),
                _glucose("g2", "2026-08-11T14:18:00Z", 100),
                _glucose("g3", "2026-08-11T14:22:00Z", 110),
                _glucose("g4", "2026-08-11T15:05:00Z", M3_GLUCOSE),
            ]
        ),
    )
    window = _q(start="2026-08-11T14:00:00Z", end="2026-08-11T16:00:00Z")
    series = await client.get(
        "/v1/query/glucose/series?" + window + "&resolution=15m",
        headers=read_headers,
    )
    timeline = await client.get(
        TIMELINE_PATH + "?" + window, headers=read_headers
    )
    assert series.status_code == 200
    assert timeline.status_code == 200
    tbody = timeline.json()
    sbody = series.json()
    assert tbody["glucose_resolution"] == "15m"
    assert tbody["glucose"]["aggregation"] == "mean_min_max"
    assert tbody["glucose"]["truncated"] is False
    assert tbody["glucose"]["source_record_count"] == sbody["source_record_count"]
    assert tbody["glucose"]["returned_point_count"] == sbody["returned_point_count"]
    assert tbody["glucose"]["points"] == sbody["points"]
    assert tbody["glucose"]["data_fresh_through"] == sbody["data_fresh_through"]
    assert "resolution" not in tbody["glucose"]
    for point in tbody["glucose"]["points"]:
        assert set(point) == {
            "start",
            "end",
            "mean_mg_dl",
            "min_mg_dl",
            "max_mg_dl",
            "sample_count",
        }
        assert "timestamp" not in point
        assert "value_mg_dl" not in point


@pytest.mark.asyncio
async def test_timeline_sleep_more_than_public_page_limit(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    base = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    intervals = []
    for i in range(101):
        start = base + timedelta(minutes=i * 2)
        end = start + timedelta(minutes=1)
        intervals.append(
            _sleep(
                f"sl-many-{i:03d}",
                start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "core",
            )
        )
    await _seed(client, ingest_headers, _export_body(sleep_sessions=intervals))
    resp = await client.get(_timeline_url(), headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sleep_intervals"]) == 101
    assert body["coverage"]["sleep_intervals"]["count"] == 101
    _assert_no_envelope_pagination(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "seed_kwargs"),
    [
        (
            "sleep_intervals",
            {
                "sleep_sessions": [
                    _sleep(
                        f"sl-cap-{i}",
                        f"2026-08-11T0{i}:00:00.000Z",
                        f"2026-08-11T0{i}:30:00.000Z",
                        "core",
                    )
                    for i in range(3)
                ]
            },
        ),
        (
            "meals",
            {
                "meal_events": [
                    _meal(f"meal-cap-{i}", f"2026-08-11T0{i}:00:00.000Z")
                    for i in range(3)
                ]
            },
        ),
        (
            "workouts",
            {
                "workouts": [
                    _workout(
                        f"wo-cap-{i}",
                        f"2026-08-11T0{i}:00:00.000Z",
                        f"2026-08-11T0{i}:30:00.000Z",
                    )
                    for i in range(3)
                ]
            },
        ),
        (
            "weight_measurements",
            {
                "weight_measurements": [
                    _weight(f"wt-cap-{i}", f"2026-08-11T0{i}:00:00.000Z", 70.0 + i)
                    for i in range(3)
                ]
            },
        ),
    ],
)
async def test_timeline_event_cap_fails_whole_request(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    seed_kwargs: dict,
):
    monkeypatch.setattr(
        "app.services.query_service.MAX_TIMELINE_ITEMS_PER_CATEGORY", 2
    )
    await _seed(client, ingest_headers, _export_body(**seed_kwargs))
    resp = await client.get(_timeline_url(), headers=read_headers)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "RESULT_TOO_LARGE"
    assert body["error"]["details"] == {"max_items": 2, "category": category}
    assert "meals" not in body
    assert "glucose" not in body
    assert "coverage" not in body
    assert "max_points" not in (body["error"].get("details") or {})


@pytest.mark.asyncio
async def test_timeline_logs_safe_metadata_and_sanitized_failure(
    client: AsyncClient, ingest_headers: dict, read_headers: dict
):
    await _seed(
        client,
        ingest_headers,
        _export_body(
            meal_events=[
                _meal("meal-m3-log", "2026-08-11T12:00:00.000Z", foods=[M3_FOOD])
            ],
            workouts=[
                _workout(
                    "wo-m3-log",
                    "2026-08-11T06:00:00.000Z",
                    "2026-08-11T06:32:00.000Z",
                )
            ],
            sleep_sessions=[
                _sleep(
                    "sl-m3-log",
                    "2026-08-11T01:00:00.000Z",
                    "2026-08-11T02:00:00.000Z",
                    M3_STAGE,
                )
            ],
            weight_measurements=[
                _weight("wt-m3-log", "2026-08-11T08:00:00.000Z", M3_WEIGHT)
            ],
            glucose_samples=[
                _glucose("g-m3-log", "2026-08-11T14:16:00Z", M3_GLUCOSE)
            ],
        ),
    )
    records: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _ListHandler()
    handler.setLevel(logging.INFO)
    query_logger.addHandler(handler)
    previous_level = query_logger.level
    previous_disabled = query_logger.disabled
    query_logger.setLevel(logging.INFO)
    query_logger.disabled = False
    try:
        resp = await client.get(_timeline_url(), headers=read_headers)
    finally:
        query_logger.removeHandler(handler)
        query_logger.setLevel(previous_level)
        query_logger.disabled = previous_disabled
    assert resp.status_code == 200
    log_text = "\n".join(records)
    assert "query_access" in log_text
    assert "route=/v1/query/personal-timeline" in log_text
    assert "start=" in log_text
    assert "end=" in log_text
    assert "timezone=" in log_text
    assert "resolution=15m" in log_text
    # Four event arrays only; glucose buckets are not included in record_count.
    assert "record_count=4" in log_text
    assert "truncated=False" in log_text
    assert "latency_ms=" in log_text
    assert M3_FOOD not in log_text
    assert M3_STAGE not in log_text
    assert "meal-m3-log" not in log_text
    assert "wo-m3-log" not in log_text
    assert "sl-m3-log" not in log_text
    assert "wt-m3-log" not in log_text
    assert str(M3_WEIGHT) not in log_text
    assert str(M3_GLUCOSE) not in log_text
    assert "average_heart_rate" not in log_text
    assert "active_energy_kcal" not in log_text
    assert "secret-note" not in log_text
    assert "Authorization" not in log_text
    assert "test-read-key" not in log_text

    with patch(
        "app.services.query_service.HealthDataQueryService.resolve_personal_user",
        new_callable=AsyncMock,
        side_effect=RuntimeError(
            "connection to postgresql://secret@db SELECT * FROM meals failed"
        ),
    ):
        failed = await client.get(_timeline_url(), headers=read_headers)
    assert failed.status_code == 500
    body = failed.json()
    assert body["error"]["code"] == "QUERY_FAILED"
    assert "postgresql" not in failed.text.lower()
    assert "secret" not in failed.text
    assert "SELECT" not in failed.text
    assert "RuntimeError" not in failed.text
    assert "stack" not in failed.text.lower()
