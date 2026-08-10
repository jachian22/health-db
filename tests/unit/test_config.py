"""Unit tests for settings validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_identical_api_keys_rejected():
    with pytest.raises(ValidationError, match="must be different"):
        Settings(
            INGEST_API_KEY="same-key",
            READ_API_KEY="same-key",
            _env_file=None,
        )


def test_distinct_api_keys_accepted():
    settings = Settings(
        INGEST_API_KEY="key-a",
        READ_API_KEY="key-b",
        _env_file=None,
    )
    assert settings.ingest_api_key == "key-a"
    assert settings.read_api_key == "key-b"
