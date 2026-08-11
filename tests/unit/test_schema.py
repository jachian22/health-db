"""Schema-level guarantees for the Phase 1 PostgreSQL models and migration."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from alembic import command
from app.db.base import NAMING_CONVENTION, Base
from app.db.models import (  # noqa: F401 — register metadata
    GlucoseSample,
    HealthSource,
    IngestionBatch,
    MealEvent,
    SleepInterval,
    User,
    WeightMeasurement,
    Workout,
)

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_TABLES = {
    "users",
    "health_sources",
    "ingestion_batches",
    "glucose_samples",
    "workouts",
    "sleep_intervals",
    "weight_measurements",
    "meal_events",
}

SOURCE_DERIVED_TABLES = (
    "glucose_samples",
    "workouts",
    "sleep_intervals",
    "weight_measurements",
    "meal_events",
)

IDENTITY_UQ = {
    "glucose_samples": "uq_glucose_samples_user_id_source_source_sample_id",
    "workouts": "uq_workouts_user_id_source_source_sample_id",
    "sleep_intervals": "uq_sleep_intervals_user_id_source_source_sample_id",
    "weight_measurements": "uq_weight_measurements_user_id_source_source_sample_id",
    "meal_events": "uq_meal_events_user_id_source_source_sample_id",
}

REQUIRED_INDEX_NAMES = {
    "ix_health_sources_user_id_source",
    "ix_ingestion_batches_user_id_received_at",
    "ix_ingestion_batches_user_id_payload_sha256",
    "ix_glucose_samples_user_id_sample_time",
    "ix_glucose_samples_user_id_source_sample_time",
    "ix_workouts_user_id_start_time",
    "ix_workouts_user_id_sport_start_time",
    "ix_sleep_intervals_user_id_start_time",
    "ix_sleep_intervals_user_id_end_time",
    "ix_sleep_intervals_user_id_stage_start_time",
    "ix_weight_measurements_user_id_measured_at",
    "ix_meal_events_user_id_meal_completed_at",
}


def _sync_url(async_url: str) -> str:
    if async_url.startswith("postgresql+asyncpg://"):
        return async_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return async_url


def test_required_tables_present_in_metadata():
    assert REQUIRED_TABLES.issubset(set(Base.metadata.tables))
    assert "request_audit_logs" not in Base.metadata.tables


@pytest.mark.parametrize("table_name", SOURCE_DERIVED_TABLES)
def test_source_derived_identity_unique_constraint(table_name: str):
    table = Base.metadata.tables[table_name]
    expected = IDENTITY_UQ[table_name]
    matches = [
        c
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
        and c.name == expected
        and [col.name for col in c.columns] == ["user_id", "source", "source_sample_id"]
    ]
    assert matches, f"{table_name} missing identity UniqueConstraint {expected}"


def test_meal_events_has_completed_at_not_start_end():
    columns = set(Base.metadata.tables["meal_events"].c.keys())
    assert "meal_completed_at" in columns
    assert "meal_start" not in columns
    assert "meal_end" not in columns


def test_weight_value_kg_is_numeric_not_float():
    col = Base.metadata.tables["weight_measurements"].c.value_kg
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 10
    assert col.type.scale == 6


def test_glucose_value_mg_dl_is_numeric_not_float():
    col = Base.metadata.tables["glucose_samples"].c.value_mg_dl
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 10
    assert col.type.scale == 3


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("users", "created_at"),
        ("users", "updated_at"),
        ("health_sources", "created_at"),
        ("ingestion_batches", "received_at"),
        ("ingestion_batches", "exported_at"),
        ("ingestion_batches", "data_start"),
        ("ingestion_batches", "data_end"),
        ("glucose_samples", "sample_time"),
        ("glucose_samples", "ingested_at"),
        ("glucose_samples", "updated_at"),
        ("glucose_samples", "deleted_at"),
        ("workouts", "start_time"),
        ("workouts", "end_time"),
        ("sleep_intervals", "start_time"),
        ("weight_measurements", "measured_at"),
        ("meal_events", "meal_completed_at"),
    ],
)
def test_core_timestamps_are_timezone_aware(table_name: str, column_name: str):
    col = Base.metadata.tables[table_name].c[column_name]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True


def test_json_columns_use_jsonb():
    assert isinstance(Base.metadata.tables["ingestion_batches"].c.raw_payload.type, JSONB)
    assert isinstance(Base.metadata.tables["glucose_samples"].c.metadata.type, JSONB)
    assert isinstance(Base.metadata.tables["meal_events"].c.foods.type, JSONB)
    assert isinstance(Base.metadata.tables["ingestion_batches"].c.error_summary.type, JSONB)


def test_naming_convention_is_deterministic():
    assert NAMING_CONVENTION["pk"] == "pk_%(table_name)s"
    assert NAMING_CONVENTION["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )
    assert NAMING_CONVENTION["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"
    assert NAMING_CONVENTION["ix"] == "ix_%(table_name)s_%(column_0_N_name)s"
    assert NAMING_CONVENTION["ck"] == "ck_%(table_name)s_%(constraint_name)s"

    declared_indexes = {
        idx.name
        for table in Base.metadata.tables.values()
        for idx in table.indexes
        if isinstance(idx, Index) and idx.name
    }
    assert REQUIRED_INDEX_NAMES.issubset(declared_indexes)

    uq_names = {
        c.name
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and c.name
    }
    assert set(IDENTITY_UQ.values()).issubset(uq_names)

    ck_names = {
        c.name
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    assert "ck_glucose_samples_value_mg_dl_positive" in ck_names
    assert "ck_weight_measurements_value_kg_positive" in ck_names
    assert "ck_workouts_end_after_start" in ck_names
    assert "ck_ingestion_batches_data_end_after_data_start" in ck_names


@pytest.fixture(scope="module")
def migration_database_url() -> Generator[str, None, None]:
    """Isolated DB for upgrade/downgrade cycle (not the shared integration DB)."""
    base = os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/health_db_test",
        ),
    )
    # Force a dedicated schema-cycle database so we never fight conftest's
    # session-scoped migrated_database fixture.
    if "/" not in base.rsplit("@", 1)[-1]:
        pytest.skip("Cannot derive migration test database URL")
    host_part = base.rsplit("/", 1)[0]
    url = f"{host_part}/health_db_schema_test"
    sync_url = _sync_url(url)

    admin = create_engine(
        f"{sync_url.rsplit('/', 1)[0]}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": "health_db_schema_test"},
        ).scalar()
        if not exists:
            conn.execute(text('CREATE DATABASE "health_db_schema_test"'))
    admin.dispose()
    yield url


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def test_alembic_upgrade_creates_full_schema(migration_database_url: str):
    sync_url = _sync_url(migration_database_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    command.upgrade(_alembic_config(sync_url), "head")

    engine = create_engine(sync_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert REQUIRED_TABLES.issubset(tables)
    assert "request_audit_logs" not in tables
    assert "alembic_version" in tables

    # Spot-check identity unique + timezone + JSONB via inspector/reflection.
    uqs = {u["name"] for u in insp.get_unique_constraints("glucose_samples")}
    assert "uq_glucose_samples_user_id_source_source_sample_id" in uqs

    indexes = {idx["name"] for idx in insp.get_indexes("workouts")}
    assert "ix_workouts_user_id_sport_start_time" in indexes

    meal_cols = {c["name"] for c in insp.get_columns("meal_events")}
    assert "meal_completed_at" in meal_cols
    assert "meal_start" not in meal_cols
    assert "meal_end" not in meal_cols

    with engine.connect() as conn:
        seeded = conn.execute(
            text(
                "SELECT external_identifier FROM users "
                "WHERE external_identifier = 'personal-primary'"
            )
        ).scalar()
        assert seeded == "personal-primary"
    engine.dispose()


def test_constraint_and_index_names_fit_postgres_limit():
    """Postgres truncates identifiers at 63 chars; truncation is silent."""
    for table in Base.metadata.tables.values():
        for obj in list(table.constraints) + list(table.indexes):
            if obj.name is None:
                continue
            assert len(str(obj.name)) <= 63, (
                f"{table.name}: identifier too long ({len(str(obj.name))}): {obj.name}"
            )


def test_alembic_downgrade_base_removes_schema(migration_database_url: str):
    sync_url = _sync_url(migration_database_url)
    cfg = _alembic_config(sync_url)

    # Ensure we start from head (previous test may have left it there).
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(sync_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert REQUIRED_TABLES.isdisjoint(tables)
    engine.dispose()

    # Prove the cycle is clean.
    command.upgrade(cfg, "head")
    engine = create_engine(sync_url)
    insp = inspect(engine)
    assert REQUIRED_TABLES.issubset(set(insp.get_table_names()))
    engine.dispose()
