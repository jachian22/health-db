"""API dependency helpers."""

from __future__ import annotations

from fastapi import Request

from app.schemas.common import ApiResponse, ResponseMeta


def attach_query_meta(request: Request, meta: dict) -> None:
    request.state.row_count = meta.get("count")
    request.state.user_id = meta.get("user_id")
    request.state.range_start = meta.get("start")
    request.state.range_end = meta.get("end")
    request.state.bounded = meta.get("bounded", True)


def envelope(request: Request, data, meta: dict, warnings: list[str] | None = None) -> ApiResponse:
    attach_query_meta(request, meta)
    return ApiResponse(
        data=data,
        meta=ResponseMeta(
            count=meta.get("count", 0),
            start=meta.get("start"),
            end=meta.get("end"),
            resolution=meta.get("resolution"),
            bounded=meta.get("bounded", True),
            request_id=getattr(request.state, "request_id", None),
        ),
        warnings=warnings or [],
    )
