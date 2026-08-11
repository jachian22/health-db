"""Optional per-user source catalog for provenance."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class HealthSource(TimestampMixin, Base):
    __tablename__ = "health_sources"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_name",
            name="uq_health_sources_user_id_source_source_name",
        ),
        # Postgres unique constraints treat NULLs as distinct, so NULL
        # source_name rows need their own partial unique index for
        # ON CONFLICT upserts (NULLS NOT DISTINCT needs PG15+; dev runs 14).
        Index(
            "uq_health_sources_user_id_source_null_name",
            "user_id",
            "source",
            unique=True,
            postgresql_where=text("source_name IS NULL"),
        ),
        Index("ix_health_sources_user_id_source", "user_id", "source"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
