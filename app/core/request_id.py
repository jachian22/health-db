"""Request ID generation and attachment."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> uuid.UUID:
    return uuid.uuid4()


def parse_or_create_request_id(raw: str | None) -> uuid.UUID:
    if raw:
        try:
            return uuid.UUID(raw.strip())
        except ValueError:
            pass
    return new_request_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = parse_or_create_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = str(request_id)
        return response
