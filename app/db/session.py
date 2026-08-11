"""Async database engine and session management.

Engine creation is lazy: nothing connects at import or application startup.
Callers that need Postgres (including /ready) trigger the first connection.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

logger = logging.getLogger("app.db")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        _engine = create_async_engine(
            settings.async_database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped session. Callers manage commits explicitly."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database_ready() -> bool:
    """Return True if a minimal `SELECT 1` succeeds against Postgres.

    Does not raise; logs a sanitized failure when the database is unreachable.
    Never logs DATABASE_URL or driver messages that may embed credentials.
    """
    settings = get_settings()
    if not settings.database_url:
        return False
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        # Type only — exception messages / tracebacks can embed the DSN.
        logger.error("database_readiness_failed error_type=%s", type(exc).__name__)
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
