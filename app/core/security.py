"""API-key authentication with ingest/read roles."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import AppError

_bearer = HTTPBearer(auto_error=False)


class AuthRole(StrEnum):
    INGEST = "ingest"
    READ = "read"


@dataclass(frozen=True, slots=True)
class AuthContext:
    role: AuthRole
    external_user_id: str


def _extract_bearer(
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        return None
    token = credentials.credentials.strip()
    return token or None


async def require_ingest_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Authenticate POST /v1/ingest/batch with INGEST_API_KEY only."""
    token = _extract_bearer(credentials)
    if token is None or not secrets.compare_digest(token, settings.ingest_api_key):
        raise AppError(
            code="UNAUTHORIZED",
            message="Invalid or missing ingestion credentials",
            status_code=401,
        )
    ctx = AuthContext(
        role=AuthRole.INGEST,
        external_user_id=settings.primary_user_external_id,
    )
    request.state.auth_role = ctx.role.value
    request.state.external_user_id = ctx.external_user_id
    return ctx


async def require_read_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Authenticate GET /v1/query/* with READ_API_KEY only.

    INGEST_API_KEY is intentionally rejected with 401 (not 403) so the Query API
    never treats the ingest credential as a valid-but-wrong-role principal.
    """
    token = _extract_bearer(credentials)
    if token is None or not secrets.compare_digest(token, settings.read_api_key):
        raise AppError(
            code="UNAUTHORIZED",
            message="Invalid or missing read credentials",
            status_code=401,
        )
    ctx = AuthContext(
        role=AuthRole.READ,
        external_user_id=settings.primary_user_external_id,
    )
    request.state.auth_role = ctx.role.value
    request.state.external_user_id = ctx.external_user_id
    return ctx


RequireRead = Annotated[AuthContext, Depends(require_read_auth)]
