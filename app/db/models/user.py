"""User model — ownership boundary for the Phase 1 single principal."""

from __future__ import annotations

import uuid

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("external_identifier", name="uq_users_external_identifier"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    external_identifier: Mapped[str] = mapped_column(Text, nullable=False)
