"""Unit tests for liveness and database readiness probes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.db.session import dispose_engine
from app.main import create_app


@pytest.fixture
async def probe_client(monkeypatch: pytest.MonkeyPatch):
    """App client with no database URL (and settings cache cleared)."""
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    await dispose_engine()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dispose_engine()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_ok_without_database_url(probe_client: AsyncClient):
    resp = await probe_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_unavailable_without_database_url(probe_client: AsyncClient):
    resp = await probe_client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {
        "detail": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "Database is unavailable",
        }
    }


@pytest.mark.asyncio
async def test_ready_failure_does_not_expose_connection_string(
    monkeypatch: pytest.MonkeyPatch,
):
    secret_url = "postgresql+asyncpg://secret_user:super_secret_password@db.example:5432/health"
    monkeypatch.setenv("DATABASE_URL", secret_url)
    get_settings.cache_clear()
    await dispose_engine()

    async def fail_ready() -> bool:
        return False

    monkeypatch.setattr("app.api.v1.health.check_database_ready", fail_ready)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ready")

    assert resp.status_code == 503
    payload = resp.text
    assert "super_secret_password" not in payload
    assert "secret_user" not in payload
    assert secret_url not in payload
    assert resp.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"

    await dispose_engine()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ready_success_when_database_check_passes(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/health_db",
    )
    get_settings.cache_clear()
    await dispose_engine()

    async def ok_ready() -> bool:
        return True

    monkeypatch.setattr("app.api.v1.health.check_database_ready", ok_ready)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ready")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "database": "connected"}

    await dispose_engine()
    get_settings.cache_clear()


def test_database_url_optional_at_settings_load():
    settings = Settings(DATABASE_URL="", _env_file=None)
    assert settings.database_url is None


def test_missing_database_url_raises_on_url_helpers():
    settings = Settings(DATABASE_URL="", _env_file=None)
    assert settings.database_url is None
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _ = settings.async_database_url
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _ = settings.sync_database_url
