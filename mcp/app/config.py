"""MCP service configuration. Required settings fail fast; secrets are never logged."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # Optional Host allowlist for MCP DNS-rebinding protection (comma-separated).
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
    def allowed_host_list(self) -> list[str]:
        hosts = [
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
            "[::1]",
            "[::1]:*",
        ]
        extra = [item.strip() for item in self.mcp_allowed_hosts.split(",") if item.strip()]
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            extra.append(railway_domain)
        for host in extra:
            hosts.append(host)
            if ":" not in host and not host.endswith(":*"):
                hosts.append(f"{host}:*")
        return list(dict.fromkeys(hosts))

    @property
    def allowed_origin_list(self) -> list[str]:
        origins = [
            "http://127.0.0.1",
            "http://127.0.0.1:*",
            "http://localhost",
            "http://localhost:*",
        ]
        railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            origins.append(f"https://{railway_domain}")
            origins.append(f"https://{railway_domain}:*")
        extra = [item.strip() for item in self.mcp_allowed_origins.split(",") if item.strip()]
        origins.extend(extra)
        return list(dict.fromkeys(origins))


def validate_query_api_base_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@lru_cache
def get_settings() -> Settings:
    return Settings()
