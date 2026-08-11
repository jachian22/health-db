"""Enforce health_sources uniqueness for NULL source_name rows.

Postgres treats NULLs as distinct in unique constraints, so rows with
source_name IS NULL (e.g. manual meal events) could be duplicated under
concurrency. Add a partial unique index covering the NULL case so
ON CONFLICT upserts work for both null and non-null source names.
(NULLS NOT DISTINCT would be cleaner but requires Postgres 15+; local
dev runs 14.)

Revision ID: 002_source_nulls
Revises: 001_initial
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "002_source_nulls"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NULL_NAME_INDEX = "uq_health_sources_user_id_source_null_name"

# Typed tables that reference health_sources.id and must be re-pointed
# before duplicate catalog rows can be deleted.
_REFERENCING_TABLES = (
    "glucose_samples",
    "workouts",
    "sleep_intervals",
    "weight_measurements",
    "meal_events",
)


def upgrade() -> None:
    # Duplicates are only possible where source_name IS NULL (the existing
    # constraint already enforces uniqueness for non-null names). Keep the
    # oldest row per (user_id, source), re-point references, delete extras.
    for table in _REFERENCING_TABLES:
        op.execute(
            f"""
            WITH dupes AS (
                SELECT id,
                       first_value(id) OVER (
                           PARTITION BY user_id, source
                           ORDER BY created_at, id
                       ) AS keep_id
                FROM health_sources
                WHERE source_name IS NULL
            )
            UPDATE {table} t
            SET health_source_id = d.keep_id
            FROM dupes d
            WHERE t.health_source_id = d.id
              AND d.id <> d.keep_id
            """
        )
    op.execute(
        """
        WITH dupes AS (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY user_id, source
                       ORDER BY created_at, id
                   ) AS keep_id
            FROM health_sources
            WHERE source_name IS NULL
        )
        DELETE FROM health_sources h
        USING dupes d
        WHERE h.id = d.id
          AND d.id <> d.keep_id
        """
    )

    op.create_index(
        _NULL_NAME_INDEX,
        "health_sources",
        ["user_id", "source"],
        unique=True,
        postgresql_where="source_name IS NULL",
    )


def downgrade() -> None:
    op.drop_index(_NULL_NAME_INDEX, table_name="health_sources")
