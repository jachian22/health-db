"""ML-layer configuration. Credentials are never hardcoded or logged."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ML_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ML_ROOT.parent

DEFAULT_QUERY_TIMEZONE = "America/New_York"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_PAGE_LIMIT = 500
MAX_RETRIES = 3

# Query API hard limits. The client windows requests; it does not change the API.
RAW_GLUCOSE_MAX_DAYS = 7
MAX_GLUCOSE_POINTS = 10_000
MAX_WORKOUT_RANGE_DAYS = 365
MAX_SLEEP_RANGE_DAYS = 90
MAX_WEIGHT_RANGE_DAYS = 365

GLUCOSE_SANITY_MIN_MG_DL = 20.0
GLUCOSE_SANITY_MAX_MG_DL = 600.0

QUERY_API_CONTRACT = "health-db-query-api-v1"
RANGE_SEMANTICS = "[start, end)"


def env_files() -> tuple[Path, ...]:
    """Resolve dotenv paths at call time, not at import."""
    return tuple(p for p in (_ML_ROOT / ".env", _REPO_ROOT / ".env") if p.is_file())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    health_api_url: AnyHttpUrl = Field(
        validation_alias=AliasChoices("HEALTH_API_URL", "QUERY_API_BASE_URL"),
    )
    health_api_read_key: SecretStr = Field(
        validation_alias=AliasChoices("HEALTH_API_READ_KEY", "READ_API_KEY"),
        min_length=1,
    )
    health_api_timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        validation_alias=AliasChoices("HEALTH_API_TIMEOUT_SECONDS", "QUERY_API_TIMEOUT_SECONDS"),
    )
    timezone: str = Field(default=DEFAULT_QUERY_TIMEZONE, alias="HEALTH_ML_TIMEZONE")

    def __init__(self, **kwargs: Any) -> None:
        if "_env_file" not in kwargs:
            files = env_files()
            kwargs["_env_file"] = files or None
        super().__init__(**kwargs)

    @model_validator(mode="before")
    @classmethod
    def drop_blank_env_values(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and value.strip() == "")
            }
        return data

    @field_validator("health_api_timeout_seconds", mode="before")
    @classmethod
    def coerce_timeout(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return float(value.strip())
        return value

    @property
    def health_api_url_str(self) -> str:
        return str(self.health_api_url).rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_files() or None)
