"""Ingestion batch audit — one raw payload copy per iOS export."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk

INGESTION_BATCH_STATUSES = ("received", "processed", "partial", "failed")


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (
        CheckConstraint("data_end > data_start", name="data_end_after_data_start"),
        CheckConstraint(
            "status IN ('received', 'processed', 'partial', 'failed')",
            name="status_valid",
        ),
        Index("ix_ingestion_batches_user_id_received_at", "user_id", "received_at"),
        Index("ix_ingestion_batches_user_id_payload_sha256", "user_id", "payload_sha256"),
    )

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
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

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

    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
