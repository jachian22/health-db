"""Application configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional at import/boot so /health works before Postgres is wired.
    # Never log the full value — it may contain credentials.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    ingest_api_key: str = Field(default="dev-ingest-key-change-me", alias="INGEST_API_KEY")
    read_api_key: str = Field(default="dev-read-key-change-me", alias="READ_API_KEY")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # Query read path: Postgres statement_timeout (milliseconds).
    query_statement_timeout_ms: int = Field(default=10_000, alias="QUERY_STATEMENT_TIMEOUT_MS")

    # Interactive OpenAPI docs. None → enabled outside production.
    enable_api_docs: bool | None = Field(default=None, alias="ENABLE_API_DOCS")

    # Phase 1 single principal
    primary_user_external_id: str = "personal-primary"

    @model_validator(mode="before")
    @classmethod
    def drop_blank_env_values(cls, data: Any) -> Any:
        # Railway dashboard blanks often show up as "". Drop them so defaults apply.
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and value.strip() == "")
            }
        return data

    @field_validator("db_echo", mode="before")
    @classmethod
    def coerce_db_echo(cls, value: object) -> object:
        if value is None:
            return False
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return value

    @field_validator("enable_api_docs", mode="before")
    @classmethod
    def coerce_enable_api_docs(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def api_docs_enabled(self) -> bool:
        if self.enable_api_docs is not None:
            return self.enable_api_docs
        return self.environment.lower() not in {"production", "prod"}

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic (psycopg2)."""
        url = self.database_url
        if not url:
            raise ValueError("DATABASE_URL is not configured")
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql://", 1)
        return url

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if not url:
            raise ValueError("DATABASE_URL is not configured")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
