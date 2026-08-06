"""Shared response envelope and common request fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ResponseMeta(BaseModel):
    count: int = 0
    start: datetime | None = None
    end: datetime | None = None
    resolution: str | None = None
    bounded: bool = True
    request_id: str | None = None


class ApiResponse(BaseModel, Generic[T]):
    data: T
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
    warnings: list[str] = Field(default_factory=list)
    next_cursor: str | None = None


class TimeRangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    user_id: str | None = Field(default=None, description="External user identifier; defaults to primary user")
    limit: int | None = None
    include_deleted: bool = False


class SeriesRequest(TimeRangeRequest):
    resolution: str = "raw"
    sport: str | None = Field(
        default=None,
        description="Optional sport substring filter (runs series only)",
    )


class SummaryRequest(TimeRangeRequest):
    metric: str | None = None
    group_by: str = "day"


class EventRequest(TimeRangeRequest):
    pass


def utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


class MetadataMixin(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metadata: dict[str, Any] | None = Field(default=None, alias="metadata")
