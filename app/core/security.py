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


async def resolve_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    token = _extract_bearer(credentials)
    if token is None:
        raise AppError(
            code="UNAUTHORIZED",
            message="Invalid or missing credentials",
            hint="Send Authorization: Bearer <api-key>.",
            status_code=401,
        )
    if secrets.compare_digest(token, settings.ingest_api_key):
        role = AuthRole.INGEST
    elif secrets.compare_digest(token, settings.read_api_key):
        role = AuthRole.READ
    else:
        raise AppError(
            code="UNAUTHORIZED",
            message="Invalid or missing credentials",
            status_code=401,
        )
    ctx = AuthContext(role=role, external_user_id=settings.primary_user_external_id)
    request.state.auth_role = role.value
    request.state.external_user_id = ctx.external_user_id
    return ctx


def require_role(*allowed: AuthRole):
    async def _dependency(
        auth: Annotated[AuthContext, Depends(resolve_auth)],
    ) -> AuthContext:
        if auth.role not in allowed:
            raise AppError(
                code="FORBIDDEN",
                message=f"API key role '{auth.role}' cannot access this endpoint.",
                hint=f"Use a key with one of: {', '.join(r.value for r in allowed)}.",
                status_code=403,
            )
        return auth

    return _dependency


RequireIngest = Annotated[AuthContext, Depends(require_ingest_auth)]
RequireRead = Annotated[AuthContext, Depends(require_role(AuthRole.READ))]
