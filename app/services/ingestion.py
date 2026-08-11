"""Ingestion service: validate, audit, and idempotently upsert typed health rows.

Upserts use PostgreSQL `INSERT ... ON CONFLICT ... DO UPDATE` against the
`(user_id, source, source_sample_id)` unique constraint. The DO UPDATE clause
carries an IS DISTINCT FROM guard over meaningful fields, so materially
identical replays touch nothing and are classified `unchanged`. `xmax = 0`
on the RETURNING rows distinguishes inserts from updates.

Soft-deleted rows are never revived in Phase 1.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
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

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import (
    GlucoseSample,
    HealthSource,
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
    IngestSummary,
)

logger = get_logger(__name__)

REJECTION_RETURN_CAP = 100
PERSONAL_PRIMARY = "personal-primary"

# Validators embed a machine-readable code prefix in their messages
# (e.g. ValueError("INVALID_UNIT: …")) so rejection codes are deterministic.
_REJECTION_CODE_RE = re.compile(
    r"(INVALID_UNIT|INVALID_TIMESTAMP|INVALID_SLEEP_STAGE|INVALID_WORKOUT|"
    r"UNSUPPORTED_WORKOUT_SOURCE|INVALID_REQUEST):\s*"
)

_APP_SOURCE_NAMES = frozenset({"Stelo", "Strava", "Health"})


@dataclass
class _EntityOutcome:
    counts: EntityResultCounts = field(default_factory=EntityResultCounts)
    rejections: list[IngestRejection] = field(default_factory=list)


@dataclass(frozen=True)
class _EntitySpec:
    """Everything the generic upsert needs to know about one entity type."""

    entity_type: str
    model: type
    schema: type[BaseModel]
    # Column names compared by IS DISTINCT FROM to decide updated vs unchanged.
    meaningful_columns: tuple[str, ...]
    # Validated item -> full column-name -> value row dict (identity + fields).
    row_builder: Callable[[Any, uuid.UUID, uuid.UUID | None], dict[str, Any]]


def _payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rejection_from(
    exc: ValidationError,
    entity_type: str,
    raw: dict[str, Any],
    index: int,
) -> IngestRejection:
    errors = exc.errors()
    raw_msg = str(errors[0]["msg"]) if errors else str(exc)
    match = _REJECTION_CODE_RE.search(raw_msg)
    code = match.group(1) if match else "INVALID_REQUEST"
    # Strip Pydantic's "Value error, " wrapper and our code prefix for readability.
    message = _REJECTION_CODE_RE.sub("", raw_msg).removeprefix("Value error, ").strip()
    message = message or raw_msg
    # Forbid / extra-field errors for meal_start / meal_end → clear contract message.
    if errors and errors[0].get("type") == "extra_forbidden":
        loc = errors[0].get("loc") or ()
        field = loc[-1] if loc else "field"
        code = "INVALID_REQUEST"
        message = f"Unexpected field '{field}' is not allowed"
    sample_id = raw.get("source_sample_id")
    return IngestRejection(
        entity_type=entity_type,
        index=index,
        source_sample_id=str(sample_id) if sample_id is not None else None,
        code=code,
        message=message,
    )


def _as_decimal(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _infer_source_type(source: str, source_name: str | None) -> str | None:
    if source == "manual":
        return "manual"
    if source_name in _APP_SOURCE_NAMES:
        return "app"
    return None


async def get_or_create_personal_user(session: AsyncSession) -> User:
    """Resolve the fixed personal-primary user, creating it if absent."""
    external_id = get_settings().primary_user_external_id or PERSONAL_PRIMARY
    result = await session.execute(
        select(User).where(User.external_identifier == external_id)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    stmt = (
        pg_insert(User)
        .values(external_identifier=external_id)
        .on_conflict_do_nothing(index_elements=["external_identifier"])
        .returning(User.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        user = await session.get(User, inserted)
        assert user is not None
        return user

    result = await session.execute(
        select(User).where(User.external_identifier == external_id)
    )
    user = result.scalar_one()
    return user


async def _resolve_health_source(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    source: str,
    source_name: str | None,
) -> uuid.UUID:
    """Idempotently resolve/create a health_sources catalog row."""
    source_type = _infer_source_type(source, source_name)
    conditions = [
        HealthSource.user_id == user_id,
        HealthSource.source == source,
    ]
    if source_name is None:
        conditions.append(HealthSource.source_name.is_(None))
    else:
        conditions.append(HealthSource.source_name == source_name)

    existing = (
        await session.execute(select(HealthSource).where(*conditions))
    ).scalar_one_or_none()
    if existing is not None:
        if source_type and existing.source_type != source_type:
            existing.source_type = source_type
        return existing.id

    stmt = (
        pg_insert(HealthSource.__table__)
        .values(
            {
                "user_id": user_id,
                "source": source,
                "source_name": source_name,
                "source_type": source_type,
                "metadata": {},
            }
        )
        .on_conflict_do_nothing(
            constraint="uq_health_sources_user_id_source_source_name"
        )
        .returning(HealthSource.__table__.c.id)
    )
    try:
        new_id = (await session.execute(stmt)).scalar_one_or_none()
    except Exception:
        # NULL source_name uniqueness is awkward in Postgres; fall back to select.
        new_id = None

    if new_id is not None:
        return new_id

    existing = (
        await session.execute(select(HealthSource).where(*conditions))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id

    # Last resort: insert without relying on ON CONFLICT (NULL source_name case).
    row = HealthSource(
        user_id=user_id,
        source=source,
        source_name=source_name,
        source_type=source_type,
        metadata_={},
    )
    session.add(row)
    await session.flush()
    return row.id


def _glucose_row(
    item: GlucoseSampleIn, user_id: uuid.UUID, health_source_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "health_source_id": health_source_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "sample_time": item.sample_time,
        "value_mg_dl": _as_decimal(item.value),
        "trend": item.trend,
        "metadata": item.metadata,
    }


def _workout_row(
    item: WorkoutIn, user_id: uuid.UUID, health_source_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "health_source_id": health_source_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "sport": item.sport,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "distance_meters": _as_decimal(item.distance_meters),
        "active_energy_kcal": _as_decimal(item.active_energy_kcal),
        "average_heart_rate": _as_decimal(item.average_heart_rate),
        "maximum_heart_rate": _as_decimal(item.maximum_heart_rate),
        "metadata": item.metadata,
    }


def _sleep_row(
    item: SleepIntervalIn, user_id: uuid.UUID, health_source_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "health_source_id": health_source_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "stage": item.stage,
        "metadata": item.metadata,
    }


def _weight_row(
    item: WeightMeasurementIn, user_id: uuid.UUID, health_source_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "health_source_id": health_source_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "measured_at": item.measured_at,
        "value_kg": _as_decimal(item.value),
        "metadata": item.metadata,
    }


def _meal_row(
    item: MealEventIn, user_id: uuid.UUID, health_source_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "health_source_id": health_source_id,
        "source": item.source,
        "source_name": item.source_name,
        "source_sample_id": item.source_sample_id,
        "meal_completed_at": item.meal_completed_at,
        "foods": item.foods,
        "notes": item.notes,
        "metadata": item.metadata,
    }


_ENTITY_SPECS: dict[str, _EntitySpec] = {
    "glucose_samples": _EntitySpec(
        entity_type="glucose_samples",
        model=GlucoseSample,
        schema=GlucoseSampleIn,
        meaningful_columns=(
            "sample_time",
            "value_mg_dl",
            "trend",
            "source_name",
            "metadata",
            "health_source_id",
        ),
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
            "health_source_id",
        ),
        row_builder=_workout_row,
    ),
    "sleep_sessions": _EntitySpec(
        entity_type="sleep_sessions",
        model=SleepInterval,
        schema=SleepIntervalIn,
        meaningful_columns=(
            "start_time",
            "end_time",
            "stage",
            "source_name",
            "metadata",
            "health_source_id",
        ),
        row_builder=_sleep_row,
    ),
    "weight_measurements": _EntitySpec(
        entity_type="weight_measurements",
        model=WeightMeasurement,
        schema=WeightMeasurementIn,
        meaningful_columns=(
            "measured_at",
            "value_kg",
            "source_name",
            "metadata",
            "health_source_id",
        ),
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
            "health_source_id",
        ),
        row_builder=_meal_row,
    ),
}


async def _upsert_entity(
    session_factory: async_sessionmaker[AsyncSession],
    spec: _EntitySpec,
    user_id: uuid.UUID,
    raw_items: list[dict[str, Any]],
) -> _EntityOutcome:
    outcome = _EntityOutcome()
    outcome.counts.received = len(raw_items)

    accepted: list[tuple[int, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            outcome.counts.rejected += 1
            outcome.rejections.append(
                IngestRejection(
                    entity_type=spec.entity_type,
                    index=index,
                    source_sample_id=None,
                    code="INVALID_REQUEST",
                    message="Record must be a JSON object",
                )
            )
            continue
        try:
            item = spec.schema.model_validate(raw)
        except ValidationError as exc:
            outcome.counts.rejected += 1
            outcome.rejections.append(
                _rejection_from(exc, spec.entity_type, raw, index)
            )
            continue
        accepted.append((index, item))

    if not accepted:
        return outcome

    # ON CONFLICT cannot update the same row twice in one statement, so
    # de-duplicate by identity, keeping the last occurrence (later wins).
    deduped: dict[tuple[str, str], Any] = {}
    for _index, item in accepted:
        deduped[(item.source, item.source_sample_id)] = item

    async with session_factory() as session:
        async with session.begin():
            rows: list[dict[str, Any]] = []
            for item in deduped.values():
                health_source_id = await _resolve_health_source(
                    session,
                    user_id=user_id,
                    source=item.source,
                    source_name=item.source_name,
                )
                rows.append(spec.row_builder(item, user_id, health_source_id))

            table = sa.inspect(spec.model).local_table
            stmt = pg_insert(table).values(rows)
            excluded = stmt.excluded
            set_: dict[str, Any] = {col: excluded[col] for col in spec.meaningful_columns}
            set_["updated_at"] = sa.func.now()

            # Only update live rows; never revive soft-deleted records.
            changed = sa.and_(
                table.c.deleted_at.is_(None),
                sa.or_(
                    *[
                        table.c[col].is_distinct_from(excluded[col])
                        for col in spec.meaningful_columns
                    ]
                ),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "source", "source_sample_id"],
                set_=set_,
                where=changed,
            ).returning(sa.literal_column("(xmax = 0)").label("was_inserted"))

            result = await session.execute(stmt)
            flags = [row.was_inserted for row in result]

    inserted = sum(1 for f in flags if f)
    outcome.counts.inserted = inserted
    outcome.counts.updated = len(flags) - inserted
    outcome.counts.unchanged = len(rows) - len(flags)
    return outcome


async def _mark_batch_status(
    session_factory: async_sessionmaker[AsyncSession],
    batch_id: uuid.UUID,
    *,
    status: str,
    error_summary: dict[str, Any] | None = None,
) -> None:
    try:
        async with session_factory() as session:
            async with session.begin():
                batch = await session.get(IngestionBatch, batch_id)
                if batch is not None:
                    batch.status = status
                    if error_summary is not None:
                        batch.error_summary = error_summary
    except Exception:
        logger.exception(
            "failed_to_update_batch_status batch_id=%s status=%s", batch_id, status
        )


def _truncate_rejections(
    rejections: list[IngestRejection],
) -> tuple[list[IngestRejection], bool]:
    if len(rejections) <= REJECTION_RETURN_CAP:
        return rejections, False
    return rejections[:REJECTION_RETURN_CAP], True


async def ingest_batch(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    payload: HealthExportPayload,
    request_id: uuid.UUID,
) -> IngestBatchResponse:
    started = time.perf_counter()
    payload.enforce_phase1_contract()
    raw_payload = payload.model_dump(mode="json")
    checksum = _payload_sha256(raw_payload)
    batch_id: uuid.UUID | None = None

    try:
        # Transaction 0: resolve user + create pending batch with raw payload copy.
        async with session_factory() as session:
            async with session.begin():
                user = await get_or_create_personal_user(session)
                batch = IngestionBatch(
                    user_id=user.id,
                    schema_version=payload.schema_version,
                    exported_at=payload.exported_at,
                    data_start=payload.data_start,
                    data_end=payload.data_end,
                    payload_sha256=checksum,
                    raw_payload=raw_payload,
                    status="received",
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

        outcomes: dict[str, _EntityOutcome] = {}
        for entity_type, items in entity_payloads.items():
            outcomes[entity_type] = await _upsert_entity(
                session_factory, _ENTITY_SPECS[entity_type], user_id, items
            )

        rejections: list[IngestRejection] = []
        for outcome in outcomes.values():
            rejections.extend(outcome.rejections)

        any_rejected = any(o.counts.rejected > 0 for o in outcomes.values())
        status = "partial" if any_rejected else "processed"

        counts = {name: outcome.counts for name, outcome in outcomes.items()}
        returned_rejections, truncated = _truncate_rejections(rejections)

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
                if any_rejected:
                    batch.error_summary = {
                        "rejected_total": sum(o.counts.rejected for o in outcomes.values()),
                        "rejections_truncated": truncated,
                    }

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ingestion_complete request_id=%s ingestion_batch_id=%s status=%s "
            "schema_version=%s data_start=%s data_end=%s latency_ms=%s "
            "glucose_received=%s glucose_inserted=%s glucose_updated=%s "
            "glucose_unchanged=%s glucose_rejected=%s "
            "workouts_received=%s workouts_inserted=%s workouts_updated=%s "
            "workouts_unchanged=%s workouts_rejected=%s "
            "sleep_received=%s sleep_inserted=%s sleep_updated=%s "
            "sleep_unchanged=%s sleep_rejected=%s "
            "weight_received=%s weight_inserted=%s weight_updated=%s "
            "weight_unchanged=%s weight_rejected=%s "
            "meals_received=%s meals_inserted=%s meals_updated=%s "
            "meals_unchanged=%s meals_rejected=%s",
            request_id,
            batch_id,
            status,
            payload.schema_version,
            payload.data_start.isoformat(),
            payload.data_end.isoformat(),
            latency_ms,
            counts["glucose_samples"].received,
            counts["glucose_samples"].inserted,
            counts["glucose_samples"].updated,
            counts["glucose_samples"].unchanged,
            counts["glucose_samples"].rejected,
            counts["workouts"].received,
            counts["workouts"].inserted,
            counts["workouts"].updated,
            counts["workouts"].unchanged,
            counts["workouts"].rejected,
            counts["sleep_sessions"].received,
            counts["sleep_sessions"].inserted,
            counts["sleep_sessions"].updated,
            counts["sleep_sessions"].unchanged,
            counts["sleep_sessions"].rejected,
            counts["weight_measurements"].received,
            counts["weight_measurements"].inserted,
            counts["weight_measurements"].updated,
            counts["weight_measurements"].unchanged,
            counts["weight_measurements"].rejected,
            counts["meal_events"].received,
            counts["meal_events"].inserted,
            counts["meal_events"].updated,
            counts["meal_events"].unchanged,
            counts["meal_events"].rejected,
        )

        return IngestBatchResponse(
            batch_id=batch_id,
            status=status,
            schema_version=payload.schema_version,
            data_start=payload.data_start,
            data_end=payload.data_end,
            summary=IngestSummary(
                glucose_samples=counts["glucose_samples"],
                workouts=counts["workouts"],
                sleep_sessions=counts["sleep_sessions"],
                weight_measurements=counts["weight_measurements"],
                meal_events=counts["meal_events"],
            ),
            rejections=returned_rejections,
            rejections_truncated=truncated,
        )
    except AppError:
        raise
    except Exception:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "ingestion_failed request_id=%s ingestion_batch_id=%s latency_ms=%s",
            request_id,
            batch_id,
            latency_ms,
        )
        if batch_id is not None:
            await _mark_batch_status(
                session_factory,
                batch_id,
                status="failed",
                error_summary={"code": "INGESTION_FAILED"},
            )
        raise AppError(
            code="INGESTION_FAILED",
            message="Ingestion could not be completed",
            status_code=500,
        )
