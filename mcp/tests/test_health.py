"""Liveness and readiness probes."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from mcp_service.main import create_app
from tests.conftest import TEST_READ_KEY, FakeQueryClient, assert_no_secrets, make_settings


def test_health_ok_without_calling_upstream(client: TestClient, fake_query_client: FakeQueryClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert fake_query_client.calls == []


def test_ready_ok_when_upstream_reachable(client: TestClient, fake_query_client: FakeQueryClient):
    fake_query_client.ready_ok = True
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "query_api": "reachable"}
    assert fake_query_client.calls == [("ready", {})]
    assert TEST_READ_KEY not in resp.text
    assert_no_secrets(resp.text)


def test_ready_503_when_upstream_fails(settings):
    fake = FakeQueryClient(ready_ok=False)
    app = create_app(settings=settings, query_client=fake)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert "request_id" in body
    assert TEST_READ_KEY not in resp.text
    assert_no_secrets(resp.text)


def test_ready_does_not_expose_secrets():
    settings = make_settings()
    fake = FakeQueryClient(ready_ok=False)
    app = create_app(settings=settings, query_client=fake)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/ready")
    assert TEST_READ_KEY not in resp.text
    assert "Bearer" not in resp.text
    assert_no_secrets(resp.text)


def test_missing_required_settings_fail_fast(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("READ_API_KEY", raising=False)
    monkeypatch.delenv("QUERY_API_BASE_URL", raising=False)
    from pydantic import ValidationError

    from mcp_service.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
