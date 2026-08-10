"""Meal event model — completion-time only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class MealEvent(TimestampMixin, Base):
    __tablename__ = "meal_events"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_sample_id", name="uq_meal_identity"),
        Index("ix_meal_user_completed_at", "user_id", "meal_completed_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sample_id: Mapped[str] = mapped_column(Text, nullable=False)
    meal_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    foods: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    ingestion_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_batches.id"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
