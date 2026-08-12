"""Statelessness and isolation guarantees."""

from __future__ import annotations

import inspect

import pytest
from mcp import Client
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeQueryClient, make_settings


def _initialize_payload(req_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "health-db-tests", "version": "0.0.1"},
        },
    }


def test_two_independent_requests_need_no_session_id(
    client: TestClient, mcp_headers: dict[str, str]
):
    headers = {
        **mcp_headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    first = client.post("/mcp", headers=headers, json=_initialize_payload(1))
    second = client.post("/mcp", headers=headers, json=_initialize_payload(2))
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers.get("mcp-session-id") in {None, ""}
    assert second.headers.get("mcp-session-id") in {None, ""}


def test_no_custom_session_state_is_persisted(app):
    mcp = app.state.mcp
    manager = getattr(mcp, "session_manager", None)
    assert manager is not None
    # Stateless HTTP: no event store and no required session tracking.
    assert getattr(manager, "event_store", None) is None


@pytest.mark.asyncio
async def test_tool_results_are_not_shared_across_requests(
    settings: Settings, mcp_headers: dict[str, str]
):
    fake_a = FakeQueryClient()
    fake_b = FakeQueryClient()
    app_a = create_app(settings=settings, query_client=fake_a)
    app_b = create_app(settings=settings, query_client=fake_b)

    async with Client(app_a.state.mcp) as c1:
        result_a = await c1.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
            },
        )
    async with Client(app_b.state.mcp) as c2:
        result_b = await c2.call_tool(
            "get_data_coverage",
            {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-12T00:00:00Z",
            },
        )
    assert result_a.is_error is False
    assert result_b.is_error is False
    assert fake_a.calls and fake_b.calls
    assert fake_a is not fake_b


def test_fresh_app_instances_handle_sequential_requests(mcp_headers: dict[str, str]):
    for _ in range(2):
        settings = make_settings()
        fake = FakeQueryClient()
        app = create_app(settings=settings, query_client=fake)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            health = client.get("/health")
            assert health.status_code == 200
            mcp = client.post(
                "/mcp",
                headers={
                    **mcp_headers,
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json=_initialize_payload(1),
            )
            assert mcp.status_code == 200


def test_settings_have_no_database_configuration():
    settings = make_settings()
    field_names = set(Settings.model_fields)
    assert "database_url" not in field_names
    assert "DATABASE_URL" not in field_names
    assert not hasattr(settings, "database_url")
    assert "ingest_api_key" not in field_names


def test_mcp_package_does_not_import_sqlalchemy_or_backend_db():
    import app.config
    import app.main
    import app.query_api_client
    import app.tools

    for module in (app.config, app.main, app.query_api_client, app.tools):
        source = inspect.getsource(module)
        assert "sqlalchemy" not in source
        assert "app.db" not in source
        assert "create_engine" not in source
        assert "DATABASE_URL" not in source
