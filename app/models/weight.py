"""Weight measurement records."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.session import Base


class WeightMeasurement(Base):
    __tablename__ = "weight_measurements"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_weight_source_sample"),
        Index("ix_weight_user_measured_at", "user_id", "measured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("health_sources.id", ondelete="CASCADE"))
    source_sample_id: Mapped[str] = mapped_column(String(255))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON().with_variant(JSONB(), "postgresql"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
