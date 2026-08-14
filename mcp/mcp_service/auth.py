"""Static bearer-token authentication for MCP HTTP requests."""

from __future__ import annotations

import secrets

from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_service.logging import current_request_id, log_request

_PUBLIC_PATHS = frozenset({"/health", "/ready"})
_MCP_PATHS = frozenset({"/mcp", "/mcp/"})


def _unauthorized(request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing MCP credentials",
            },
            "request_id": request_id,
        },
        headers={"WWW-Authenticate": "Bearer", "X-Request-ID": request_id},
    )


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, remainder = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = remainder.strip()
    return token or None


def tokens_match(supplied: str, expected: SecretStr | str) -> bool:
    expected_value = expected.get_secret_value() if isinstance(expected, SecretStr) else expected
    try:
        return secrets.compare_digest(supplied, expected_value)
    except (TypeError, ValueError):
        return False


class McpAuthMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <MCP_API_KEY> on /mcp. Health probes are public."""

    def __init__(self, app, api_key: SecretStr | str) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in _PUBLIC_PATHS or path not in _MCP_PATHS:
            return await call_next(request)

        request_id = str(getattr(request.state, "request_id", None) or current_request_id())
        token = extract_bearer_token(request.headers.get("authorization"))
        if token is None or not tokens_match(token, self._api_key):
            log_request(
                request_id=request_id,
                category="mcp_auth",
                http_status=401,
                outcome="unauthorized",
                principal="unauthenticated",
                error_code="UNAUTHORIZED",
            )
            return _unauthorized(request_id)

        request.state.auth_principal = "mcp_caller"
        return await call_next(request)
