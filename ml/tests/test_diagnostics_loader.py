"""Snapshot loader integrity tests — local fixtures only, no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from health_ml.datasets.manifest import sha256_file
from health_ml.datasets.snapshot import GLUCOSE_ARROW_SCHEMA, UTC_TIMESTAMP, WORKOUTS_ARROW_SCHEMA
from health_ml.diagnostics.loader import load_snapshot
from health_ml.errors import DiagnosticsValidationError
from health_ml.schemas.canonical import GlucoseRecord
from tests.conftest import FakeHealthClient, replace_category_parquet, write_snapshot


def _digests(snapshot_dir: Path) -> dict[str, str]:
    return {path.name: sha256_file(path) for path in sorted(snapshot_dir.iterdir()) if path.is_file()}


def test_valid_snapshot_loads(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    loaded = load_snapshot(snapshot)
    assert loaded.snapshot_id == snapshot.name
    assert len(loaded.glucose) == 4
    assert len(loaded.meals) == 1
    assert all(row.timestamp.tzinfo is not None for row in loaded.glucose)
    assert loaded.category_meta["glucose"].source_sort_status == "already_sorted"


def test_missing_required_artifact_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    (snapshot / "meals.parquet").unlink()
    with pytest.raises(DiagnosticsValidationError, match="missing required"):
        load_snapshot(snapshot)


def test_invalid_snapshot_manifest_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    (snapshot / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DiagnosticsValidationError, match="manifest is invalid"):
        load_snapshot(snapshot)


def test_checksum_mismatch_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    payload = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    payload["checksums"]["glucose"] = "0" * 64
    payload["artifacts"]["glucose"]["sha256"] = "0" * 64
    (snapshot / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(DiagnosticsValidationError, match="SHA-256"):
        load_snapshot(snapshot)


def test_canonical_parquet_schema_mismatch_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    table = pa.table({"value": [1.0]})
    replace_category_parquet(snapshot, "glucose", table)
    with pytest.raises(DiagnosticsValidationError, match="schema does not match"):
        load_snapshot(snapshot)


def test_empty_schema_valid_category_succeeds(tmp_path: Path):
    snapshot = write_snapshot(tmp_path, FakeHealthClient()).output_dir
    loaded = load_snapshot(snapshot)
    assert loaded.glucose == ()
    assert loaded.meals == ()
    assert pq.read_table(snapshot / "glucose.parquet").schema.equals(GLUCOSE_ARROW_SCHEMA)


def test_loader_never_writes_to_snapshot_directory(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    before = _digests(snapshot)
    mtimes = {path.name: path.stat().st_mtime_ns for path in snapshot.iterdir()}
    load_snapshot(snapshot)
    assert _digests(snapshot) == before
    assert {path.name: path.stat().st_mtime_ns for path in snapshot.iterdir()} == mtimes


def test_unsorted_glucose_is_sorted_for_analysis_only(tmp_path: Path):
    t0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
    t1 = t0.replace(minute=5)
    client = FakeHealthClient(glucose=[GlucoseRecord(timestamp=t0, glucose_mg_dl=100.0)])
    snapshot = write_snapshot(tmp_path, client).output_dir
    table = pa.Table.from_pylist(
        [
            {"timestamp": t1, "glucose_mg_dl": 110.0},
            {"timestamp": t0, "glucose_mg_dl": 100.0},
        ],
        schema=GLUCOSE_ARROW_SCHEMA,
    )
    replace_category_parquet(snapshot, "glucose", table)
    loaded = load_snapshot(snapshot)
    assert loaded.category_meta["glucose"].source_sort_status == "sorted_for_analysis"
    assert [row.timestamp for row in loaded.glucose] == [t0, t1]
    assert [row.timestamp for row in loaded.glucose_source_order] == [t1, t0]
    assert UTC_TIMESTAMP == GLUCOSE_ARROW_SCHEMA.field("timestamp").type


def test_invalid_workout_interval_fails_closed(tmp_path: Path):
    t0 = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    snapshot = write_snapshot(tmp_path, FakeHealthClient()).output_dir
    table = pa.Table.from_pylist(
        [
            {
                "workout_id": "bad-interval",
                "start": t0,
                "end": t0,
                "sport": "running",
                "distance_meters": None,
                "source": "apple_health",
            }
        ],
        schema=WORKOUTS_ARROW_SCHEMA,
    )
    replace_category_parquet(snapshot, "workouts", table)
    with pytest.raises(DiagnosticsValidationError, match="invalid interval"):
        load_snapshot(snapshot)
