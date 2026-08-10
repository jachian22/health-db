"""Initial Phase 1 schema: users, ingestion_batches, typed health tables, audit logs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("external_identifier", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("external_identifier", name="uq_users_external_identifier"),
    )

    op.create_table(
        "ingestion_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("glucose_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("glucose_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("glucose_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("glucose_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workouts_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workouts_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workouts_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workouts_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sleep_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sleep_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sleep_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sleep_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meals_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meals_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meals_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meals_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ingestion_batches_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_batches"),
    )

    op.create_table(
        "glucose_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_sample_id", sa.Text(), nullable=False),
        sa.Column("sample_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_mg_dl", sa.Numeric(10, 3), nullable=False),
        sa.Column("trend", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batches.id"], name="fk_glucose_samples_ingestion_batch_id_ingestion_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_glucose_samples_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_glucose_samples"),
        sa.UniqueConstraint("user_id", "source", "source_sample_id", name="uq_glucose_identity"),
    )
    op.create_index("ix_glucose_user_sample_time", "glucose_samples", ["user_id", "sample_time"])

    op.create_table(
        "workouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_sample_id", sa.Text(), nullable=False),
        sa.Column("sport", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("distance_meters", sa.Numeric(12, 3), nullable=True),
        sa.Column("active_energy_kcal", sa.Numeric(12, 3), nullable=True),
        sa.Column("average_heart_rate", sa.Numeric(10, 3), nullable=True),
        sa.Column("maximum_heart_rate", sa.Numeric(10, 3), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batches.id"], name="fk_workouts_ingestion_batch_id_ingestion_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_workouts_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_workouts"),
        sa.UniqueConstraint("user_id", "source", "source_sample_id", name="uq_workout_identity"),
    )
    op.create_index("ix_workout_user_start_time", "workouts", ["user_id", "start_time"])
    op.create_index("ix_workout_user_end_time", "workouts", ["user_id", "end_time"])

    op.create_table(
        "sleep_intervals",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_sample_id", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batches.id"], name="fk_sleep_intervals_ingestion_batch_id_ingestion_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sleep_intervals_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_sleep_intervals"),
        sa.UniqueConstraint("user_id", "source", "source_sample_id", name="uq_sleep_identity"),
    )
    op.create_index("ix_sleep_user_start_time", "sleep_intervals", ["user_id", "start_time"])
    op.create_index("ix_sleep_user_end_time", "sleep_intervals", ["user_id", "end_time"])

    op.create_table(
        "weight_measurements",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_sample_id", sa.Text(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_kg", sa.Numeric(10, 6), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batches.id"], name="fk_weight_measurements_ingestion_batch_id_ingestion_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_weight_measurements_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_weight_measurements"),
        sa.UniqueConstraint("user_id", "source", "source_sample_id", name="uq_weight_identity"),
    )
    op.create_index("ix_weight_user_measured_at", "weight_measurements", ["user_id", "measured_at"])

    op.create_table(
        "meal_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_sample_id", sa.Text(), nullable=False),
        sa.Column("meal_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("foods", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batches.id"], name="fk_meal_events_ingestion_batch_id_ingestion_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_meal_events_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_meal_events"),
        sa.UniqueConstraint("user_id", "source", "source_sample_id", name="uq_meal_identity"),
    )
    op.create_index("ix_meal_user_completed_at", "meal_events", ["user_id", "meal_completed_at"])

    op.create_table(
        "request_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("auth_role", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("query_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("query_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_resolution", sa.Text(), nullable=True),
        sa.Column("rows_returned", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_request_audit_logs_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_request_audit_logs"),
    )
    op.create_index("ix_request_audit_request_id", "request_audit_logs", ["request_id"])
    op.create_index("ix_request_audit_created_at", "request_audit_logs", ["created_at"])
    op.create_index("ix_request_audit_user_created", "request_audit_logs", ["user_id", "created_at"])

    # Seed Phase 1 principal idempotently
    op.execute(
        """
        INSERT INTO users (external_identifier)
        VALUES ('personal-primary')
        ON CONFLICT (external_identifier) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_request_audit_user_created", table_name="request_audit_logs")
    op.drop_index("ix_request_audit_created_at", table_name="request_audit_logs")
    op.drop_index("ix_request_audit_request_id", table_name="request_audit_logs")
    op.drop_table("request_audit_logs")

    op.drop_index("ix_meal_user_completed_at", table_name="meal_events")
    op.drop_table("meal_events")

    op.drop_index("ix_weight_user_measured_at", table_name="weight_measurements")
    op.drop_table("weight_measurements")

    op.drop_index("ix_sleep_user_end_time", table_name="sleep_intervals")
    op.drop_index("ix_sleep_user_start_time", table_name="sleep_intervals")
    op.drop_table("sleep_intervals")

    op.drop_index("ix_workout_user_end_time", table_name="workouts")
    op.drop_index("ix_workout_user_start_time", table_name="workouts")
    op.drop_table("workouts")

    op.drop_index("ix_glucose_user_sample_time", table_name="glucose_samples")
    op.drop_table("glucose_samples")

    op.drop_table("ingestion_batches")
    op.drop_table("users")
