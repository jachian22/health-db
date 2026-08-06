"""Request logging helpers."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


logger = logging.getLogger("health_db")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        auth_failed = getattr(request.state, "auth_failed", False)
        row_count = getattr(request.state, "row_count", None)
        user_id = getattr(request.state, "user_id", None)
        range_start = getattr(request.state, "range_start", None)
        range_end = getattr(request.state, "range_end", None)
        bounded = getattr(request.state, "bounded", None)

        logger.info(
            "request_id=%s method=%s path=%s status=%s latency_ms=%s auth_failed=%s "
            "user_id=%s start=%s end=%s row_count=%s bounded=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            auth_failed,
            user_id,
            range_start,
            range_end,
            row_count,
            bounded,
        )
        return response
