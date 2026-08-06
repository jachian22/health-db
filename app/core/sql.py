"""Dialect-aware SQL helpers for Postgres (prod) and SQLite (tests)."""

from __future__ import annotations

from sqlalchemy import Integer, cast, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement


def dialect_name(db: AsyncSession) -> str:
    bind = db.get_bind()
    return bind.dialect.name


def date_trunc_day(col: ColumnElement, dialect: str) -> ColumnElement:
    if dialect == "postgresql":
        return func.date_trunc("day", col)
    return func.date(col)


def date_trunc_week(col: ColumnElement, dialect: str) -> ColumnElement:
    """Monday-based week start (matches Postgres date_trunc('week', ...))."""
    if dialect == "postgresql":
        return func.date_trunc("week", col)
    # SQLite %w: 0=Sunday … 6=Saturday → days since Monday = (dow + 6) % 7
    dow = cast(func.strftime("%w", col), Integer)
    days_since_monday = (dow + 6) % 7
    return func.date(col, func.printf("-%d days", days_since_monday))


def epoch_seconds(col: ColumnElement, dialect: str) -> ColumnElement:
    if dialect == "postgresql":
        return func.extract("epoch", col)
    return cast(func.strftime("%s", col), Integer)


def time_bucket_start(col: ColumnElement, seconds: int, dialect: str) -> ColumnElement:
    """Start of the epoch-aligned bucket containing ``col``."""
    bucket = func.floor(epoch_seconds(col, dialect) / seconds) * seconds
    if dialect == "postgresql":
        return func.to_timestamp(bucket)
    return func.datetime(bucket, "unixepoch")
