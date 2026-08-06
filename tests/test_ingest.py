"""Ingestion endpoint tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


SAMPLE_BATCH = {
    "user_id": "user_1",
    "glucose_samples": [
        {
            "source_name": "stelo",
            "source_sample_id": "g1",
            "sample_time": "2026-08-01T10:00:00Z",
            "value": 110.0,
            "unit": "mg/dL",
            "trend": "flat",
        }
    ],
    "workouts": [
        {
            "source_name": "apple_health",
            "source_sample_id": "w1",
            "start_time": "2026-08-01T06:00:00Z",
            "end_time": "2026-08-01T07:00:00Z",
            "sport": "running",
            "distance_m": 8000,
            "active_energy_kcal": 520,
            "avg_hr": 145,
            "max_hr": 168,
        }
    ],
    "sleep_sessions": [
        {
            "source_name": "apple_health",
            "source_sample_id": "s1",
            "start_time": "2026-07-31T23:00:00Z",
            "end_time": "2026-08-01T06:30:00Z",
            "duration_s": 27000,
        }
    ],
    "weight_measurements": [
        {
            "source_name": "apple_health",
            "source_sample_id": "wt1",
            "measured_at": "2026-08-01T07:30:00Z",
            "value": 75.2,
            "unit": "kg",
        }
    ],
    "meal_events": [
        {
            "source_name": "manual",
            "source_sample_id": "m1",
            "meal_start": "2026-08-01T12:00:00Z",
            "meal_end": "2026-08-01T12:30:00Z",
            "meal_completed_at": "2026-08-01T12:30:00Z",
            "notes": "lunch",
            "foods": [{"name": "salad"}],
        }
    ],
    "sync_state": [
        {
            "entity_type": "glucose",
            "source_name": "stelo",
            "anchor": "cursor-abc",
            "last_synced_at": "2026-08-01T12:00:00Z",
        }
    ],
}


@pytest.mark.asyncio
async def test_ingest_batch(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/v1/ingest/batch", json=SAMPLE_BATCH, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["glucose_samples"]["inserted"] == 1
    assert body["data"]["workouts"]["inserted"] == 1
    assert body["data"]["meal_events"]["inserted"] == 1


@pytest.mark.asyncio
async def test_ingest_idempotent_upsert(client: AsyncClient, auth_headers: dict):
    await client.post("/v1/ingest/batch", json=SAMPLE_BATCH, headers=auth_headers)
    updated = {
        **SAMPLE_BATCH,
        "glucose_samples": [
            {
                **SAMPLE_BATCH["glucose_samples"][0],
                "value": 120.0,
            }
        ],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
        "sync_state": [],
    }
    resp = await client.post("/v1/ingest/batch", json=updated, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["glucose_samples"]["updated"] == 1
    assert resp.json()["data"]["glucose_samples"]["inserted"] == 0

    series = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert series.status_code == 200
    assert series.json()["data"][0]["v"] == 120.0


@pytest.mark.asyncio
async def test_ingest_rejects_bad_unit(client: AsyncClient, auth_headers: dict):
    bad = {
        "user_id": "user_1",
        "glucose_samples": [
            {
                "source_name": "stelo",
                "source_sample_id": "g-bad",
                "sample_time": "2026-08-01T10:00:00Z",
                "value": 5.5,
                "unit": "stones",
            }
        ],
    }
    resp = await client.post("/v1/ingest/batch", json=bad, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_tombstone(client: AsyncClient, auth_headers: dict):
    await client.post("/v1/ingest/batch", json=SAMPLE_BATCH, headers=auth_headers)
    tomb = {
        "user_id": "user_1",
        "glucose_samples": [
            {
                **SAMPLE_BATCH["glucose_samples"][0],
                "deleted_at": datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc).isoformat(),
            }
        ],
    }
    resp = await client.post("/v1/ingest/batch", json=tomb, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["glucose_samples"]["tombstoned"] == 1

    series = await client.post(
        "/v1/series/glucose",
        headers=auth_headers,
        json={"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z", "user_id": "user_1"},
    )
    assert series.json()["meta"]["count"] == 0
