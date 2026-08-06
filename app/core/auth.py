"""API key authentication for Phase 1."""

from __future__ import annotations

import secrets

from fastapi import Header, Request

from app.core.config import get_settings
from app.core.errors import UnauthorizedError


async def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """Validate bearer token or X-API-Key header."""
    settings = get_settings()
    provided: str | None = None

    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()
        else:
            provided = authorization.strip()

    if not provided and x_api_key:
        provided = x_api_key.strip()

    expected = settings.api_key
    if not provided or not expected or not secrets.compare_digest(provided, expected):
        request.state.auth_failed = True
        raise UnauthorizedError()

    return provided
