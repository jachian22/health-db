"""Integration tests for authenticated Query API v1 (GET /v1/query/*)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _seed(client: AsyncClient, ingest_headers: dict, seed_body: dict) -> None:
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=seed_body)
    assert resp.status_code == 200, resp.text


def _q(**params: str) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())


# --- Authentication ---


@pytest.mark.asyncio
async def test_query_missing_auth_returns_401(client: AsyncClient):
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z")
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "request_id" in resp.json()


@pytest.mark.asyncio
async def test_query_invalid_read_key_returns_401(client: AsyncClient):
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
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
async def test_ingest_key_rejected_by_query_api(
    client: AsyncClient,
    ingest_headers: dict,
):
    resp = await client.get(
        "/v1/query/coverage?"
        + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
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
    ):
        resp = await client.post(
            path + "?" + _q(start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z"),
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
