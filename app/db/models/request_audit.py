"""Request audit log — metadata only; never raw health payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk


class RequestAuditLog(Base):
    __tablename__ = "request_audit_logs"
    __table_args__ = (
        Index("ix_request_audit_request_id", "request_id"),
        Index("ix_request_audit_created_at", "created_at"),
        Index("ix_request_audit_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    auth_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    query_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_returned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
