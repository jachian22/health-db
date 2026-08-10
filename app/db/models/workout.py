"""Workout model."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class Workout(TimestampMixin, Base):
    __tablename__ = "workouts"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_sample_id", name="uq_workout_identity"),
        Index("ix_workout_user_start_time", "user_id", "start_time"),
        Index("ix_workout_user_end_time", "user_id", "end_time"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sample_id: Mapped[str] = mapped_column(Text, nullable=False)
    sport: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    distance_meters: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    active_energy_kcal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    average_heart_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    maximum_heart_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    ingestion_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_batches.id"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
