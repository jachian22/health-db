"""Diagnostics artifact and report tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from health_ml.datasets.manifest import sha256_file
from health_ml.datasets.snapshot import build_snapshot
from health_ml.diagnostics.models import (
    DAILY_COVERAGE_ARROW_SCHEMA,
    ELIGIBILITY_DISCLOSURE,
    FORECAST_READINESS_ARROW_SCHEMA,
    GLUCOSE_GAPS_ARROW_SCHEMA,
    SPLIT_DISCLOSURE,
)
from health_ml.diagnostics.runner import OUTPUT_FILES, run_diagnostics
from health_ml.errors import DiagnosticsError, DiagnosticsExistsError
from health_ml.schemas.canonical import GlucoseRecord, MealRecord
from tests.conftest import END, START, FakeHealthClient, write_snapshot

FOOD = "secret-food-xyz"
GLUCOSE_VALUE = 187.3


def _sensitive_snapshot(tmp_path: Path):
    return write_snapshot(
        tmp_path,
        FakeHealthClient(
            glucose=[
                GlucoseRecord(
                    timestamp=datetime(2026, 8, 5, 16, 0, tzinfo=UTC),
                    glucose_mg_dl=GLUCOSE_VALUE,
                )
            ],
            meals=[
                MealRecord(
                    meal_id="meal-secret",
                    timestamp=datetime(2026, 8, 5, 19, 0, tzinfo=UTC),
                    foods=[FOOD],
                    source="manual",
                )
            ],
        ),
    )


def test_expected_output_files_and_parquet_round_trip(tmp_path: Path, populated_client):
    snapshot = write_snapshot(tmp_path, populated_client)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == set(OUTPUT_FILES)
    schemas = {
        "glucose_gaps.parquet": GLUCOSE_GAPS_ARROW_SCHEMA,
        "daily_coverage.parquet": DAILY_COVERAGE_ARROW_SCHEMA,
        "forecast_readiness.parquet": FORECAST_READINESS_ARROW_SCHEMA,
    }
    for filename, schema in schemas.items():
        table = pq.read_table(result.output_dir / filename)
        assert table.schema.equals(schema)
        again = pq.read_table(result.output_dir / filename)
        assert again.to_pylist() == table.to_pylist()


def test_manifest_checksums_match_artifacts(tmp_path: Path, populated_client):
    snapshot = write_snapshot(tmp_path, populated_client)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    payload = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, spec in payload["files"].items():
        path = result.output_dir / spec["path"]
        assert path.is_file()
        assert sha256_file(path) == spec["sha256"]
        if name.endswith(".parquet"):
            assert spec["rows"] == pq.read_table(path).num_rows
    assert payload["input_snapshot_id"] == snapshot.snapshot_id
    assert payload["input_snapshot_manifest_sha256"] == sha256_file(snapshot.output_dir / "manifest.json")
    assert "created_at" not in result.diagnostics_id


def test_repeated_run_does_not_overwrite(tmp_path: Path, populated_client):
    snapshot = write_snapshot(tmp_path, populated_client)
    created = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)
    first = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics", created_at=created)
    with pytest.raises(DiagnosticsExistsError):
        run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics", created_at=created)
    marker = first.output_dir / "diagnostics.md"
    original = marker.read_text(encoding="utf-8")
    original_json = sha256_file(first.output_dir / "diagnostics.json")
    second = run_diagnostics(
        snapshot.output_dir,
        tmp_path / "diagnostics",
        overwrite=True,
        created_at=created,
    )
    assert second.output_dir == first.output_dir
    assert second.diagnostics_id == first.diagnostics_id
    assert marker.read_text(encoding="utf-8") == original
    assert sha256_file(second.output_dir / "diagnostics.json") == original_json


def test_diagnostics_id_changes_when_snapshot_bytes_change(tmp_path: Path, populated_client):
    first_snap = write_snapshot(tmp_path, populated_client)
    first = run_diagnostics(first_snap.output_dir, tmp_path / "diagnostics")
    build_snapshot(START, END, tmp_path / "snapshots", client=FakeHealthClient(), overwrite=True)
    second = run_diagnostics(first_snap.output_dir, tmp_path / "diagnostics")
    assert first.diagnostics_id != second.diagnostics_id
    assert first.output_dir != second.output_dir


def test_json_contains_required_fields(tmp_path: Path, populated_client):
    snapshot = write_snapshot(tmp_path, populated_client)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    payload = json.loads((result.output_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert payload["diagnostics_schema_version"] == "1.5"
    assert isinstance(payload["diagnostics_id"], str) and payload["diagnostics_id"]
    assert payload["diagnostics_id"].startswith("d1.5_")
    assert isinstance(payload["created_at"], str) and payload["created_at"].endswith("Z")
    assert payload["input_snapshot"]["snapshot_id"] == snapshot.snapshot_id
    assert result.report.configuration.display_timezone_source == "snapshot_manifest"
    assert isinstance(payload["input_snapshot"]["manifest_checksum"], str)
    assert len(payload["input_snapshot"]["manifest_checksum"]) == 64
    assert payload["input_snapshot"]["source_interval"]["range_semantics"] == "[start, end)"
    assert isinstance(payload["input_snapshot"]["source_file_checksums"], dict)
    assert set(payload["structural_summary"]) == {"glucose", "meals", "workouts", "sleep", "weight"}
    for summary in payload["structural_summary"].values():
        assert isinstance(summary["row_count"], int)
        assert isinstance(summary["in_declared_interval_count"], int)
        assert isinstance(summary["outside_declared_interval_count"], int)
    workouts = payload["structural_summary"]["workouts"]
    assert isinstance(workouts["overlapping_snapshot_count"], int)
    assert isinstance(workouts["non_overlapping_count"], int)
    assert isinstance(workouts["extends_beyond_bounds_count"], int)
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["limitations"], list)
    assert payload["limitations"]
    assert payload["forecast_readiness_summary"]["eligibility_disclosure"] == ELIGIBILITY_DISCLOSURE
    assert payload["chronological_coverage_summary"]["split_disclosure"] == SPLIT_DISCLOSURE
    assert isinstance(payload["forecast_readiness_summary"]["horizons"], list)
    assert [row["horizon_minutes"] for row in payload["forecast_readiness_summary"]["horizons"]] == [
        30,
        60,
        120,
    ]


def test_refuses_to_write_under_snapshot_directory(tmp_path: Path, populated_client):
    snapshot = write_snapshot(tmp_path, populated_client)
    with pytest.raises(DiagnosticsError, match="must not be written under the snapshot directory"):
        run_diagnostics(snapshot.output_dir, snapshot.output_dir)


def test_markdown_includes_limitations_and_omits_raw_values(tmp_path: Path):
    snapshot = _sensitive_snapshot(tmp_path)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    markdown = (result.output_dir / "diagnostics.md").read_text(encoding="utf-8")
    assert ELIGIBILITY_DISCLOSURE in markdown
    assert SPLIT_DISCLOSURE in markdown
    assert "No episodes" in markdown
    assert FOOD not in markdown
    assert str(GLUCOSE_VALUE) not in markdown
    assert FOOD not in result.cli_summary
    assert str(GLUCOSE_VALUE) not in result.cli_summary
    json_text = (result.output_dir / "diagnostics.json").read_text(encoding="utf-8")
    assert FOOD not in json_text
