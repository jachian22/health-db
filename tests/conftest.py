"""Shared pytest fixtures — requires PostgreSQL."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure test keys / env before app imports settings.
# Force-assign so a developer shell export (e.g. local curl READ_API_KEY) cannot
# leak into the test suite via setdefault.
os.environ["ENVIRONMENT"] = "test"
os.environ["INGEST_API_KEY"] = "test-ingest-key"
os.environ["READ_API_KEY"] = "test-read-key"
os.environ.setdefault("LOG_LEVEL", "WARNING")

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/health_db_test",
    ),
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import dispose_engine  # noqa: E402
from app.main import create_app  # noqa: E402

MINIMAL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "health_export_minimal.json"
QUERY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "health_export_fixture.json"
ROOT = Path(__file__).resolve().parents[1]


def _sync_url(async_url: str) -> str:
    if async_url.startswith("postgresql+asyncpg://"):
        return async_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return async_url


def _ensure_database_exists(async_url: str) -> None:
    sync_url = _sync_url(async_url)
    # Connect to maintenance DB to create test DB if needed
    if "/" not in sync_url.rsplit("@", 1)[-1]:
        return
    host_part, db_name = sync_url.rsplit("/", 1)
    admin_url = f"{host_part}/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> Generator[str, None, None]:
    # Safety guard: this fixture drops the entire public schema. Refuse to
    # run against anything that doesn't look like a dedicated test database.
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith("_test"):
        pytest.exit(
            f"Refusing to run destructive test setup against database '{db_name}'. "
            "Point TEST_DATABASE_URL/DATABASE_URL at a database whose name ends with '_test'.",
            returncode=1,
        )
    get_settings.cache_clear()
    _ensure_database_exists(TEST_DATABASE_URL)
    # Drop schema and remigrate for a clean session
    sync_url = _sync_url(TEST_DATABASE_URL)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    yield TEST_DATABASE_URL


@pytest_asyncio.fixture
async def session_factory(
    migrated_database: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    get_settings.cache_clear()
    await dispose_engine()
    engine = create_async_engine(migrated_database, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()
    await dispose_engine()


@pytest_asyncio.fixture
async def client(
    migrated_database: str,
) -> AsyncGenerator[AsyncClient, None]:
    get_settings.cache_clear()
    await dispose_engine()
    # Truncate typed tables between tests while keeping seeded user
    sync_url = _sync_url(migrated_database)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                TRUNCATE TABLE
                  glucose_samples,
                  workouts,
                  sleep_intervals,
                  weight_measurements,
                  meal_events,
                  ingestion_batches,
                  health_sources
                RESTART IDENTITY CASCADE
                """
            )
        )
    engine.dispose()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_engine()


@pytest.fixture
def ingest_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-ingest-key"}


@pytest.fixture
def read_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-read-key"}


@pytest.fixture
def export_fixture() -> dict:
    return json.loads(MINIMAL_FIXTURE_PATH.read_text())


@pytest.fixture
def ingest_body(export_fixture: dict) -> dict:
    """iOS export object accepted directly as the ingest request body."""
    return export_fixture


@pytest.fixture
def query_seed_body() -> dict:
    """Richer fixture used by query integration tests."""
    return json.loads(QUERY_FIXTURE_PATH.read_text())
