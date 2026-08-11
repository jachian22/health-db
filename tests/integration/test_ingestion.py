"""Integration tests for ingestion idempotency and rejections."""

from __future__ import annotations

import copy

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    GlucoseSample,
    HealthSource,
    IngestionBatch,
    MealEvent,
    SleepInterval,
    User,
    WeightMeasurement,
    Workout,
)


@pytest.mark.asyncio
async def test_ingest_missing_bearer_token(client: AsyncClient, ingest_body: dict):
    resp = await client.post("/v1/ingest/batch", json=ingest_body)
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "Invalid or missing ingestion credentials"
    assert "test-ingest-key" not in resp.text


@pytest.mark.asyncio
async def test_ingest_wrong_bearer_token(client: AsyncClient, ingest_body: dict):
    resp = await client.post(
        "/v1/ingest/batch",
        headers={"Authorization": "Bearer wrong-key"},
        json=ingest_body,
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert "wrong-key" not in resp.text


@pytest.mark.asyncio
async def test_ingest_read_key_rejected(client: AsyncClient, ingest_body: dict, read_headers: dict):
    resp = await client.post("/v1/ingest/batch", headers=read_headers, json=ingest_body)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_unsupported_schema_version(
    client: AsyncClient, ingest_headers: dict, ingest_body: dict
):
    body = copy.deepcopy(ingest_body)
    body["schema_version"] = 99
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert err["details"]["supported_versions"] == [1]


@pytest.mark.asyncio
async def test_data_end_before_start(
    client: AsyncClient, ingest_headers: dict, ingest_body: dict
):
    body = copy.deepcopy(ingest_body)
    body["data_start"] = "2026-08-10T20:00:00.000Z"
    body["data_end"] = "2026-07-30T00:00:00.000Z"
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_TIMESTAMP"


@pytest.mark.asyncio
async def test_missing_required_top_level_field(
    client: AsyncClient, ingest_headers: dict, ingest_body: dict
):
    body = copy.deepcopy(ingest_body)
    del body["schema_version"]
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


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
    assert body["summary"]["glucose_samples"]["inserted"] == 2
    assert body["summary"]["workouts"]["inserted"] == 1
    assert body["summary"]["sleep_sessions"]["inserted"] == 1
    assert body["summary"]["weight_measurements"]["inserted"] == 1
    assert body["summary"]["meal_events"]["inserted"] == 1
    assert body["rejections"] == []
    assert "raw_payload" not in body

    async with session_factory() as session:
        batches = (await session.execute(select(func.count()).select_from(IngestionBatch))).scalar()
        assert batches == 1
        batch = (await session.execute(select(IngestionBatch))).scalar_one()
        assert batch.raw_payload is not None
        assert "glucose_samples" in batch.raw_payload

        user = (
            await session.execute(
                select(User).where(User.external_identifier == "personal-primary")
            )
        ).scalar_one()
        assert user is not None

        sources = (
            await session.execute(select(func.count()).select_from(HealthSource))
        ).scalar()
        assert sources >= 1

        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2
        assert (await session.execute(select(func.count()).select_from(Workout))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(SleepInterval))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(WeightMeasurement))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(MealEvent))).scalar() == 1

        glucose = (await session.execute(select(GlucoseSample))).scalars().first()
        assert glucose.health_source_id is not None

    replay = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert replay.status_code == 200
    rbody = replay.json()
    assert rbody["status"] == "processed"
    assert rbody["summary"]["glucose_samples"]["unchanged"] == 2
    assert rbody["summary"]["glucose_samples"]["inserted"] == 0
    assert rbody["summary"]["workouts"]["unchanged"] == 1
    assert rbody["summary"]["sleep_sessions"]["unchanged"] == 1
    assert rbody["summary"]["weight_measurements"]["unchanged"] == 1
    assert rbody["summary"]["meal_events"]["unchanged"] == 1

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2
        assert (await session.execute(select(func.count()).select_from(IngestionBatch))).scalar() == 2
        sources_after = (
            await session.execute(select(func.count()).select_from(HealthSource))
        ).scalar()
        assert sources_after == sources


@pytest.mark.asyncio
async def test_ingest_update_preserves_ingested_at(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(GlucoseSample).where(
                    GlucoseSample.source_sample_id == "glucose-sample-0001"
                )
            )
        ).scalar_one()
        original_ingested_at = row.ingested_at
        original_updated_at = row.updated_at
        original_id = row.id

    modified = copy.deepcopy(ingest_body)
    modified["glucose_samples"][0]["value"] = 111
    modified["glucose_samples"][0]["metadata"] = {"source_app": "Stelo", "note": "corrected"}

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=modified)
    assert resp.status_code == 200
    results = resp.json()["summary"]["glucose_samples"]
    assert results["updated"] == 1
    assert results["unchanged"] == 1
    assert results["inserted"] == 0

    async with session_factory() as session:
        rows = (
            await session.execute(select(func.count()).select_from(GlucoseSample))
        ).scalar()
        assert rows == 2
        row = (
            await session.execute(
                select(GlucoseSample).where(
                    GlucoseSample.source_sample_id == "glucose-sample-0001"
                )
            )
        ).scalar_one()
        assert row.id == original_id
        assert float(row.value_mg_dl) == 111.0
        assert row.ingested_at == original_ingested_at
        assert row.updated_at > original_updated_at


@pytest.mark.asyncio
async def test_invalid_weight_unit_partial_success(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    body["weight_measurements"][0]["unit"] = "lb"

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["summary"]["weight_measurements"]["rejected"] == 1
    assert data["summary"]["glucose_samples"]["inserted"] == 2
    assert data["summary"]["meal_events"]["inserted"] == 1
    rejection = next(r for r in data["rejections"] if r["code"] == "INVALID_UNIT")
    assert rejection["entity_type"] == "weight_measurements"
    assert rejection["index"] == 0
    assert "lb" not in rejection.get("message", "").lower() or "kg" in rejection["message"]

    async with session_factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(WeightMeasurement))
        ).scalar() == 0
        assert (await session.execute(select(func.count()).select_from(MealEvent))).scalar() == 1


@pytest.mark.asyncio
async def test_non_strava_workout_rejected(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    body["workouts"][0]["source_name"] = "Apple Watch"

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["summary"]["workouts"]["rejected"] == 1
    assert data["summary"]["glucose_samples"]["inserted"] == 2
    assert any(r["code"] == "UNSUPPORTED_WORKOUT_SOURCE" for r in data["rejections"])

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(Workout))).scalar() == 0
        assert (await session.execute(select(func.count()).select_from(GlucoseSample))).scalar() == 2


@pytest.mark.asyncio
async def test_meal_completed_at_accepted(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
):
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert resp.status_code == 200
    assert resp.json()["summary"]["meal_events"]["inserted"] == 1


@pytest.mark.asyncio
async def test_meal_start_end_rejected(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    body["meal_events"][0]["meal_start"] = "2026-08-10T17:00:00.000Z"

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["summary"]["meal_events"]["rejected"] == 1
    assert any(r["code"] == "INVALID_REQUEST" for r in data["rejections"])

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(MealEvent))).scalar() == 0


@pytest.mark.asyncio
async def test_duplicate_identity_in_payload_applies_last(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
):
    body = copy.deepcopy(ingest_body)
    first = copy.deepcopy(body["glucose_samples"][0])
    first["value"] = 200
    body["glucose_samples"].append(first)

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=body)
    assert resp.status_code == 200

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
async def test_upsert_failure_returns_sanitized_ingestion_failed(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import ingestion as ingestion_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated upsert failure with secret DSN postgresql://x")

    monkeypatch.setattr(ingestion_module, "_upsert_entity", _boom)

    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INGESTION_FAILED"
    assert "simulated" not in resp.text
    assert "postgresql://" not in resp.text
    assert "DATABASE_URL" not in resp.text

    async with session_factory() as session:
        batch = (await session.execute(select(IngestionBatch))).scalar_one()
        assert batch.status == "failed"


@pytest.mark.asyncio
async def test_response_excludes_secrets(
    client: AsyncClient,
    ingest_headers: dict[str, str],
    ingest_body: dict,
):
    resp = await client.post("/v1/ingest/batch", headers=ingest_headers, json=ingest_body)
    assert resp.status_code == 200
    text = resp.text
    assert "test-ingest-key" not in text
    assert "DATABASE_URL" not in text
    assert "postgresql" not in text.lower()
