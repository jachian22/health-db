"""Integration tests for authenticated query endpoints."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient


async def _seed(client: AsyncClient, ingest_headers: dict, ingest_body: dict) -> None:
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_query_auth(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    await _seed(client, ingest_headers, ingest_body)
    body = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-08T00:00:00Z",
    }
    assert (await client.post("/v1/query/series/glucose", json=body)).status_code == 401
    forbidden = await client.post(
        "/v1/query/series/glucose", headers=ingest_headers, json=body
    )
    assert forbidden.status_code == 403
    ok = await client.post("/v1/query/series/glucose", headers=read_headers, json=body)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_half_open_range_and_empty(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    await _seed(client, ingest_headers, ingest_body)

    # Fixture glucose at 2026-08-05T14:15:00Z and another sample
    at_start = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-05T14:15:00.000Z",
            "end": "2026-08-06T00:00:00Z",
            "resolution": "raw",
        },
    )
    assert at_start.status_code == 200
    # The record at exactly `start` must be included (half-open [start, end)).
    start_ids = [r["source_sample_id"] for r in at_start.json()["data"]]
    assert "aaaaaaaa-1111-2222-3333-444444444401" in start_ids

    at_end_excluded = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-05T14:15:00.000Z",
            "resolution": "raw",
        },
    )
    assert at_end_excluded.status_code == 200
    ids = [r["source_sample_id"] for r in at_end_excluded.json()["data"]]
    assert "aaaaaaaa-1111-2222-3333-444444444401" not in ids

    empty = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-02T00:00:00Z",
        },
    )
    assert empty.status_code == 200
    assert empty.json()["data"] == []
    assert empty.json()["meta"]["row_count"] == 0
    assert empty.json()["meta"]["actual_first_record_at"] is None


@pytest.mark.asyncio
async def test_invalid_and_wide_range(
    client: AsyncClient,
    read_headers: dict,
):
    bad = await client.post(
        "/v1/query/series/weight",
        headers=read_headers,
        json={"start": "2026-08-08T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_RANGE"

    wide = await client.post(
        "/v1/query/series/weight",
        headers=read_headers,
        json={"start": "2024-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert wide.status_code == 400
    assert wide.json()["error"]["code"] == "RANGE_TOO_WIDE"


@pytest.mark.asyncio
async def test_glucose_raw_and_aggregate(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    await _seed(client, ingest_headers, ingest_body)
    raw = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "resolution": "raw",
        },
    )
    assert raw.status_code == 200
    times = [row["timestamp"] for row in raw.json()["data"]]
    assert times == sorted(times)

    agg = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "resolution": "1d",
        },
    )
    assert agg.status_code == 200
    for bucket in agg.json()["data"]:
        assert "count" in bucket
        assert "min_mg_dl" in bucket
        assert "max_mg_dl" in bucket
        assert "avg_mg_dl" in bucket
    # No interpolation: only days with samples appear
    assert len(agg.json()["data"]) <= 2


@pytest.mark.asyncio
async def test_runs_sleep_overlap_and_meals_weight(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    await _seed(client, ingest_headers, ingest_body)

    # Workout 06:00–06:32 on 2026-08-05 — overlaps window that starts mid-run
    runs = await client.post(
        "/v1/query/series/runs",
        headers=read_headers,
        json={
            "start": "2026-08-05T06:15:00Z",
            "end": "2026-08-05T07:00:00Z",
        },
    )
    assert runs.status_code == 200
    assert runs.json()["meta"]["row_count"] == 1
    assert runs.json()["data"][0]["duration_seconds"] == 32 * 60

    # Sleep 23:10–00:30 crosses midnight — overlap with next-day window
    sleep = await client.post(
        "/v1/query/series/sleep",
        headers=read_headers,
        json={
            "start": "2026-08-06T00:00:00Z",
            "end": "2026-08-06T01:00:00Z",
        },
    )
    assert sleep.status_code == 200
    assert sleep.json()["meta"]["row_count"] >= 1

    weight = await client.post(
        "/v1/query/series/weight",
        headers=read_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-08T00:00:00Z"},
    )
    assert weight.status_code == 200
    assert weight.json()["data"][0]["value_kg"] == 72.5

    meals = await client.post(
        "/v1/query/events/meals",
        headers=read_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-08T00:00:00Z"},
    )
    assert meals.status_code == 200
    assert meals.json()["meta"]["row_count"] == 1
    assert "rice" in meals.json()["data"][0]["foods"]


@pytest.mark.asyncio
async def test_request_audit_deferred_without_table(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    """Phase 1 omits request_audit_logs; queries must still succeed."""
    await _seed(client, ingest_headers, ingest_body)
    resp = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
            "resolution": "raw",
        },
    )
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers
    assert resp.json()["meta"]["row_count"] == 2


@pytest.mark.asyncio
async def test_too_many_rows(
    client: AsyncClient,
    ingest_headers: dict,
    read_headers: dict,
    ingest_body: dict,
):
    body = copy.deepcopy(ingest_body)
    # Create many glucose points
    samples = []
    start = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(10):
        samples.append(
            {
                "source": "apple_health",
                "source_name": "Stelo",
                "source_sample_id": f"g-many-{i}",
                "sample_time": (start + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
                "value": 90 + i,
                "unit": "mg/dL",
                "metadata": {},
            }
        )
    body["payload"]["glucose_samples"] = samples
    await _seed(client, ingest_headers, body)

    resp = await client.post(
        "/v1/query/series/glucose",
        headers=read_headers,
        json={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-02T00:00:00Z",
            "resolution": "raw",
            "limit": 5,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOO_MANY_ROWS"
