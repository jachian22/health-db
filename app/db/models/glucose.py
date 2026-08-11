"""Glucose sample model."""

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


class GlucoseSample(IngestedTimestampMixin, Base):
    __tablename__ = "glucose_samples"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_sample_id",
            name="uq_glucose_samples_user_id_source_source_sample_id",
        ),
        Index("ix_glucose_samples_user_id_sample_time", "user_id", "sample_time"),
        Index(
            "ix_glucose_samples_user_id_source_sample_time",
            "user_id",
            "source",
            "sample_time",
        ),
        CheckConstraint("value_mg_dl > 0", name="value_mg_dl_positive"),
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
    sample_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_mg_dl: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    trend: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
