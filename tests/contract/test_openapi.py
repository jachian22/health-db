"""OpenAPI contract checks for agent-consumable schema documentation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_openapi_documents_phase1_contract(client: AsyncClient):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()

    paths = spec["paths"]
    assert "/v1/ingest/batch" in paths
    assert "/v1/query/series/glucose" in paths
    assert "/v1/query/series/runs" in paths
    assert "/v1/query/series/sleep" in paths
    assert "/v1/query/series/weight" in paths
    assert "/v1/query/events/meals" in paths
    assert "/health" in paths
    assert "/ready" in paths

    # No Phase 2 surfaces
    for banned in ("/v1/summary", "/v1/plan", "/v1/compare"):
        assert not any(banned in p for p in paths)

    glucose = paths["/v1/query/series/glucose"]["post"]
    assert "half-open" in glucose["description"].lower() or "[start, end)" in glucose["description"]
    assert "mg/dL" in glucose["description"]

    # Resolution enum present on GlucoseSeriesQuery
    schemas = spec["components"]["schemas"]
    glucose_query = schemas["GlucoseSeriesQuery"]
    resolution = glucose_query["properties"]["resolution"]
    assert set(resolution.get("enum", [])) == {"raw", "5m", "15m", "1h", "1d"} or (
        "raw" in str(resolution)
    )

    # Error schema exists
    assert "ErrorResponse" in schemas or "ErrorBody" in schemas

    # Range semantics mentioned in app description
    assert "365" in spec["info"]["description"]
    assert "[start, end)" in spec["info"]["description"] or "half-open" in spec["info"]["description"].lower()

    weight = paths["/v1/query/series/weight"]["post"]
    assert "kilogram" in weight["description"].lower()
