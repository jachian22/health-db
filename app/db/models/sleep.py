"""Sleep interval model — raw HealthKit intervals only; no sessionization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IngestedTimestampMixin, uuid_pk


class SleepInterval(IngestedTimestampMixin, Base):
    __tablename__ = "sleep_intervals"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_sample_id",
            name="uq_sleep_intervals_user_id_source_source_sample_id",
        ),
        Index("ix_sleep_intervals_user_id_start_time", "user_id", "start_time"),
        Index("ix_sleep_intervals_user_id_end_time", "user_id", "end_time"),
        Index("ix_sleep_intervals_user_id_stage_start_time", "user_id", "stage", "start_time"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
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
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
