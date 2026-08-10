"""Shared Pydantic helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, Field


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(ensure_utc)]

NonEmptyStr = Annotated[str, Field(min_length=1)]
