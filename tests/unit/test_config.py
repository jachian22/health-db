"""Unit tests for settings."""

from __future__ import annotations

from app.core.config import Settings


def test_identical_api_keys_do_not_block_boot():
    # Boot must succeed even if keys are misconfigured; auth can still enforce roles.
    settings = Settings(
        INGEST_API_KEY="same-key",
        READ_API_KEY="same-key",
        _env_file=None,
    )
    assert settings.ingest_api_key == settings.read_api_key


def test_distinct_api_keys_accepted():
    settings = Settings(
        INGEST_API_KEY="key-a",
        READ_API_KEY="key-b",
        _env_file=None,
    )
    assert settings.ingest_api_key == "key-a"
    assert settings.read_api_key == "key-b"


def test_postgres_url_normalization():
    settings = Settings(
        DATABASE_URL="postgres://user:pass@host:5432/db",
        INGEST_API_KEY="key-a",
        READ_API_KEY="key-b",
        _env_file=None,
    )
    assert settings.sync_database_url.startswith("postgresql://")
    assert settings.async_database_url.startswith("postgresql+asyncpg://")


def test_database_url_defaults_to_none():
    # Blank env values are dropped so the optional default (None) applies,
    # even if the process environment has a DATABASE_URL from other tests.
    settings = Settings(
        DATABASE_URL="",
        INGEST_API_KEY="key-a",
        READ_API_KEY="key-b",
        _env_file=None,
    )
    assert settings.database_url is None
