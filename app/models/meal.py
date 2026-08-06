"""Meal event records — interval plus optional completion anchor."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.session import Base


class MealEvent(Base):
    __tablename__ = "meal_events"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_meals_source_sample"),
        Index("ix_meals_user_meal_start", "user_id", "meal_start"),
        Index("ix_meals_user_meal_completed_at", "user_id", "meal_completed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("health_sources.id", ondelete="CASCADE"))
    source_sample_id: Mapped[str] = mapped_column(String(255))
    meal_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    meal_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    meal_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    foods: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON().with_variant(JSONB(), "postgresql"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
