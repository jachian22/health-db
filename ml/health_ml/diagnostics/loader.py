"""Load and integrity-check an immutable Phase 0/1 snapshot. Never writes to it."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from health_ml import SCHEMA_VERSION
from health_ml.datasets.manifest import SNAPSHOT_FILES, SnapshotManifest, read_manifest, sha256_file
from health_ml.datasets.snapshot import (
    GLUCOSE_ARROW_SCHEMA,
    MEALS_ARROW_SCHEMA,
    SLEEP_ARROW_SCHEMA,
    WEIGHT_ARROW_SCHEMA,
    WORKOUTS_ARROW_SCHEMA,
    records_from_glucose_table,
    records_from_meals_table,
    records_from_sleep_table,
    records_from_weight_table,
    records_from_workouts_table,
    sort_glucose,
    sort_meals,
    sort_sleep,
    sort_weight,
    sort_workouts,
)
from health_ml.diagnostics.models import SortStatus
from health_ml.errors import DiagnosticsValidationError
from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)

REQUIRED_SNAPSHOT_FILES = tuple(f"{name}.parquet" for name in SNAPSHOT_FILES) + ("manifest.json",)
SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

_ARROW_SCHEMAS: dict[str, pa.Schema] = {
    "glucose": GLUCOSE_ARROW_SCHEMA,
    "meals": MEALS_ARROW_SCHEMA,
    "workouts": WORKOUTS_ARROW_SCHEMA,
    "sleep": SLEEP_ARROW_SCHEMA,
    "weight": WEIGHT_ARROW_SCHEMA,
}


@dataclass(frozen=True)
class LoadedCategory:
    name: str
    source_sort_status: SortStatus
    invalid_interval_count: int = 0


@dataclass(frozen=True)
class LoadedSnapshot:
    snapshot_dir: Path
    snapshot_id: str
    manifest: SnapshotManifest
    manifest_sha256: str
    glucose: tuple[GlucoseRecord, ...]
    meals: tuple[MealRecord, ...]
    workouts: tuple[WorkoutRecord, ...]
    sleep: tuple[SleepInterval, ...]
    weight: tuple[WeightRecord, ...]
    glucose_source_order: tuple[GlucoseRecord, ...]
    category_meta: dict[str, LoadedCategory]
    source_file_checksums: dict[str, str]


def load_snapshot(
    snapshot_dir: Path,
    progress: Callable[[str, str], None] | None = None,
) -> LoadedSnapshot:
    """Validate snapshot layout, manifest, checksums, and canonical Parquet schemas.

    Reads only. Raises DiagnosticsValidationError on integrity failure.
    """
    log = progress or (lambda _phase, _name: None)
    path = Path(snapshot_dir)
    if not path.exists() or not path.is_dir():
        raise DiagnosticsValidationError(f"Snapshot directory does not exist: {path}")

    missing = [name for name in REQUIRED_SNAPSHOT_FILES if not (path / name).is_file()]
    if missing:
        raise DiagnosticsValidationError(
            "Snapshot is missing required artifacts:\n" + "\n".join(f"  - {name}" for name in missing),
            missing,
        )

    log("load", "manifest.json")
    manifest_path = path / "manifest.json"
    try:
        manifest = read_manifest(manifest_path)
    except (OSError, ValueError, ValidationError) as exc:
        raise DiagnosticsValidationError(f"Snapshot manifest is invalid: {manifest_path}") from exc
    log("ok", "manifest.json")

    if manifest.schema_version not in SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS:
        raise DiagnosticsValidationError(
            "Unsupported snapshot schema_version "
            f"{manifest.schema_version!r}; expected one of {sorted(SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS)}"
        )

    _verify_checksums(path, manifest)
    tables = _read_and_check_tables(path, progress=log)

    glucose_source = tuple(records_from_glucose_table(tables["glucose"]))
    meals_source = tuple(records_from_meals_table(tables["meals"]))
    workouts_source, workout_invalid = _interval_records(
        tables["workouts"], records_from_workouts_table, "workout"
    )
    sleep_source, sleep_invalid = _interval_records(
        tables["sleep"], records_from_sleep_table, "sleep"
    )
    weight_source = tuple(records_from_weight_table(tables["weight"]))

    glucose = tuple(sort_glucose(glucose_source))
    meals = tuple(sort_meals(meals_source))
    workouts = tuple(sort_workouts(workouts_source))
    sleep = tuple(sort_sleep(sleep_source))
    weight = tuple(sort_weight(weight_source))

    category_meta = {
        "glucose": LoadedCategory(
            name="glucose",
            source_sort_status=_sort_status(glucose_source, glucose),
        ),
        "meals": LoadedCategory(
            name="meals",
            source_sort_status=_sort_status(meals_source, meals),
        ),
        "workouts": LoadedCategory(
            name="workouts",
            source_sort_status=_sort_status(workouts_source, workouts),
            invalid_interval_count=workout_invalid,
        ),
        "sleep": LoadedCategory(
            name="sleep",
            source_sort_status=_sort_status(sleep_source, sleep),
            invalid_interval_count=sleep_invalid,
        ),
        "weight": LoadedCategory(
            name="weight",
            source_sort_status=_sort_status(weight_source, weight),
        ),
    }

    checksums = dict(manifest.checksums)
    if not checksums:
        checksums = {name: artifact.sha256 for name, artifact in manifest.artifacts.items()}

    return LoadedSnapshot(
        snapshot_dir=path,
        snapshot_id=path.name,
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
        glucose=glucose,
        meals=meals,
        workouts=workouts,
        sleep=sleep,
        weight=weight,
        glucose_source_order=glucose_source,
        category_meta=category_meta,
        source_file_checksums=checksums,
    )


def _sort_status(source: Sequence[object], sorted_records: Sequence[object]) -> SortStatus:
    if list(source) == list(sorted_records):
        return "already_sorted"
    return "sorted_for_analysis"


def _verify_checksums(snapshot_dir: Path, manifest: SnapshotManifest) -> None:
    expected: dict[str, str] = {}
    if manifest.checksums:
        expected.update(manifest.checksums)
    for name, artifact in manifest.artifacts.items():
        previous = expected.get(name)
        if previous is not None and previous != artifact.sha256:
            raise DiagnosticsValidationError(
                f"Snapshot manifest checksum disagreement for {name}: "
                "checksums and artifacts do not match"
            )
        expected[name] = artifact.sha256
    if not expected:
        return
    problems: list[str] = []
    for name, digest in expected.items():
        filename = f"{name}.parquet"
        if name in manifest.files:
            filename = manifest.files[name]
        elif name in manifest.artifacts:
            filename = manifest.artifacts[name].file
        path = snapshot_dir / filename
        if not path.is_file():
            problems.append(f"{filename} listed in checksums is missing")
            continue
        actual = sha256_file(path)
        if actual != digest:
            problems.append(f"{filename} SHA-256 does not match the snapshot manifest")
    if problems:
        raise DiagnosticsValidationError(
            "Snapshot artifact checksum verification failed:\n"
            + "\n".join(f"  - {item}" for item in problems),
            problems,
        )


def _read_and_check_tables(
    snapshot_dir: Path,
    progress: Callable[[str, str], None] | None = None,
) -> dict[str, pa.Table]:
    log = progress or (lambda _phase, _name: None)
    tables: dict[str, pa.Table] = {}
    problems: list[str] = []
    for name, expected in _ARROW_SCHEMAS.items():
        path = snapshot_dir / f"{name}.parquet"
        log("load", path.name)
        try:
            table = pq.read_table(path)
        except (OSError, pa.ArrowInvalid) as exc:
            raise DiagnosticsValidationError(f"Failed to read {path.name}") from exc
        if not _schema_compatible(table.schema, expected):
            problems.append(
                f"{path.name} schema does not match canonical snapshot schema "
                f"(expected columns {list(expected.names)}, found {list(table.schema.names)})"
            )
            continue
        tables[name] = table
        log("ok", path.name)
    if problems:
        raise DiagnosticsValidationError(
            "Canonical Parquet schema mismatch:\n" + "\n".join(f"  - {item}" for item in problems),
            problems,
        )
    return tables


def _schema_compatible(actual: pa.Schema, expected: pa.Schema) -> bool:
    if actual.names != expected.names:
        return False
    for name in expected.names:
        if actual.field(name).type != expected.field(name).type:
            return False
    return True


def _interval_records(
    table: pa.Table,
    converter,
    label: str,
) -> tuple[tuple[object, ...], int]:
    rows = table.to_pylist()
    invalid = 0
    for row in rows:
        start = row.get("start")
        end = row.get("end")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise DiagnosticsValidationError(f"{label} parquet has a row with a non-timestamp interval bound")
        if start.tzinfo is None or end.tzinfo is None:
            raise DiagnosticsValidationError(f"{label} parquet has a naive timestamp")
        if end <= start:
            invalid += 1
    if invalid:
        raise DiagnosticsValidationError(
            f"{label} parquet has {invalid} invalid interval(s) with start >= end; "
            "refusing to diagnose a non-canonical snapshot"
        )
    return tuple(converter(table)), 0
