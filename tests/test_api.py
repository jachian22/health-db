"""Series, summary, events, planner, and auth/bounds tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.test_ingest import SAMPLE_BATCH


async def _seed(client: AsyncClient, headers: dict) -> None:
    resp = await client.post("/v1/ingest/batch", json=SAMPLE_BATCH, headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient):
    resp = await client.post(
        "/v1/series/glucose",
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_invalid_range(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={"start": "2026-08-02T00:00:00Z", "end": "2026-08-01T00:00:00Z", "user_id": "user_1"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_RANGE"


@pytest.mark.asyncio
async def test_range_too_wide(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={
            "start": "2024-01-01T00:00:00Z",
            "end": "2026-08-01T00:00:00Z",
            "user_id": "user_1",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "RANGE_TOO_WIDE"


@pytest.mark.asyncio
async def test_unsupported_resolution(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "resolution": "3h",
            "user_id": "user_1",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_RESOLUTION"


@pytest.mark.asyncio
async def test_series_glucose_and_runs(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    g = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "resolution": "raw",
            "user_id": "user_1",
        },
    )
    assert g.status_code == 200
    assert g.json()["meta"]["count"] == 1
    assert g.json()["data"][0]["v"] == 110.0

    r = await client.post(
        "/v1/series/runs",
        headers=auth_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert r.status_code == 200
    assert r.json()["data"][0]["sport"] == "running"


@pytest.mark.asyncio
async def test_series_glucose_query_method(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.request(
        "QUERY",
        "/v1/series/glucose",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "resolution": "raw",
            "user_id": "user_1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["meta"]["count"] == 1
    assert body["data"][0]["v"] == 110.0


@pytest.mark.asyncio
async def test_series_too_many_rows(client: AsyncClient, auth_headers: dict, monkeypatch):
    monkeypatch.setenv("MAX_ROWS_PER_RESPONSE", "5")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        samples = [
            {
                "source_name": "stelo",
                "source_sample_id": f"g-limit-{i}",
                "sample_time": f"2026-08-01T10:{i:02d}:00Z",
                "value": 100.0 + i,
                "unit": "mg/dL",
            }
            for i in range(10)
        ]
        resp = await client.post(
            "/v1/ingest/batch",
            headers=auth_headers,
            json={"user_id": "user_1", "glucose_samples": samples},
        )
        assert resp.status_code == 200

        series = await client.post(
            "/v1/series/glucose",
            headers=auth_headers,
            json={
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-02T00:00:00Z",
                "resolution": "raw",
                "user_id": "user_1",
            },
        )
        assert series.status_code == 400
        assert series.json()["error"]["code"] == "TOO_MANY_ROWS"
    finally:
        monkeypatch.setenv("MAX_ROWS_PER_RESPONSE", "5000")
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_series_runs_sport_filter(client: AsyncClient, auth_headers: dict):
    batch = {
        "user_id": "user_1",
        "workouts": [
            {
                "source_name": "apple_health",
                "source_sample_id": "run-1",
                "start_time": "2026-08-01T06:00:00Z",
                "end_time": "2026-08-01T07:00:00Z",
                "sport": "running",
                "distance_m": 8000,
            },
            {
                "source_name": "apple_health",
                "source_sample_id": "cycle-1",
                "start_time": "2026-08-01T08:00:00Z",
                "end_time": "2026-08-01T09:00:00Z",
                "sport": "cycling",
                "distance_m": 20000,
            },
        ],
    }
    assert (await client.post("/v1/ingest/batch", json=batch, headers=auth_headers)).status_code == 200

    all_runs = await client.post(
        "/v1/series/runs",
        headers=auth_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert all_runs.status_code == 200
    assert all_runs.json()["meta"]["count"] == 2
    sports = {r["sport"] for r in all_runs.json()["data"]}
    assert sports == {"running", "cycling"}

    filtered = await client.post(
        "/v1/series/runs",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "user_id": "user_1",
            "sport": "running",
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["meta"]["count"] == 1
    assert filtered.json()["data"][0]["sport"] == "running"


@pytest.mark.asyncio
async def test_summary_weekly_sample_weighted(client: AsyncClient, auth_headers: dict):
    """Weekly glucose avg must be sample-weighted, not mean-of-daily-means."""
    # Mon 2026-08-03: 99 samples at 100 → daily mean 100
    # Tue 2026-08-04: 1 sample at 200 → daily mean 200
    # Mean of daily means = 150; true sample-weighted mean = (99*100 + 200)/100 = 101
    samples = [
        {
            "source_name": "stelo",
            "source_sample_id": f"g-mon-{i}",
            "sample_time": f"2026-08-03T{10 + i // 60:02d}:{i % 60:02d}:00Z",
            "value": 100.0,
            "unit": "mg/dL",
        }
        for i in range(99)
    ] + [
        {
            "source_name": "stelo",
            "source_sample_id": "g-tue-1",
            "sample_time": "2026-08-04T10:00:00Z",
            "value": 200.0,
            "unit": "mg/dL",
        }
    ]
    resp = await client.post(
        "/v1/ingest/batch",
        headers=auth_headers,
        json={"user_id": "user_1", "glucose_samples": samples},
    )
    assert resp.status_code == 200

    weekly = await client.post(
        "/v1/summary/weekly",
        headers=auth_headers,
        json={
            "start": "2026-08-03T00:00:00Z",
            "end": "2026-08-10T00:00:00Z",
            "user_id": "user_1",
        },
    )
    assert weekly.status_code == 200
    weeks = weekly.json()["data"]
    assert len(weeks) == 1
    expected = round((99 * 100.0 + 200.0) / 100, 2)
    assert weeks[0]["glucose_avg"] == expected
    assert weeks[0]["glucose_avg"] != 150.0


@pytest.mark.asyncio
async def test_series_meals_anchor(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/series/meals",
        headers=auth_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert resp.status_code == 200
    meal = resp.json()["data"][0]
    assert meal["meal_completed_at"] is not None
    assert meal["anchor"] == meal["meal_completed_at"]


@pytest.mark.asyncio
async def test_summary_daily(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/summary/daily",
        headers=auth_headers,
        json={"start": "2026-07-31T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert resp.status_code == 200
    days = resp.json()["data"]
    assert len(days) >= 1
    assert any(d["glucose_count"] > 0 or d["run_count"] > 0 for d in days)


@pytest.mark.asyncio
async def test_summary_glucose(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    resp = await client.post(
        "/v1/summary/glucose",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "group_by": "day",
            "user_id": "user_1",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 1
    assert resp.json()["data"]["avg"] == 110.0


@pytest.mark.asyncio
async def test_events_lookup(client: AsyncClient, auth_headers: dict):
    await _seed(client, auth_headers)
    meals = await client.post(
        "/v1/events/meals",
        headers=auth_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "limit": 10,
            "user_id": "user_1",
        },
    )
    assert meals.status_code == 200
    assert meals.json()["meta"]["count"] == 1
    assert meals.json()["data"][0]["meal_completed_at"] is not None


@pytest.mark.asyncio
async def test_plan_retrieve(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/v1/plan/retrieve",
        headers=auth_headers,
        json={
            "intent": "render_glucose_around_runs",
            "horizon_days": 30,
            "entities": ["glucose", "runs", "meals"],
            "goal": "build_chart",
        },
    )
    assert resp.status_code == 200
    plan = resp.json()["data"]
    assert "glucose" in plan["recommended_entities"]
    assert any(e["path"] == "/v1/series/glucose" for e in plan["recommended_endpoints"])
    assert plan["recommended_resolution"] in ("5m", "15m")


@pytest.mark.asyncio
async def test_plan_meal_response(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/v1/plan/retrieve",
        headers=auth_headers,
        json={"intent": "render_meal_response", "horizon_days": 30, "goal": "build_chart"},
    )
    assert resp.status_code == 200
    paths = [e["path"] for e in resp.json()["data"]["recommended_endpoints"]]
    assert "/v1/events/meals" in paths
    assert any("meal_completed_at" in c for c in resp.json()["data"]["caveats"])


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
