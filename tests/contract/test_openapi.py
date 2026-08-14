"""OpenAPI contract checks for Query API v1 and ingest."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_openapi_documents_query_api_v1(client: AsyncClient):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()

    paths = spec["paths"]
    assert "/v1/ingest/batch" in paths
    assert "/v1/query/coverage" in paths
    assert "/v1/query/glucose/series" in paths
    assert "/v1/query/glucose/summary" in paths
    assert "/v1/query/meals" in paths
    assert "/v1/query/workouts" in paths
    assert "/v1/query/sleep-intervals" in paths
    assert "/v1/query/weight-measurements" in paths
    assert "/v1/query/last-logged-meal" in paths
    assert "/v1/query/context-snapshot" in paths
    assert "/v1/query/personal-timeline" in paths
    assert "/health" in paths
    assert "/ready" in paths

    # Removed Phase-1 POST series/events surfaces
    for banned in (
        "/v1/query/series/glucose",
        "/v1/query/series/runs",
        "/v1/query/series/sleep",
        "/v1/query/series/weight",
        "/v1/query/events/meals",
        "/v1/summary",
        "/v1/plan",
        "/v1/compare",
    ):
        assert banned not in paths

    coverage = paths["/v1/query/coverage"]["get"]
    assert "get" in paths["/v1/query/coverage"]
    assert "post" not in paths["/v1/query/coverage"]
    assert "read-only" in coverage["description"].lower()
    assert "bounded" in coverage["description"].lower()
    assert "overlap" in coverage["description"].lower()
    assert "start_time < end" in coverage["description"]
    assert "may differ" not in coverage["description"].lower()

    glucose = paths["/v1/query/glucose/series"]["get"]
    assert "7 days" in glucose["description"]
    assert "10000" in glucose["description"]
    assert "mg/dL" in glucose["description"] or "mg/dl" in glucose["description"].lower()

    meals = paths["/v1/query/meals"]["get"]
    assert "notes" in meals["description"].lower()
    assert "hmac" in meals["description"].lower()

    workouts = paths["/v1/query/workouts"]["get"]
    assert "post" not in paths["/v1/query/workouts"]
    assert "365" in workouts["description"]
    assert "overlap" in workouts["description"].lower()
    assert "may differ" not in workouts["description"].lower()

    sleep = paths["/v1/query/sleep-intervals"]["get"]
    assert "90" in sleep["description"]
    assert "session" in sleep["description"].lower() or "raw" in sleep["description"].lower()

    weight = paths["/v1/query/weight-measurements"]["get"]
    assert "365" in weight["description"]
    assert "kg" in weight["description"].lower()

    last_meal = paths["/v1/query/last-logged-meal"]["get"]
    assert "post" not in paths["/v1/query/last-logged-meal"]
    last_meal_desc = last_meal["description"].lower()
    assert "read-only" in last_meal_desc
    assert "latest logged meal" in last_meal_desc
    assert "foods" in last_meal_desc
    assert "notes" in last_meal_desc
    assert "fasting" in last_meal_desc
    assert "medical advice" in last_meal_desc or "interpretation" in last_meal_desc

    snapshot = paths["/v1/query/context-snapshot"]["get"]
    assert "post" not in paths["/v1/query/context-snapshot"]
    snapshot_desc = snapshot["description"].lower()
    assert "read-only" in snapshot_desc
    assert "latest logged meal" in snapshot_desc
    assert "foods" in snapshot_desc
    assert "notes" in snapshot_desc
    assert "fasting" in snapshot_desc
    assert "raw" in snapshot_desc and "sleep" in snapshot_desc
    assert "glucose series" in snapshot_desc
    assert "medical advice" in snapshot_desc or "interpretation" in snapshot_desc

    timeline = paths["/v1/query/personal-timeline"]["get"]
    assert "get" in paths["/v1/query/personal-timeline"]
    assert "post" not in paths["/v1/query/personal-timeline"]
    timeline_desc = timeline["description"].lower()
    assert "read-only" in timeline_desc
    assert "72" in timeline["description"]
    assert "[start, end)" in timeline["description"]
    assert "15-minute" in timeline_desc or "15 minute" in timeline_desc
    assert "foods" in timeline_desc
    assert "notes" in timeline_desc
    assert "raw" in timeline_desc and "sleep" in timeline_desc
    assert "session" in timeline_desc
    assert "medical advice" in timeline_desc or "interpretation" in timeline_desc
    param_names = {param["name"] for param in timeline.get("parameters", [])}
    assert param_names == {"start", "end", "timezone"}
    assert "resolution" not in param_names
    assert "limit" not in param_names
    assert "cursor" not in param_names
    assert "bucket" not in param_names

    # Error schema exists
    schemas = spec["components"]["schemas"]
    assert "ErrorResponse" in schemas or "ErrorBody" in schemas

    desc = spec["info"]["description"].lower()
    assert "read-only" in desc
    assert "america/new_york" in desc
    assert "[start, end)" in spec["info"]["description"] or "half-open" in desc
    assert "0.2.0" in spec["info"]["version"] or "breaking" in desc


@pytest.mark.asyncio
async def test_docs_disabled_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        assert (await ac.get("/openapi.json")).status_code == 404
        assert (await ac.get("/docs")).status_code == 404
        root = await ac.get("/")
        assert root.status_code == 200
        assert "docs" not in root.json()
    get_settings.cache_clear()
