"""Build an immutable Parquet snapshot from the Health Query API."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow as pa
import pyarrow.parquet as pq

from health_ml import SCHEMA_VERSION, __version__
from health_ml.clients.health_api import GLUCOSE_SERIES_PATH, HealthAPIClient, HealthDataClient
from health_ml.config import (
    DEFAULT_QUERY_TIMEZONE,
    GLUCOSE_SANITY_MAX_MG_DL,
    GLUCOSE_SANITY_MIN_MG_DL,
    QUERY_API_CONTRACT,
    RANGE_SEMANTICS,
    Settings,
)
from health_ml.datasets.manifest import (
    SNAPSHOT_FILES,
    ArtifactRef,
    CategoryDiagnosticCounts,
    SnapshotDiagnosticsManifest,
    SnapshotManifest,
    SnapshotRequest,
    sha256_file,
    write_manifest,
)
from health_ml.errors import SnapshotError, SnapshotExistsError, SnapshotValidationError
from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)
from health_ml.times import interval_extends_beyond_bounds, point_in_range, require_aware_range

UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

GLUCOSE_ARROW_SCHEMA = pa.schema(
    [
        ("timestamp", UTC_TIMESTAMP),
        ("glucose_mg_dl", pa.float64()),
    ]
)
MEALS_ARROW_SCHEMA = pa.schema(
    [
        ("meal_id", pa.string()),
        ("timestamp", UTC_TIMESTAMP),
        ("foods", pa.list_(pa.string())),
        ("source", pa.string()),
    ]
)
WORKOUTS_ARROW_SCHEMA = pa.schema(
    [
        ("workout_id", pa.string()),
        ("start", UTC_TIMESTAMP),
        ("end", UTC_TIMESTAMP),
        ("sport", pa.string()),
        ("distance_meters", pa.float64()),
        ("source", pa.string()),
    ]
)
SLEEP_ARROW_SCHEMA = pa.schema(
    [
        ("sleep_id", pa.string()),
        ("start", UTC_TIMESTAMP),
        ("end", UTC_TIMESTAMP),
        ("stage", pa.string()),
        ("source", pa.string()),
    ]
)
WEIGHT_ARROW_SCHEMA = pa.schema(
    [
        ("weight_id", pa.string()),
        ("timestamp", UTC_TIMESTAMP),
        ("weight_kg", pa.float64()),
        ("source", pa.string()),
    ]
)


@dataclass(frozen=True)
class CategoryDiagnostics:
    rows: int
    extra: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotDiagnostics:
    glucose: CategoryDiagnostics
    meals: CategoryDiagnostics
    workouts: CategoryDiagnostics
    sleep: CategoryDiagnostics
    weight: CategoryDiagnostics

    def format(self) -> str:
        lines = [
            "Snapshot validation",
            "-------------------",
            *_category_block("Glucose", self.glucose, glucose=True),
            "",
            *_category_block("Meals", self.meals),
            "",
            *_category_block("Workouts", self.workouts),
            "",
            *_category_block("Sleep", self.sleep),
            "",
            *_category_block("Weight", self.weight),
        ]
        warnings = self.warning_messages()
        if warnings:
            lines.extend(["", "Warnings:", *[f"  - {item}" for item in warnings]])
        return "\n".join(lines)

    def warning_messages(self) -> list[str]:
        warnings: list[str] = []
        for name, category in (
            ("glucose", self.glucose),
            ("meals", self.meals),
            ("workouts", self.workouts),
            ("sleep", self.sleep),
            ("weight", self.weight),
        ):
            if category.rows == 0:
                warnings.append(f"{name} category is empty")
        gaps_15 = self.glucose.extra.get("gaps_over_15m", 0)
        gaps_60 = self.glucose.extra.get("gaps_over_60m", 0)
        if gaps_15:
            warnings.append(f"{gaps_15} CGM gap(s) greater than 15 minutes")
        if gaps_60:
            warnings.append(f"{gaps_60} CGM gap(s) greater than 60 minutes")
        if self.glucose.extra.get("duplicate_timestamps", 0):
            warnings.append("duplicate glucose timestamps kept as recorded")
        if self.glucose.extra.get("out_of_range", 0):
            warnings.append("glucose values outside 20–600 mg/dL kept as recorded")
        return warnings

    def to_manifest(self) -> SnapshotDiagnosticsManifest:
        return SnapshotDiagnosticsManifest(
            glucose=_counts(self.glucose),
            meals=_counts(self.meals),
            workouts=_counts(self.workouts),
            sleep=_counts(self.sleep),
            weight=_counts(self.weight),
        )


def _counts(category: CategoryDiagnostics) -> CategoryDiagnosticCounts:
    extra = category.extra
    return CategoryDiagnosticCounts(
        rows=category.rows,
        duplicate_timestamps=extra.get("duplicate_timestamps", 0),
        duplicate_ids=extra.get("duplicate_ids", 0),
        out_of_window=extra.get("out_of_window", 0),
        gaps_over_15m=extra.get("gaps_over_15m", 0),
        gaps_over_60m=extra.get("gaps_over_60m", 0),
        out_of_range=extra.get("out_of_range", 0),
    )


def _category_block(
    title: str,
    category: CategoryDiagnostics,
    *,
    glucose: bool = False,
) -> list[str]:
    lines = [
        f"{title}:",
        f"  rows: {category.rows:,}",
        f"  duplicate timestamps: {category.extra.get('duplicate_timestamps', 0)}",
        f"  duplicate ids: {category.extra.get('duplicate_ids', 0)}",
        f"  out of window: {category.extra.get('out_of_window', 0)}",
    ]
    if glucose:
        lines.append(f"  gaps > 15m: {category.extra.get('gaps_over_15m', 0)}")
        lines.append(f"  gaps > 60m: {category.extra.get('gaps_over_60m', 0)}")
        lines.append(f"  out of range: {category.extra.get('out_of_range', 0)}")
    return lines


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    output_dir: Path
    manifest: SnapshotManifest
    diagnostics: SnapshotDiagnostics
    glucose: list[GlucoseRecord]
    meals: list[MealRecord]
    workouts: list[WorkoutRecord]
    sleep: list[SleepInterval]
    weight: list[WeightRecord]


def snapshot_id_for(start: datetime, end: datetime, schema_version: str = SCHEMA_VERSION) -> str:
    return f"v{schema_version}_{_compact(start)}_{_compact(end)}"


def _compact(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def sort_glucose(records: Sequence[GlucoseRecord]) -> list[GlucoseRecord]:
    return sorted(records, key=lambda row: (row.timestamp, round(row.glucose_mg_dl, 6)))


def sort_meals(records: Sequence[MealRecord]) -> list[MealRecord]:
    return sorted(records, key=lambda row: (row.timestamp, row.meal_id))


def sort_workouts(records: Sequence[WorkoutRecord]) -> list[WorkoutRecord]:
    return sorted(records, key=lambda row: (row.start, row.end, row.workout_id))


def sort_sleep(records: Sequence[SleepInterval]) -> list[SleepInterval]:
    return sorted(records, key=lambda row: (row.start, row.end, row.sleep_id))


def sort_weight(records: Sequence[WeightRecord]) -> list[WeightRecord]:
    return sorted(records, key=lambda row: (row.timestamp, row.weight_id))


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SnapshotError(f"timezone must be a valid IANA timezone name: {name}") from exc
    return name


def point_out_of_window(timestamp: datetime, start: datetime, end: datetime) -> bool:
    return not point_in_range(timestamp, start, end)


def interval_out_of_window(
    interval_start: datetime,
    interval_end: datetime,
    start: datetime,
    end: datetime,
) -> bool:
    return interval_extends_beyond_bounds(interval_start, interval_end, start, end)


def validate_snapshot_records(
    *,
    start: datetime,
    end: datetime,
    glucose: Sequence[GlucoseRecord],
    meals: Sequence[MealRecord],
    workouts: Sequence[WorkoutRecord],
    sleep: Sequence[SleepInterval],
    weight: Sequence[WeightRecord],
) -> SnapshotDiagnostics:
    problems: list[str] = []

    meal_ids = [row.meal_id for row in meals]
    if len(meal_ids) != len(set(meal_ids)):
        problems.append("duplicate meal_id values")

    workout_ids = [row.workout_id for row in workouts]
    if len(workout_ids) != len(set(workout_ids)):
        problems.append("duplicate workout_id values")

    sleep_ids = [row.sleep_id for row in sleep]
    if len(sleep_ids) != len(set(sleep_ids)):
        problems.append("duplicate sleep_id values")

    weight_ids = [row.weight_id for row in weight]
    if len(weight_ids) != len(set(weight_ids)):
        problems.append("duplicate weight_id values")

    diagnostics = SnapshotDiagnostics(
        glucose=_glucose_diagnostics(glucose, start, end),
        meals=CategoryDiagnostics(
            rows=len(meals),
            extra={
                "out_of_window": sum(
                    1 for row in meals if point_out_of_window(row.timestamp, start, end)
                ),
            },
        ),
        workouts=CategoryDiagnostics(
            rows=len(workouts),
            extra={
                "out_of_window": sum(
                    1
                    for row in workouts
                    if interval_out_of_window(row.start, row.end, start, end)
                ),
            },
        ),
        sleep=CategoryDiagnostics(
            rows=len(sleep),
            extra={
                "out_of_window": sum(
                    1
                    for row in sleep
                    if interval_out_of_window(row.start, row.end, start, end)
                ),
            },
        ),
        weight=CategoryDiagnostics(
            rows=len(weight),
            extra={
                "out_of_window": sum(
                    1 for row in weight if point_out_of_window(row.timestamp, start, end)
                ),
            },
        ),
    )
    if problems:
        raise SnapshotValidationError(
            "Snapshot validation failed:\n" + "\n".join(f"  - {item}" for item in problems),
            problems,
        )
    return diagnostics


def _glucose_diagnostics(
    records: Sequence[GlucoseRecord],
    start: datetime,
    end: datetime,
) -> CategoryDiagnostics:
    timestamps = [row.timestamp for row in records]
    duplicate_timestamps = len(timestamps) - len(set(timestamps))
    gaps_over_15m = 0
    gaps_over_60m = 0
    for previous, current in zip(timestamps, timestamps[1:], strict=False):
        delta = current - previous
        if delta > timedelta(minutes=15):
            gaps_over_15m += 1
        if delta > timedelta(minutes=60):
            gaps_over_60m += 1
    out_of_range = sum(
        1
        for row in records
        if not (GLUCOSE_SANITY_MIN_MG_DL <= row.glucose_mg_dl <= GLUCOSE_SANITY_MAX_MG_DL)
    )
    return CategoryDiagnostics(
        rows=len(records),
        extra={
            "duplicate_timestamps": duplicate_timestamps,
            "out_of_window": sum(
                1 for row in records if point_out_of_window(row.timestamp, start, end)
            ),
            "gaps_over_15m": gaps_over_15m,
            "gaps_over_60m": gaps_over_60m,
            "out_of_range": out_of_range,
        },
    )


def _table(schema: pa.Schema, rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def glucose_table(records: Sequence[GlucoseRecord]) -> pa.Table:
    return _table(
        GLUCOSE_ARROW_SCHEMA,
        [
            {
                "timestamp": row.timestamp,
                "glucose_mg_dl": row.glucose_mg_dl,
            }
            for row in records
        ],
    )


def meals_table(records: Sequence[MealRecord]) -> pa.Table:
    return _table(
        MEALS_ARROW_SCHEMA,
        [
            {
                "meal_id": row.meal_id,
                "timestamp": row.timestamp,
                "foods": list(row.foods),
                "source": row.source,
            }
            for row in records
        ],
    )


def workouts_table(records: Sequence[WorkoutRecord]) -> pa.Table:
    return _table(
        WORKOUTS_ARROW_SCHEMA,
        [
            {
                "workout_id": row.workout_id,
                "start": row.start,
                "end": row.end,
                "sport": row.sport,
                "distance_meters": row.distance_meters,
                "source": row.source,
            }
            for row in records
        ],
    )


def sleep_table(records: Sequence[SleepInterval]) -> pa.Table:
    return _table(
        SLEEP_ARROW_SCHEMA,
        [
            {
                "sleep_id": row.sleep_id,
                "start": row.start,
                "end": row.end,
                "stage": row.stage,
                "source": row.source,
            }
            for row in records
        ],
    )


def weight_table(records: Sequence[WeightRecord]) -> pa.Table:
    return _table(
        WEIGHT_ARROW_SCHEMA,
        [
            {
                "weight_id": row.weight_id,
                "timestamp": row.timestamp,
                "weight_kg": row.weight_kg,
                "source": row.source,
            }
            for row in records
        ],
    )


def records_from_glucose_table(table: pa.Table) -> list[GlucoseRecord]:
    return [
        GlucoseRecord(
            timestamp=_arrow_datetime(row["timestamp"]),
            glucose_mg_dl=row["glucose_mg_dl"],
        )
        for row in table.to_pylist()
    ]


def records_from_meals_table(table: pa.Table) -> list[MealRecord]:
    return [
        MealRecord(
            meal_id=row["meal_id"],
            timestamp=_arrow_datetime(row["timestamp"]),
            foods=list(row["foods"] or []),
            source=row["source"],
        )
        for row in table.to_pylist()
    ]


def records_from_workouts_table(table: pa.Table) -> list[WorkoutRecord]:
    return [
        WorkoutRecord(
            workout_id=row["workout_id"],
            start=_arrow_datetime(row["start"]),
            end=_arrow_datetime(row["end"]),
            sport=row["sport"],
            distance_meters=row["distance_meters"],
            source=row["source"],
        )
        for row in table.to_pylist()
    ]


def records_from_sleep_table(table: pa.Table) -> list[SleepInterval]:
    return [
        SleepInterval(
            sleep_id=row["sleep_id"],
            start=_arrow_datetime(row["start"]),
            end=_arrow_datetime(row["end"]),
            stage=row["stage"],
            source=row["source"],
        )
        for row in table.to_pylist()
    ]


def records_from_weight_table(table: pa.Table) -> list[WeightRecord]:
    return [
        WeightRecord(
            weight_id=row["weight_id"],
            timestamp=_arrow_datetime(row["timestamp"]),
            weight_kg=row["weight_kg"],
            source=row["source"],
        )
        for row in table.to_pylist()
    ]


def _arrow_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_snapshot(
    start: datetime,
    end: datetime,
    output_dir: Path,
    *,
    client: HealthDataClient | None = None,
    settings: Settings | None = None,
    overwrite: bool = False,
    timezone: str = DEFAULT_QUERY_TIMEZONE,
    created_at: datetime | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> SnapshotResult:
    """Extract canonical records and write an immutable snapshot directory."""
    start_utc, end_utc = require_aware_range(start, end)
    tz_name = validate_timezone(timezone)
    snapshot_id = snapshot_id_for(start_utc, end_utc)
    final_dir = Path(output_dir) / snapshot_id
    if final_dir.exists() and not overwrite:
        raise SnapshotExistsError(
            f"Snapshot already exists: {final_dir}. Pass overwrite=True to replace it."
        )

    owns_client = client is None
    if client is None:
        resolved_settings = settings or Settings()
        client = HealthAPIClient(resolved_settings, timezone=tz_name)
        settings = resolved_settings

    log = progress or (lambda _phase, _name: None)
    try:
        log("fetch", "glucose")
        glucose = sort_glucose(client.get_glucose(start_utc, end_utc))
        log("ok", "glucose")
        log("fetch", "meals")
        meals = sort_meals(client.get_meals(start_utc, end_utc))
        log("ok", "meals")
        log("fetch", "workouts")
        workouts = sort_workouts(client.get_workouts(start_utc, end_utc))
        log("ok", "workouts")
        log("fetch", "sleep")
        sleep = sort_sleep(client.get_sleep(start_utc, end_utc))
        log("ok", "sleep")
        log("fetch", "weight")
        weight = sort_weight(client.get_weight(start_utc, end_utc))
        log("ok", "weight")
    finally:
        if owns_client:
            client.close()

    diagnostics = validate_snapshot_records(
        start=start_utc,
        end=end_utc,
        glucose=glucose,
        meals=meals,
        workouts=workouts,
        sleep=sleep,
        weight=weight,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / f".{snapshot_id}.partial"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    tables = {
        "glucose": glucose_table(glucose),
        "meals": meals_table(meals),
        "workouts": workouts_table(workouts),
        "sleep": sleep_table(sleep),
        "weight": weight_table(weight),
    }
    files: dict[str, str] = {}
    checksums: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    try:
        for name in SNAPSHOT_FILES:
            filename = f"{name}.parquet"
            path = tmp_dir / filename
            log("write", filename)
            pq.write_table(tables[name], path, compression="zstd")
            files[name] = filename
            checksums[name] = sha256_file(path)
            row_counts[name] = tables[name].num_rows
            log("ok", filename)

        created = created_at or datetime.now(tz=UTC)
        if created.tzinfo is None:
            raise SnapshotError("created_at must be timezone-aware")
        api_url = client.api_base_url
        if not api_url and settings is not None:
            api_url = settings.health_api_url_str
        request = SnapshotRequest(
            source_start=start_utc,
            source_end=end_utc,
            timezone=tz_name,
            range_semantics=RANGE_SEMANTICS,
            schema_version=SCHEMA_VERSION,
            extractor_package="health-ml",
            extractor_version=__version__,
            api_contract=QUERY_API_CONTRACT,
            api_url=api_url,
            glucose_path=GLUCOSE_SERIES_PATH,
            glucose_resolution="raw",
        )
        artifacts = {
            name: ArtifactRef(file=files[name], sha256=checksums[name], rows=row_counts[name])
            for name in SNAPSHOT_FILES
        }
        manifest = SnapshotManifest(
            schema_version=SCHEMA_VERSION,
            created_at=created.astimezone(UTC),
            request=request,
            artifacts=artifacts,
            diagnostics=diagnostics.to_manifest(),
            files=files,
            row_counts=row_counts,
            checksums=checksums,
        )
        log("write", "manifest.json")
        write_manifest(tmp_dir / "manifest.json", manifest)
        log("ok", "manifest.json")

        if final_dir.exists():
            shutil.rmtree(final_dir)
        tmp_dir.rename(final_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return SnapshotResult(
        snapshot_id=snapshot_id,
        output_dir=final_dir,
        manifest=manifest,
        diagnostics=diagnostics,
        glucose=glucose,
        meals=meals,
        workouts=workouts,
        sleep=sleep,
        weight=weight,
    )
