"""Settings fail-fast and secret handling."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
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
