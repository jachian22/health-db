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

# asyncpg caps bind parameters per statement at 32767 (int16). Chunk bulk
# inserts well below that so a full HealthKit export cannot overflow it.
MAX_BIND_PARAMS_PER_STATEMENT = 20_000

# Error types raised as PydanticCustomError by the export schemas.
# ValidationError.errors()[n]["type"] carries them directly — no parsing.
_REJECTION_CODES = frozenset(
    {
        "INVALID_UNIT",
        "INVALID_TIMESTAMP",
        "INVALID_WORKOUT",
        "UNSUPPORTED_WORKOUT_SOURCE",
        "INVALID_REQUEST",
    }
)

_APP_SOURCE_NAMES = frozenset({"Stelo", "Strava", "Health"})

# Maps response entity keys to the column prefixes on ingestion_batches
# (e.g. sleep_sessions -> sleep_inserted / sleep_updated / ...).
_BATCH_COLUMN_PREFIXES = {
    "glucose_samples": "glucose",
    "workouts": "workouts",
    "sleep_sessions": "sleep",
    "weight_measurements": "weight",
    "meal_events": "meals",
}
_COUNT_METRICS = ("inserted", "updated", "unchanged", "rejected")


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
    if not errors:
        code, message = "INVALID_REQUEST", "Record failed validation"
    else:
        err = errors[0]
        err_type = str(err.get("type", ""))
        if err_type in _REJECTION_CODES:
            code, message = err_type, str(err.get("msg", ""))
        elif err_type == "extra_forbidden":
            # Strict contract fields (e.g. meal_start / meal_end on meals).
            loc = err.get("loc") or ()
            field_name = loc[-1] if loc else "field"
            code = "INVALID_REQUEST"
            message = f"Unexpected field '{field_name}' is not allowed"
        else:
            # Built-in Pydantic errors (missing, greater_than, …).
            code, message = "INVALID_REQUEST", str(err.get("msg", ""))
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
        if user is None:
            raise RuntimeError(
                f"User row {inserted} vanished within its own transaction"
            )
        return user

    # Another request inserted the user between our select and insert.
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
    """Idempotently resolve/create a health_sources catalog row.

    Single round-trip upsert. Postgres unique constraints treat NULLs as
    distinct, so NULL source_name rows are covered by a partial unique
    index (migration 002) and the conflict target is chosen per case.
    DO UPDATE keeps an existing source_type when the new one is NULL,
    and always RETURNs the row id.
    """
    table = HealthSource.__table__
    stmt = pg_insert(table).values(
        {
            "user_id": user_id,
            "source": source,
            "source_name": source_name,
            "source_type": _infer_source_type(source, source_name),
            "metadata": {},
        }
    )
    set_ = {
        "source_type": sa.func.coalesce(
            stmt.excluded.source_type, table.c.source_type
        )
    }
    if source_name is None:
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "source"],
            index_where=table.c.source_name.is_(None),
            set_=set_,
        )
    else:
        stmt = stmt.on_conflict_do_update(
            constraint="uq_health_sources_user_id_source_source_name",
            set_=set_,
        )
    return (await session.execute(stmt.returning(table.c.id))).scalar_one()


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
            # Resolve each distinct (source, source_name) catalog row once per
            # batch instead of once per record.
            source_cache: dict[tuple[str, str | None], uuid.UUID] = {}
            rows: list[dict[str, Any]] = []
            for item in deduped.values():
                cache_key = (item.source, item.source_name)
                health_source_id = source_cache.get(cache_key)
                if health_source_id is None:
                    health_source_id = await _resolve_health_source(
                        session,
                        user_id=user_id,
                        source=item.source,
                        source_name=item.source_name,
                    )
                    source_cache[cache_key] = health_source_id
                rows.append(spec.row_builder(item, user_id, health_source_id))

            table = sa.inspect(spec.model).local_table
            flags: list[bool] = []
            # asyncpg limits bind parameters per statement; chunk large batches
            # so a full HealthKit export cannot overflow it.
            params_per_row = len(rows[0])
            chunk_size = max(1, MAX_BIND_PARAMS_PER_STATEMENT // params_per_row)
            for start in range(0, len(rows), chunk_size):
                chunk = rows[start : start + chunk_size]
                stmt = pg_insert(table).values(chunk)
                excluded = stmt.excluded
                set_: dict[str, Any] = {
                    col: excluded[col] for col in spec.meaningful_columns
                }
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
                ).returning(
                    # xmax is a Postgres MVCC system column: 0 means the row
                    # version was created by this statement (insert); non-zero
                    # means an existing row was updated. This is a widely used
                    # but implementation-specific trick — revisit if Postgres
                    # storage semantics ever change.
                    sa.literal_column("(xmax = 0)").label("was_inserted")
                )

                result = await session.execute(stmt)
                flags.extend(row.was_inserted for row in result)

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
                if batch is None:
                    raise RuntimeError(
                        f"Ingestion batch {batch_id} disappeared before finalization"
                    )
                batch.status = status
                for entity_type, prefix in _BATCH_COLUMN_PREFIXES.items():
                    entity_counts = counts[entity_type]
                    for metric in _COUNT_METRICS:
                        setattr(
                            batch,
                            f"{prefix}_{metric}",
                            getattr(entity_counts, metric),
                        )
                if any_rejected:
                    batch.error_summary = {
                        "rejected_total": sum(o.counts.rejected for o in outcomes.values()),
                        "rejections_truncated": truncated,
                    }

        latency_ms = int((time.perf_counter() - started) * 1000)
        log_fields: dict[str, Any] = {
            "request_id": request_id,
            "ingestion_batch_id": batch_id,
            "status": status,
            "schema_version": payload.schema_version,
            "data_start": payload.data_start.isoformat(),
            "data_end": payload.data_end.isoformat(),
            "latency_ms": latency_ms,
        }
        for entity_type, prefix in _BATCH_COLUMN_PREFIXES.items():
            entity_counts = counts[entity_type]
            log_fields[f"{prefix}_received"] = entity_counts.received
            for metric in _COUNT_METRICS:
                log_fields[f"{prefix}_{metric}"] = getattr(entity_counts, metric)
        logger.info(
            "ingestion_complete %s",
            " ".join(f"{key}={value}" for key, value in log_fields.items()),
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
        # from None: the original exception is already logged server-side;
        # do not chain driver details into the sanitized client error.
        raise AppError(
            code="INGESTION_FAILED",
            message="Ingestion could not be completed",
            status_code=500,
        ) from None
