"""Batch ingestion with idempotent bulk upserts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.sql import dialect_name
from app.models import (
    GlucoseSample,
    HealthSource,
    MealEvent,
    SleepSession,
    SyncState,
    User,
    WeightMeasurement,
    Workout,
)
from app.schemas.ingest import (
    BatchIngestRequest,
    BatchIngestResponse,
    EntityUpsertCounts,
    GlucoseIngest,
    MealIngest,
    SleepIngest,
    SyncStateIngest,
    WeightIngest,
    WorkoutIngest,
)

UPSERT_CHUNK_SIZE = 500
SAMPLE_CONFLICT_COLS = ["user_id", "source_id", "source_sample_id"]
SYNC_CONFLICT_COLS = ["user_id", "entity_type", "source_name"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


async def get_or_create_user(db: AsyncSession, external_id: str) -> User:
    result = await db.execute(select(User).where(User.external_identifier == external_id))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(external_identifier=external_id)
    db.add(user)
    await db.flush()
    return user


async def get_or_create_source(
    db: AsyncSession, user_id: int, source_name: str, source_type: str = "app"
) -> HealthSource:
    result = await db.execute(
        select(HealthSource).where(
            HealthSource.user_id == user_id,
            HealthSource.source_name == source_name,
        )
    )
    source = result.scalar_one_or_none()
    if source:
        return source
    source = HealthSource(user_id=user_id, source_name=source_name, source_type=source_type)
    db.add(source)
    await db.flush()
    return source


async def resolve_sources_for_batch(
    db: AsyncSession,
    user_id: int,
    items: Sequence[Any],
) -> dict[str, HealthSource]:
    """Resolve all distinct source names for a batch in one query (+ creates)."""
    specs: dict[str, str] = {}
    for item in items:
        if item.source_name not in specs:
            specs[item.source_name] = getattr(item, "source_type", "app")
    if not specs:
        return {}

    result = await db.execute(
        select(HealthSource).where(
            HealthSource.user_id == user_id,
            HealthSource.source_name.in_(list(specs.keys())),
        )
    )
    sources = {s.source_name: s for s in result.scalars().all()}
    created = False
    for name, source_type in specs.items():
        if name not in sources:
            source = HealthSource(user_id=user_id, source_name=name, source_type=source_type)
            db.add(source)
            sources[name] = source
            created = True
    if created:
        await db.flush()
    return sources


async def _count_existing_keys(
    db: AsyncSession,
    model: type[DeclarativeBase],
    conflict_cols: list[str],
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    cols = [getattr(model, c) for c in conflict_cols]
    key_tuples = [tuple(r[c] for c in conflict_cols) for r in rows]
    if len(conflict_cols) == 1:
        result = await db.execute(
            select(func.count()).select_from(model).where(cols[0].in_([k[0] for k in key_tuples]))
        )
        return int(result.scalar_one())

    # Prefer OR of ANDs — portable across SQLite and Postgres for composite keys.
    conditions = [
        and_(*[col == val for col, val in zip(cols, key, strict=True)]) for key in key_tuples
    ]
    result = await db.execute(
        select(func.count()).select_from(model).where(or_(*conditions))
    )
    return int(result.scalar_one())


async def bulk_upsert(
    db: AsyncSession,
    model: type[DeclarativeBase],
    rows: list[dict[str, Any]],
    conflict_cols: list[str],
    update_cols: list[str],
) -> EntityUpsertCounts:
    """Dialect-native bulk upsert with inserted/updated/tombstoned counts."""
    if not rows:
        return EntityUpsertCounts()

    tombstoned = sum(1 for r in rows if r.get("deleted_at") is not None)
    dialect = dialect_name(db)
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert

    existing_count = 0
    if dialect != "postgresql":
        existing_count = await _count_existing_keys(db, model, conflict_cols, rows)

    inserted = 0
    updated = 0

    for chunk in _chunks(rows, UPSERT_CHUNK_SIZE):
        # Use __table__ — a column named "metadata" collides with ORM MetaData on insert(Model).
        stmt = insert_fn(model.__table__).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={col: stmt.excluded[col] for col in update_cols},
        )
        if dialect == "postgresql":
            stmt = stmt.returning(literal_column("(xmax = 0)").label("was_inserted"))
            result = await db.execute(stmt)
            for row in result:
                if row.was_inserted:
                    inserted += 1
                else:
                    updated += 1
        else:
            await db.execute(stmt)

    if dialect != "postgresql":
        updated = min(existing_count, len(rows))
        inserted = len(rows) - updated

    return EntityUpsertCounts(inserted=inserted, updated=updated, tombstoned=tombstoned)


async def _upsert_glucose(
    db: AsyncSession, user: User, items: list[GlucoseIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    sources = await resolve_sources_for_batch(db, user.id, items)
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "source_id": sources[item.source_name].id,
            "source_sample_id": item.source_sample_id,
            "sample_time": item.sample_time,
            "value": item.value,
            "unit": item.unit,
            "trend": item.trend,
            "metadata": item.metadata,
            "deleted_at": item.deleted_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        GlucoseSample,
        rows,
        SAMPLE_CONFLICT_COLS,
        ["sample_time", "value", "unit", "trend", "metadata", "deleted_at", "updated_at"],
    )


async def _upsert_workouts(
    db: AsyncSession, user: User, items: list[WorkoutIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    sources = await resolve_sources_for_batch(db, user.id, items)
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "source_id": sources[item.source_name].id,
            "source_sample_id": item.source_sample_id,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "sport": item.sport,
            "distance_m": item.distance_m,
            "active_energy_kcal": item.active_energy_kcal,
            "avg_hr": item.avg_hr,
            "max_hr": item.max_hr,
            "metadata": item.metadata,
            "deleted_at": item.deleted_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        Workout,
        rows,
        SAMPLE_CONFLICT_COLS,
        [
            "start_time",
            "end_time",
            "sport",
            "distance_m",
            "active_energy_kcal",
            "avg_hr",
            "max_hr",
            "metadata",
            "deleted_at",
            "updated_at",
        ],
    )


async def _upsert_sleep(
    db: AsyncSession, user: User, items: list[SleepIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    sources = await resolve_sources_for_batch(db, user.id, items)
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "source_id": sources[item.source_name].id,
            "source_sample_id": item.source_sample_id,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "duration_s": item.duration_s
            or int((item.end_time - item.start_time).total_seconds()),
            "sleep_stage_summary": item.sleep_stage_summary,
            "metadata": item.metadata,
            "deleted_at": item.deleted_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        SleepSession,
        rows,
        SAMPLE_CONFLICT_COLS,
        [
            "start_time",
            "end_time",
            "duration_s",
            "sleep_stage_summary",
            "metadata",
            "deleted_at",
            "updated_at",
        ],
    )


async def _upsert_weight(
    db: AsyncSession, user: User, items: list[WeightIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    sources = await resolve_sources_for_batch(db, user.id, items)
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "source_id": sources[item.source_name].id,
            "source_sample_id": item.source_sample_id,
            "measured_at": item.measured_at,
            "value": item.value,
            "unit": item.unit,
            "metadata": item.metadata,
            "deleted_at": item.deleted_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        WeightMeasurement,
        rows,
        SAMPLE_CONFLICT_COLS,
        ["measured_at", "value", "unit", "metadata", "deleted_at", "updated_at"],
    )


async def _upsert_meals(
    db: AsyncSession, user: User, items: list[MealIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    sources = await resolve_sources_for_batch(db, user.id, items)
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "source_id": sources[item.source_name].id,
            "source_sample_id": item.source_sample_id,
            "meal_start": item.meal_start,
            "meal_end": item.meal_end,
            "meal_completed_at": item.meal_completed_at,
            "notes": item.notes,
            "foods": item.foods,
            "metadata": item.metadata,
            "deleted_at": item.deleted_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        MealEvent,
        rows,
        SAMPLE_CONFLICT_COLS,
        [
            "meal_start",
            "meal_end",
            "meal_completed_at",
            "notes",
            "foods",
            "metadata",
            "deleted_at",
            "updated_at",
        ],
    )


async def _upsert_sync_state(
    db: AsyncSession, user: User, items: list[SyncStateIngest]
) -> EntityUpsertCounts:
    if not items:
        return EntityUpsertCounts()
    now = _now()
    rows = [
        {
            "user_id": user.id,
            "entity_type": item.entity_type,
            "source_name": item.source_name,
            "anchor": item.anchor,
            "last_synced_at": item.last_synced_at,
            "last_seen_at": item.last_seen_at,
            "updated_at": now,
        }
        for item in items
    ]
    return await bulk_upsert(
        db,
        SyncState,
        rows,
        SYNC_CONFLICT_COLS,
        ["anchor", "last_synced_at", "last_seen_at", "updated_at"],
    )


async def ingest_batch(db: AsyncSession, payload: BatchIngestRequest) -> BatchIngestResponse:
    user = await get_or_create_user(db, payload.user_id)
    return BatchIngestResponse(
        user_id=payload.user_id,
        glucose_samples=await _upsert_glucose(db, user, payload.glucose_samples),
        workouts=await _upsert_workouts(db, user, payload.workouts),
        sleep_sessions=await _upsert_sleep(db, user, payload.sleep_sessions),
        weight_measurements=await _upsert_weight(db, user, payload.weight_measurements),
        meal_events=await _upsert_meals(db, user, payload.meal_events),
        sync_state=await _upsert_sync_state(db, user, payload.sync_state),
    )
