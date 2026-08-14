"""Workout model — Strava-filtered at the exporter; DB keeps general provenance."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IngestedTimestampMixin, uuid_pk


class Workout(IngestedTimestampMixin, Base):
    __tablename__ = "workouts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_sample_id",
            name="uq_workouts_user_id_source_source_sample_id",
        ),
        Index("ix_workouts_user_id_start_time", "user_id", "start_time"),
        Index("ix_workouts_user_id_sport_start_time", "user_id", "sport", "start_time"),
        # Overlap queries also filter end_time. Do not add
        # ix_workouts_user_id_end_time until EXPLAIN (ANALYZE, BUFFERS)
        # after deploy shows the current plan or measured latency needs it.
        CheckConstraint("end_time > start_time", name="end_after_start"),
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_meters_nonnegative",
        ),
        CheckConstraint(
            "active_energy_kcal IS NULL OR active_energy_kcal >= 0",
            name="active_energy_kcal_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "maximum_heart_rate IS NULL OR maximum_heart_rate >= 0",
            name="maximum_heart_rate_nonnegative",
        ),
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
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sport: Mapped[str] = mapped_column(Text, nullable=False)
    distance_meters: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    active_energy_kcal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    average_heart_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    maximum_heart_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
