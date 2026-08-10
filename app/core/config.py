"""Application configuration via environment variables."""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/health_db",
        alias="DATABASE_URL",
    )
    ingest_api_key: str = Field(default="dev-ingest-key-change-me", alias="INGEST_API_KEY")
    read_api_key: str = Field(default="dev-read-key-change-me", alias="READ_API_KEY")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # Query bounds
    default_lookback_days: int = 30
    max_lookback_days: int = 365
    default_row_cap: int = 5000
    hard_row_cap: int = 20_000

    # Phase 1 single principal
    primary_user_external_id: str = "personal-primary"

    @model_validator(mode="after")
    def keys_must_differ(self) -> "Settings":
        if self.ingest_api_key == self.read_api_key:
            raise ValueError(
                "INGEST_API_KEY and READ_API_KEY must be different: "
                "role separation cannot work with a shared key."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic (psycopg2)."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql://", 1)
        return url

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
