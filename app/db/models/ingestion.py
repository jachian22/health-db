"""Ingestion batch audit model — one raw payload copy per export."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class IngestionBatch(TimestampMixin, Base):
    __tablename__ = "ingestion_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    glucose_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    glucose_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    glucose_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    glucose_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    workouts_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    workouts_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    workouts_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    workouts_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    sleep_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sleep_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sleep_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sleep_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    weight_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    weight_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    weight_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    weight_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    meals_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    meals_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    meals_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    meals_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
