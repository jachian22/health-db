"""API dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db, get_session_factory

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_request_id(request: Request):
    return request.state.request_id


def session_factory_dep():
    return get_session_factory()
