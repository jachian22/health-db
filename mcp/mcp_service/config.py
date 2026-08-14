"""MCP service configuration. Required settings fail fast; secrets are never logged."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_service import __version__

_MCP_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _MCP_ROOT / ".env"

_LOCAL_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
)
_LOCAL_ORIGINS = (
    "http://127.0.0.1",
    "http://127.0.0.1:*",
    "http://localhost",
    "http://localhost:*",
)


def _with_port_wildcard(hosts: list[str]) -> list[str]:
    out: list[str] = []
    for host in hosts:
        out.append(host)
        if ":" not in host and not host.endswith(":*"):
            out.append(f"{host}:*")
    return list(dict.fromkeys(out))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_api_key: SecretStr = Field(alias="MCP_API_KEY", min_length=1)
    read_api_key: SecretStr = Field(alias="READ_API_KEY", min_length=1)
    query_api_base_url: AnyHttpUrl = Field(alias="QUERY_API_BASE_URL")

    mcp_service_name: str = Field(default="health-db", alias="MCP_SERVICE_NAME")
    mcp_service_version: str = Field(default=__version__, alias="MCP_SERVICE_VERSION")
    query_api_timeout_seconds: float = Field(default=20.0, alias="QUERY_API_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    mcp_allowed_hosts: str = Field(default="", alias="MCP_ALLOWED_HOSTS")
    mcp_allowed_origins: str = Field(default="", alias="MCP_ALLOWED_ORIGINS")

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

    @field_validator("query_api_timeout_seconds", mode="before")
    @classmethod
    def coerce_timeout(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return float(value.strip())
        return value

    @property
    def query_api_base_url_str(self) -> str:
        return str(self.query_api_base_url).rstrip("/")

    @property
    def _explicit_hosts(self) -> list[str]:
        extra = [item.strip() for item in self.mcp_allowed_hosts.split(",") if item.strip()]
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            extra.append(railway_domain)
        return extra

    @property
    def allowed_host_list(self) -> list[str]:
        extra = self._explicit_hosts
        if extra:
            return _with_port_wildcard(extra)
        return list(_LOCAL_HOSTS)

    @property
    def allowed_origin_list(self) -> list[str]:
        extra = [item.strip() for item in self.mcp_allowed_origins.split(",") if item.strip()]
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            extra.append(f"https://{railway_domain}")
            extra.append(f"https://{railway_domain}:*")
        if extra:
            return list(dict.fromkeys(extra))
        return list(_LOCAL_ORIGINS)


@lru_cache
def get_settings() -> Settings:
    return Settings()
