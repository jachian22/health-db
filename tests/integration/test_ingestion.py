"""Integration tests for ingestion idempotency and rejections."""

from __future__ import annotations

import copy

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    GlucoseSample,
    IngestionBatch,
    MealEvent,
    SleepInterval,
    WeightMeasurement,
    Workout,
)


@pytest.mark.asyncio
async def test_ingest_fixture_and_replay(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    first = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "processed"
    assert body["results"]["glucose_samples"]["inserted"] == 2
    assert body["results"]["workouts"]["inserted"] == 1
    assert body["results"]["sleep_sessions"]["inserted"] == 2
    assert body["results"]["weight_measurements"]["inserted"] == 1
    assert body["results"]["meal_events"]["inserted"] == 1

    async with session_factory() as session:
        batches = (await session.execute(select(func.count()).select_from(IngestionBatch))).scalar()
        assert batches == 1
        batch = (await session.execute(select(IngestionBatch))).scalar_one()
        assert batch.raw_payload is not None
        assert "glucose_samples" in batch.raw_payload
        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2
        assert (await session.execute(select(func.count()).select_from(Workout))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(SleepInterval))).scalar() == 2
        assert (await session.execute(select(func.count()).select_from(WeightMeasurement))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(MealEvent))).scalar() == 1

    replay = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert replay.status_code == 200
    rbody = replay.json()
    assert rbody["results"]["glucose_samples"]["unchanged"] == 2
    assert rbody["results"]["glucose_samples"]["inserted"] == 0
    assert rbody["results"]["workouts"]["unchanged"] == 1
    assert rbody["results"]["meal_events"]["unchanged"] == 1

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2
        assert (await session.execute(select(func.count()).select_from(IngestionBatch))).scalar() == 2


@pytest.mark.asyncio
async def test_ingest_update_and_insert(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
):
    await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)

    modified = copy.deepcopy(ingest_body)
    modified["payload"]["glucose_samples"][0]["value"] = 111
    modified["payload"]["glucose_samples"].append(
        {
            "source": "apple_health",
            "source_name": "Stelo",
            "source_sample_id": "new-glucose-999",
            "sample_time": "2026-08-05T15:00:00.000Z",
            "value": 105,
            "unit": "mg/dL",
            "metadata": {},
        }
    )

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=modified)
    assert resp.status_code == 200
    results = resp.json()["results"]["glucose_samples"]
    assert results["updated"] == 1
    assert results["inserted"] == 1
    assert results["unchanged"] == 1


@pytest.mark.asyncio
async def test_invalid_glucose_does_not_block_meals(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    body["payload"]["glucose_samples"].append(
        {
            "source": "apple_health",
            "source_sample_id": "bad-glucose",
            "sample_time": "2026-08-05T16:00:00.000Z",
            "value": 90,
            "unit": "mmol/L",
        }
    )

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["results"]["glucose_samples"]["rejected"] == 1
    assert data["results"]["meal_events"]["inserted"] == 1
    assert any(r["code"] == "INVALID_UNIT" for r in data["rejections"])

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(MealEvent))).scalar() == 1
        # only the two valid glucose rows
        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2


@pytest.mark.asyncio
async def test_duplicate_identity_in_payload_applies_last(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    first = copy.deepcopy(body["payload"]["glucose_samples"][0])
    first["value"] = 200
    body["payload"]["glucose_samples"].append(first)

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert any("duplicate" in w for w in data["warnings"])

    async with session_factory() as session:
        row = (
            await session.execute(
                select(GlucoseSample).where(
                    GlucoseSample.source_sample_id == first["source_sample_id"]
                )
            )
        ).scalar_one()
        assert float(row.value_mg_dl) == 200.0


@pytest.mark.asyncio
async def test_upsert_failure_marks_batch_failed(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import ingestion as ingestion_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated upsert failure")

    monkeypatch.setattr(ingestion_module, "_upsert_entity", _boom)

    # httpx's ASGI transport re-raises unhandled app exceptions rather than
    # returning the 500 JSON body, so assert on the propagated error here.
    with pytest.raises(RuntimeError, match="simulated upsert failure"):
        await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)

    async with session_factory() as session:
        batch = (await session.execute(select(IngestionBatch))).scalar_one()
        assert batch.status == "failed"


@pytest.mark.asyncio
async def test_ingest_requires_ingest_key(
    client: AsyncClient,
    read_headers: dict[str, str],
    ingest_body: dict,
):
    missing = await client.post("/v1/ingest/batch", json=ingest_body)
    assert missing.status_code == 401

    wrong_role = await client.post(
        "/v1/ingest/batch", headers=read_headers, json=ingest_body
    )
    assert wrong_role.status_code == 403
    assert wrong_role.json()["error"]["code"] == "FORBIDDEN"
