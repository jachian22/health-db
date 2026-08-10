"""Stable API error contract."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    hint: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str = Field(description="Correlates with X-Request-ID")


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        hint: str | None = None,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        self.status_code = status_code
        self.details = details
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or "unknown"
    request.state.error_code = exc.code
    body = ErrorResponse(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            hint=exc.hint,
            details=exc.details,
        ),
        request_id=str(request_id),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))
