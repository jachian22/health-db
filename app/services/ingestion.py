"""Ingestion service: validate, audit, and idempotently upsert typed health rows.

Upserts use PostgreSQL `INSERT ... ON CONFLICT ... DO UPDATE` against the
`(user_id, source, source_sample_id)` unique constraint. The DO UPDATE clause
carries an IS DISTINCT FROM guard over meaningful fields, so materially
identical replays touch nothing and are classified `unchanged`. `xmax = 0`
on the RETURNING rows distinguishes inserts from updates.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import (
    GlucoseSample,
    IngestionBatch,
    MealEvent,
    SleepInterval,
    User,
    WeightMeasurement,
    Workout,
)
from app.schemas.export import (
    GlucoseSampleIn,
    HealthExportPayload,
    MealEventIn,
    SleepIntervalIn,
    WeightMeasurementIn,
    WorkoutIn,
)
from app.schemas.ingestion import (
    EntityResultCounts,
    IngestBatchResponse,
    IngestRejection,
    IngestResults,
    SourceWindow,
)

logger = get_logger(__name__)

# Validators embed a machine-readable code prefix in their messages
# (e.g. ValueError("INVALID_UNIT: Expected mg/dL.")) so rejection codes
# are deterministic instead of being guessed from message text.
_REJECTION_CODE_RE = re.compile(
    r"(INVALID_UNIT|INVALID_TIMESTAMP|INVALID_SLEEP_STAGE|INVALID_WORKOUT|INVALID_REQUEST):\s*"
)


@dataclass
class _EntityOutcome:
    counts: EntityResultCounts = field(default_factory=EntityResultCounts)
    rejections: list[IngestRejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _EntitySpec:
    """Everything the generic upsert needs to know about one entity type."""

    entity_type: str
    model: type
    schema: type[BaseModel]
    # Column names compared by IS DISTINCT FROM to decide updated vs unchanged.
    meaningful_columns: tuple[str, ...]
    # Validated item -> full column-name -> value row dict (identity + fields).
    row_builder: Callable[[Any, uuid.UUID, uuid.UUID], dict[str, Any]]
    # Optional per-item warning (used for sleep stage normalization).
    warning_for: Callable[[Any], str | None] = lambda _item: None


def _payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rejection_from(
    exc: ValidationError, entity_type: str, raw: dict[str, Any]
) -> IngestRejection:
    errors = exc.errors()
    raw_msg = str(errors[0]["msg"]) if errors else str(exc)
    match = _REJECTION_CODE_RE.search(raw_msg)
    code = match.group(1) if match else "INVALID_REQUEST"
    # Strip Pydantic's "Value error, " wrapper and our code prefix for readability.
    message = _REJECTION_CODE_RE.sub("", raw_msg).removeprefix("Value error, ").strip()
    message = message or raw_msg
    sample_id = raw.get("source_sample_id")
    return IngestRejection(
        entity_type=entity_type,
        source_sample_id=str(sample_id) if sample_id is not None else None,
        code=code,
        message=message,
    )


def _decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def get_primary_user(session: AsyncSession, external_id: str) -> User:
    result = await session.execute(select(User).where(User.external_identifier == external_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            code="INTERNAL_ERROR",
            message=f"Primary user '{external_id}' is not seeded.",
            hint="Run alembic upgrade head to seed personal-primary.",
            status_code=500,
        )
    return user


def _glucose_row(item: GlucoseSampleIn, user_id: uuid.UUID, batch_id: uuid.UUID) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "sample_time": item.sample_time,
        "value_mg_dl": _decimal(item.value),
        "trend": item.trend,
        "metadata": item.metadata,
        "ingestion_batch_id": batch_id,
    }


def _workout_row(item: WorkoutIn, user_id: uuid.UUID, batch_id: uuid.UUID) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "sport": item.sport,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "distance_meters": _decimal(item.distance_meters),
        "active_energy_kcal": _decimal(item.active_energy_kcal),
        "average_heart_rate": _decimal(item.average_heart_rate),
        "maximum_heart_rate": _decimal(item.maximum_heart_rate),
        "metadata": item.metadata,
        "ingestion_batch_id": batch_id,
    }


def _sleep_row(item: SleepIntervalIn, user_id: uuid.UUID, batch_id: uuid.UUID) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "stage": item.stage,
        "metadata": item.metadata,
        "ingestion_batch_id": batch_id,
    }


def _weight_row(
    item: WeightMeasurementIn, user_id: uuid.UUID, batch_id: uuid.UUID
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "measured_at": item.measured_at,
        "value_kg": _decimal(item.value),
        "metadata": item.metadata,
        "ingestion_batch_id": batch_id,
    }


def _meal_row(item: MealEventIn, user_id: uuid.UUID, batch_id: uuid.UUID) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "meal_completed_at": item.meal_completed_at,
        "foods": item.foods,
        "notes": item.notes,
        "metadata": item.metadata,
        "ingestion_batch_id": batch_id,
    }


def _sleep_warning(item: SleepIntervalIn) -> str | None:
    if item.stage_warning:
        return f"sleep_sessions:{item.source_sample_id}: {item.stage_warning}"
    return None


_ENTITY_SPECS: dict[str, _EntitySpec] = {
    "glucose_samples": _EntitySpec(
        entity_type="glucose_samples",
        model=GlucoseSample,
        schema=GlucoseSampleIn,
        meaningful_columns=("sample_time", "value_mg_dl", "trend", "source_name", "metadata"),
        row_builder=_glucose_row,
    ),
    "workouts": _EntitySpec(
        entity_type="workouts",
        model=Workout,
        schema=WorkoutIn,
        meaningful_columns=(
            "sport",
            "start_time",
            "end_time",
            "distance_meters",
            "active_energy_kcal",
            "average_heart_rate",
            "maximum_heart_rate",
            "source_name",
            "metadata",
        ),
        row_builder=_workout_row,
    ),
    "sleep_sessions": _EntitySpec(
        entity_type="sleep_sessions",
        model=SleepInterval,
        schema=SleepIntervalIn,
        meaningful_columns=("start_time", "end_time", "stage", "source_name", "metadata"),
        row_builder=_sleep_row,
        warning_for=_sleep_warning,
    ),
    "weight_measurements": _EntitySpec(
        entity_type="weight_measurements",
        model=WeightMeasurement,
        schema=WeightMeasurementIn,
        meaningful_columns=("measured_at", "value_kg", "source_name", "metadata"),
        row_builder=_weight_row,
    ),
    "meal_events": _EntitySpec(
        entity_type="meal_events",
        model=MealEvent,
        schema=MealEventIn,
        meaningful_columns=(
            "meal_completed_at",
            "foods",
            "notes",
            "source_name",
            "metadata",
        ),
        row_builder=_meal_row,
    ),
}


async def _upsert_entity(
    session_factory: async_sessionmaker[AsyncSession],
    spec: _EntitySpec,
    user_id: uuid.UUID,
    batch_id: uuid.UUID,
    raw_items: list[dict[str, Any]],
) -> _EntityOutcome:
    outcome = _EntityOutcome()
    outcome.counts.received = len(raw_items)

    accepted: list[Any] = []
    for raw in raw_items:
        try:
            item = spec.schema.model_validate(raw)
        except ValidationError as exc:
            outcome.counts.rejected += 1
            outcome.rejections.append(_rejection_from(exc, spec.entity_type, raw))
            continue
        warning = spec.warning_for(item)
        if warning:
            outcome.warnings.append(warning)
        accepted.append(item)

    if not accepted:
        return outcome

    # ON CONFLICT cannot update the same row twice in one statement, so
    # de-duplicate by identity, keeping the last occurrence (later wins).
    deduped: dict[tuple[str, str], Any] = {}
    for item in accepted:
        deduped[(item.source, item.source_sample_id)] = item
    if len(deduped) < len(accepted):
        outcome.warnings.append(
            f"{spec.entity_type}: payload contained "
            f"{len(accepted) - len(deduped)} duplicate source_sample_id(s); "
            "the last occurrence of each was applied."
        )

    rows = [spec.row_builder(item, user_id, batch_id) for item in deduped.values()]
    table = sa.inspect(spec.model).local_table

    stmt = pg_insert(table).values(rows)
    excluded = stmt.excluded
    set_: dict[str, Any] = {col: excluded[col] for col in spec.meaningful_columns}
    set_["ingestion_batch_id"] = excluded["ingestion_batch_id"]
    set_["deleted_at"] = None
    set_["updated_at"] = sa.func.now()

    changed = sa.or_(
        *[table.c[col].is_distinct_from(excluded[col]) for col in spec.meaningful_columns],
        table.c.deleted_at.is_not(None),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "source", "source_sample_id"],
        set_=set_,
        where=changed,
    ).returning(sa.literal_column("(xmax = 0)").label("was_inserted"))

    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(stmt)
            flags = [row.was_inserted for row in result]

    inserted = sum(1 for f in flags if f)
    outcome.counts.inserted = inserted
    outcome.counts.updated = len(flags) - inserted
    outcome.counts.unchanged = len(rows) - len(flags)
    return outcome


async def _mark_batch_failed(
    session_factory: async_sessionmaker[AsyncSession], batch_id: uuid.UUID
) -> None:
    try:
        async with session_factory() as session:
            async with session.begin():
                batch = await session.get(IngestionBatch, batch_id)
                if batch is not None:
                    batch.status = "failed"
    except Exception:
        logger.exception("failed_to_mark_batch_failed batch_id=%s", batch_id)


async def ingest_batch(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    payload: HealthExportPayload,
    external_user_id: str,
    request_id: uuid.UUID,
) -> IngestBatchResponse:
    payload.enforce_phase1_contract()
    raw_payload = payload.model_dump(mode="json")
    checksum = _payload_sha256(raw_payload)
    warnings: list[str] = []
    rejections: list[IngestRejection] = []

    if payload.errors:
        warnings.append(
            f"Source export reported {len(payload.errors)} error(s); "
            "valid records will still be ingested."
        )

    # Transaction 0: create pending batch with the single raw payload copy.
    async with session_factory() as session:
        async with session.begin():
            user = await get_primary_user(session, external_user_id)
            batch = IngestionBatch(
                user_id=user.id,
                schema_version=payload.schema_version,
                exported_at=payload.exported_at,
                data_start=payload.data_start,
                data_end=payload.data_end,
                payload_sha256=checksum,
                raw_payload=raw_payload,
                status="processing",
                request_id=request_id,
            )
            session.add(batch)
            await session.flush()
            batch_id = batch.id
            user_id = user.id

    entity_payloads = {
        "glucose_samples": payload.glucose_samples,
        "workouts": payload.workouts,
        "sleep_sessions": payload.sleep_sessions,
        "weight_measurements": payload.weight_measurements,
        "meal_events": payload.meal_events,
    }

    # Each entity type runs in its own transaction. If anything blows up,
    # mark the batch failed instead of leaving it stuck in 'processing'.
    outcomes: dict[str, _EntityOutcome] = {}
    try:
        for entity_type, items in entity_payloads.items():
            outcomes[entity_type] = await _upsert_entity(
                session_factory, _ENTITY_SPECS[entity_type], user_id, batch_id, items
            )
    except Exception:
        await _mark_batch_failed(session_factory, batch_id)
        raise

    for outcome in outcomes.values():
        warnings.extend(outcome.warnings)
        rejections.extend(outcome.rejections)

    any_rejected = any(o.counts.rejected > 0 for o in outcomes.values())
    if payload.errors:
        status = "completed_with_source_errors"
    elif any_rejected:
        status = "completed_with_rejections"
    else:
        status = "completed"

    counts = {name: outcome.counts for name, outcome in outcomes.items()}
    async with session_factory() as session:
        async with session.begin():
            batch = await session.get(IngestionBatch, batch_id)
            assert batch is not None
            batch.status = status
            batch.glucose_inserted = counts["glucose_samples"].inserted
            batch.glucose_updated = counts["glucose_samples"].updated
            batch.glucose_unchanged = counts["glucose_samples"].unchanged
            batch.glucose_rejected = counts["glucose_samples"].rejected
            batch.workouts_inserted = counts["workouts"].inserted
            batch.workouts_updated = counts["workouts"].updated
            batch.workouts_unchanged = counts["workouts"].unchanged
            batch.workouts_rejected = counts["workouts"].rejected
            batch.sleep_inserted = counts["sleep_sessions"].inserted
            batch.sleep_updated = counts["sleep_sessions"].updated
            batch.sleep_unchanged = counts["sleep_sessions"].unchanged
            batch.sleep_rejected = counts["sleep_sessions"].rejected
            batch.weight_inserted = counts["weight_measurements"].inserted
            batch.weight_updated = counts["weight_measurements"].updated
            batch.weight_unchanged = counts["weight_measurements"].unchanged
            batch.weight_rejected = counts["weight_measurements"].rejected
            batch.meals_inserted = counts["meal_events"].inserted
            batch.meals_updated = counts["meal_events"].updated
            batch.meals_unchanged = counts["meal_events"].unchanged
            batch.meals_rejected = counts["meal_events"].rejected

    logger.info(
        "ingestion_complete request_id=%s batch_id=%s status=%s",
        request_id,
        batch_id,
        status,
    )

    return IngestBatchResponse(
        batch_id=batch_id,
        request_id=request_id,
        status=status,
        schema_version=payload.schema_version,
        source_window=SourceWindow(
            data_start=payload.data_start,
            data_end=payload.data_end,
        ),
        results=IngestResults(
            glucose_samples=counts["glucose_samples"],
            workouts=counts["workouts"],
            sleep_sessions=counts["sleep_sessions"],
            weight_measurements=counts["weight_measurements"],
            meal_events=counts["meal_events"],
        ),
        warnings=warnings,
        rejections=rejections,
    )
