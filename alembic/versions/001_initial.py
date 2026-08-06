"""Initial Phase 1 schema.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_identifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_external_identifier", "users", ["external_identifier"], unique=True)

    op.create_table(
        "health_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "source_name", name="uq_health_sources_user_name"),
    )
    op.create_index("ix_health_sources_user_id", "health_sources", ["user_id"])

    op.create_table(
        "glucose_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("health_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sample_id", sa.String(length=255), nullable=False),
        sa.Column("sample_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("trend", sa.String(length=64), nullable=True),
        sa.Column("metadata", JSONType, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_glucose_source_sample"),
    )
    op.create_index("ix_glucose_samples_user_id", "glucose_samples", ["user_id"])
    op.create_index("ix_glucose_user_sample_time", "glucose_samples", ["user_id", "sample_time"])

    op.create_table(
        "workouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("health_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sample_id", sa.String(length=255), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sport", sa.String(length=64), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("active_energy_kcal", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("metadata", JSONType, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_workouts_source_sample"),
    )
    op.create_index("ix_workouts_user_id", "workouts", ["user_id"])
    op.create_index("ix_workouts_user_start_time", "workouts", ["user_id", "start_time"])
    op.create_index("ix_workouts_user_end_time", "workouts", ["user_id", "end_time"])

    op.create_table(
        "sleep_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("health_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sample_id", sa.String(length=255), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("sleep_stage_summary", JSONType, nullable=True),
        sa.Column("metadata", JSONType, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_sleep_source_sample"),
    )
    op.create_index("ix_sleep_sessions_user_id", "sleep_sessions", ["user_id"])
    op.create_index("ix_sleep_user_start_time", "sleep_sessions", ["user_id", "start_time"])
    op.create_index("ix_sleep_user_end_time", "sleep_sessions", ["user_id", "end_time"])

    op.create_table(
        "weight_measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("health_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sample_id", sa.String(length=255), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("metadata", JSONType, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_weight_source_sample"),
    )
    op.create_index("ix_weight_measurements_user_id", "weight_measurements", ["user_id"])
    op.create_index("ix_weight_user_measured_at", "weight_measurements", ["user_id", "measured_at"])

    op.create_table(
        "meal_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("health_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sample_id", sa.String(length=255), nullable=False),
        sa.Column("meal_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meal_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meal_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("foods", JSONType, nullable=True),
        sa.Column("metadata", JSONType, nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "source_id", "source_sample_id", name="uq_meals_source_sample"),
    )
    op.create_index("ix_meal_events_user_id", "meal_events", ["user_id"])
    op.create_index("ix_meals_user_meal_start", "meal_events", ["user_id", "meal_start"])
    op.create_index("ix_meals_user_meal_completed_at", "meal_events", ["user_id", "meal_completed_at"])

    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("anchor", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "entity_type", "source_name", name="uq_sync_state_user_entity_source"),
    )
    op.create_index("ix_sync_state_user_id", "sync_state", ["user_id"])
    op.create_index(
        "ix_sync_state_user_entity_source",
        "sync_state",
        ["user_id", "entity_type", "source_name"],
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    op.drop_table("meal_events")
    op.drop_table("weight_measurements")
    op.drop_table("sleep_sessions")
    op.drop_table("workouts")
    op.drop_table("glucose_samples")
    op.drop_table("health_sources")
    op.drop_table("users")
