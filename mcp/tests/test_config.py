"""Settings fail-fast and secret handling."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_service.config import Settings
from tests.conftest import TEST_MCP_KEY, TEST_READ_KEY, make_settings


def test_settings_repr_does_not_include_secrets():
    settings = make_settings()
    dumped = repr(settings)
    assert TEST_MCP_KEY not in dumped
    assert TEST_READ_KEY not in dumped
    assert "**********" in dumped or "SecretStr" in dumped


def test_blank_required_settings_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEY", "")
    monkeypatch.setenv("READ_API_KEY", "")
    monkeypatch.setenv("QUERY_API_BASE_URL", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_query_api_base_url_fails():
    with pytest.raises(ValidationError):
        Settings(
            MCP_API_KEY="x",
            READ_API_KEY="y",
            QUERY_API_BASE_URL="not-a-url",
            _env_file=None,
        )


def test_local_host_allowlist_includes_localhost():
    settings = make_settings()
    assert "localhost" in settings.allowed_host_list
    assert "127.0.0.1" in settings.allowed_host_list
    assert "http://localhost" in settings.allowed_origin_list


def test_railway_host_allowlist_excludes_localhost(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "mcp.up.railway.app")
    settings = make_settings()
    hosts = settings.allowed_host_list
    assert "mcp.up.railway.app" in hosts
    assert "mcp.up.railway.app:*" in hosts
    assert "localhost" not in hosts
    assert "127.0.0.1" not in hosts
    origins = settings.allowed_origin_list
    assert "https://mcp.up.railway.app" in origins
    assert "http://localhost" not in origins


def test_env_file_is_mcp_directory_not_cwd():
    from mcp_service.config import _ENV_FILE, _MCP_ROOT

    assert _MCP_ROOT.name == "mcp"
    assert _ENV_FILE == _MCP_ROOT / ".env"
