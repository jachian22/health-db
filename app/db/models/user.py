"""User model."""

from __future__ import annotations

import uuid

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    external_identifier: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
