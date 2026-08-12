"""OpenAPI contract checks for Query API v1 and ingest."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


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

    glucose = paths["/v1/query/glucose/series"]["get"]
    assert "7 days" in glucose["description"]
    assert "mg/dL" in glucose["description"] or "mg/dl" in glucose["description"].lower()

    meals = paths["/v1/query/meals"]["get"]
    assert "notes" in meals["description"].lower()

    # Error schema exists
    schemas = spec["components"]["schemas"]
    assert "ErrorResponse" in schemas or "ErrorBody" in schemas

    desc = spec["info"]["description"].lower()
    assert "read-only" in desc
    assert "america/new_york" in desc
    assert "[start, end)" in spec["info"]["description"] or "half-open" in desc
