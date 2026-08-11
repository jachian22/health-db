"""Weight measurement model — kilograms are canonical; no pound storage."""

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


class WeightMeasurement(IngestedTimestampMixin, Base):
    __tablename__ = "weight_measurements"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_sample_id",
            name="uq_weight_measurements_user_id_source_source_sample_id",
        ),
        Index("ix_weight_measurements_user_id_measured_at", "user_id", "measured_at"),
        CheckConstraint("value_kg > 0", name="value_kg_positive"),
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
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_kg: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
