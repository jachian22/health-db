"""Structured API errors for Phase 1."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.hint = hint
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.hint:
            payload["error"]["hint"] = self.hint
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__("UNAUTHORIZED", message, status_code=401, hint="Pass Authorization: Bearer <api_key>")


class InvalidRangeError(AppError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__("INVALID_RANGE", message, hint=hint or "Provide start < end as ISO-8601 UTC timestamps")


class RangeTooWideError(AppError):
    def __init__(self, message: str, max_days: int) -> None:
        super().__init__(
            "RANGE_TOO_WIDE",
            message,
            hint=f"Maximum lookback is {max_days} days",
            details={"max_lookback_days": max_days},
        )


class UnsupportedResolutionError(AppError):
    def __init__(self, resolution: str, allowed: list[str]) -> None:
        super().__init__(
            "UNSUPPORTED_RESOLUTION",
            f"Unsupported resolution '{resolution}'",
            hint=f"Allowed resolutions: {', '.join(allowed)}",
            details={"allowed": allowed},
        )


class MissingRequiredFieldError(AppError):
    def __init__(self, field: str) -> None:
        super().__init__(
            "MISSING_REQUIRED_FIELD",
            f"Missing required field: {field}",
            hint=f"Include '{field}' in the request body",
        )


class NoDataError(AppError):
    def __init__(self, message: str = "No data found for the requested range") -> None:
        super().__init__("NO_DATA", message, status_code=404)


class ConflictingFiltersError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("CONFLICTING_FILTERS", message)


class TooManyRowsError(AppError):
    def __init__(self, count: int, max_rows: int) -> None:
        super().__init__(
            "TOO_MANY_ROWS",
            f"Query would return {count} rows, exceeding the limit of {max_rows}",
            hint="Narrow the time range or increase resolution",
            details={"row_count": count, "max_rows": max_rows},
        )


class ValidationError(AppError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__("MISSING_REQUIRED_FIELD", message, hint=hint)


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
