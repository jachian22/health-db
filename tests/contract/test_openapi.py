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
