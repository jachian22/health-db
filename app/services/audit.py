"""Persist metadata-only request audit rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models import RequestAuditLog, User

logger = get_logger(__name__)

# Phase 1 has a single seeded principal, so its UUID is stable for the
# process lifetime once resolved.
_user_id_cache: dict[str, uuid.UUID] = {}


async def resolve_audit_user_id(
    session_factory: async_sessionmaker[AsyncSession],
    external_id: str | None,
) -> uuid.UUID | None:
    if external_id is None:
        return None
    cached = _user_id_cache.get(external_id)
    if cached is not None:
        return cached
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(User.id).where(User.external_identifier == external_id)
            )
            user_id = result.scalar_one_or_none()
    except Exception:
        logger.exception("failed_to_resolve_audit_user external_id=%s", external_id)
        return None
    if user_id is not None:
        _user_id_cache[external_id] = user_id
    return user_id


async def write_request_audit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request_id: uuid.UUID,
    user_id: uuid.UUID | None,
    auth_role: str | None,
    method: str,
    path: str,
    status_code: int,
    started_at: datetime,
    latency_ms: int,
    query_start: datetime | None = None,
    query_end: datetime | None = None,
    requested_resolution: str | None = None,
    rows_returned: int | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    RequestAuditLog(
                        request_id=request_id,
                        user_id=user_id,
                        auth_role=auth_role,
                        method=method,
                        path=path,
                        status_code=status_code,
                        started_at=started_at,
                        latency_ms=latency_ms,
                        query_start=query_start,
                        query_end=query_end,
                        requested_resolution=requested_resolution,
                        rows_returned=rows_returned,
                        error_code=error_code,
                        metadata_=metadata or {},
                    )
                )
    except Exception:
        # Audit must never break the request path.
        logger.exception("failed_to_write_request_audit request_id=%s", request_id)
