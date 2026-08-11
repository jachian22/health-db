"""Meal event model — completion-time only (no meal_start / meal_end)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IngestedTimestampMixin, uuid_pk


class MealEvent(IngestedTimestampMixin, Base):
    __tablename__ = "meal_events"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_sample_id",
            name="uq_meal_events_user_id_source_source_sample_id",
        ),
        Index("ix_meal_events_user_id_meal_completed_at", "user_id", "meal_completed_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    health_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("health_sources.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sample_id: Mapped[str] = mapped_column(Text, nullable=False)
    meal_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    foods: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
