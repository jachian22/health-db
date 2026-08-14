"""Request-scoped IDs and privacy-safe structured logging.

Logs metadata only. Never credentials, Authorization headers, glucose values,
meal foods/notes, source sample IDs, raw payloads, or stack traces.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

request_id_ctx: ContextVar[str] = ContextVar("mcp_request_id", default="unknown")

logger = logging.getLogger("mcp_service")


def new_request_id() -> str:
    return str(uuid.uuid4())


def current_request_id() -> str:
    return request_id_ctx.get()


def configure_logging(level: str = "INFO") -> None:
    resolved = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        root.addHandler(handler)
    root.setLevel(resolved)
    logger.setLevel(resolved)


def parse_or_create_request_id(raw: str | None) -> str:
    if raw:
        try:
            return str(uuid.UUID(raw.strip()))
        except ValueError:
            pass
    return new_request_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = parse_or_create_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def log_request(
    *,
    request_id: str,
    category: str,
    tool_name: str | None = None,
    http_status: int | None = None,
    outcome: str | None = None,
    principal: str = "anonymous",
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
    resolution: str | None = None,
    bucket: str | None = None,
    record_count: int | None = None,
    truncated: bool | None = None,
    latency_ms: float | None = None,
    error_code: str | None = None,
    anchor: str | None = None,
    lookback_days: int | None = None,
    meal_lookback_days: int | None = None,
    sleep_lookback_hours: int | None = None,
    glucose_lookback_hours: int | None = None,
) -> None:
    parts = [
        f"request_id={request_id}",
        f"category={category}",
        f"tool={tool_name}",
        f"principal={principal}",
    ]
    if http_status is not None:
        parts.append(f"http_status={http_status}")
    if outcome is not None:
        parts.append(f"outcome={outcome}")
    parts.extend(
        [
            f"start={start}",
            f"end={end}",
            f"timezone={timezone}",
            f"resolution={resolution}",
            f"bucket={bucket}",
            f"record_count={record_count}",
            f"truncated={truncated}",
            f"latency_ms={f'{latency_ms:.1f}' if latency_ms is not None else None}",
            f"error_code={error_code}",
        ]
    )
    extras: list[str] = []
    if anchor is not None:
        extras.append(f"anchor={anchor}")
    if lookback_days is not None:
        extras.append(f"lookback_days={lookback_days}")
    if meal_lookback_days is not None:
        extras.append(f"meal_lookback_days={meal_lookback_days}")
    if sleep_lookback_hours is not None:
        extras.append(f"sleep_lookback_hours={sleep_lookback_hours}")
    if glucose_lookback_hours is not None:
        extras.append(f"glucose_lookback_hours={glucose_lookback_hours}")
    if extras:
        parts.extend(extras)
    logger.info("mcp_access %s", " ".join(parts))
