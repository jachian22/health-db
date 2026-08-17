"""Snapshot builder tests — mocked client, Parquet round-trip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import pytest

from health_ml.datasets.manifest import read_manifest, sha256_file
from health_ml.datasets.snapshot import (
    GLUCOSE_ARROW_SCHEMA,
    MEALS_ARROW_SCHEMA,
    SLEEP_ARROW_SCHEMA,
    WEIGHT_ARROW_SCHEMA,
    WORKOUTS_ARROW_SCHEMA,
    build_snapshot,
    records_from_glucose_table,
    records_from_meals_table,
    records_from_sleep_table,
    records_from_weight_table,
    records_from_workouts_table,
    snapshot_id_for,
)
from health_ml.errors import InvalidRangeError, SnapshotExistsError, SnapshotValidationError
from health_ml.schemas.canonical import GlucoseRecord, SleepInterval, WorkoutRecord
from tests.conftest import END, START, FakeHealthClient

EXPECTED_FILES = {
    "glucose.parquet",
    "meals.parquet",
    "workouts.parquet",
    "sleep.parquet",
    "weight.parquet",
    "manifest.json",
}


def test_snapshot_writes_expected_files_and_manifest(tmp_path: Path, populated_client: FakeHealthClient):
    created = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)
    result = build_snapshot(
        START,
        END,
        tmp_path,
        client=populated_client,
        created_at=created,
    )
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == EXPECTED_FILES
    manifest = read_manifest(result.output_dir / "manifest.json")
    assert manifest.schema_version == "0.1"
    assert manifest.row_counts["glucose"] == 4
    assert manifest.row_counts["meals"] == 1
    assert manifest.row_counts["workouts"] == 1
    assert manifest.row_counts["sleep"] == 1
    assert manifest.row_counts["weight"] == 1
    assert manifest.checksums["glucose"]
    assert manifest.request.glucose_resolution == "raw"
    assert manifest.request.glucose_path == "/v1/query/glucose/series"
    assert manifest.request.range_semantics == "[start, end)"
    assert manifest.request.source_start == START
    assert manifest.request.source_end == END
    assert manifest.request.timezone == "America/New_York"
    assert "created_at" not in result.snapshot_id
    assert result.diagnostics.glucose.extra["gaps_over_15m"] == 0
    assert result.diagnostics.glucose.extra["gaps_over_60m"] == 0
    assert manifest.diagnostics.glucose.gaps_over_15m == 0
    assert manifest.created_at == created


def test_parquet_round_trip_preserves_records(tmp_path: Path, populated_client: FakeHealthClient):
    result = build_snapshot(START, END, tmp_path, client=populated_client)
    frame = pd.read_parquet(result.output_dir / "glucose.parquet")
    assert str(frame["timestamp"].dtype).startswith("datetime64")
    assert frame["timestamp"].dt.tz is not None
    glucose = records_from_glucose_table(pq.read_table(result.output_dir / "glucose.parquet"))
    meals = records_from_meals_table(pq.read_table(result.output_dir / "meals.parquet"))
    workouts = records_from_workouts_table(pq.read_table(result.output_dir / "workouts.parquet"))
    sleep = records_from_sleep_table(pq.read_table(result.output_dir / "sleep.parquet"))
    weight = records_from_weight_table(pq.read_table(result.output_dir / "weight.parquet"))

    assert glucose == result.glucose
    assert meals == result.meals
    assert workouts == result.workouts
    assert sleep == result.sleep
    assert weight == result.weight
    assert glucose[0].timestamp.tzinfo is not None
    assert meals[0].foods == ["rice", "chicken"]
    assert workouts[0].active_energy is None
    assert workouts[0].distance_meters == 5000.0
    assert sleep[0].sleep_id == "sleep-1"
    assert weight[0].weight_id == "weight-1"
    glucose_cols = pq.read_table(result.output_dir / "glucose.parquet").column_names
    workout_cols = pq.read_table(result.output_dir / "workouts.parquet").column_names
    assert glucose_cols == ["timestamp", "glucose_mg_dl"]
    assert "trend" not in glucose_cols
    assert "source" not in glucose_cols
    assert "distance_meters" in workout_cols
    assert "active_energy" not in workout_cols
    assert "average_hr" not in workout_cols
    assert "max_hr" not in workout_cols
    local_features = {
        "hour",
        "hour_local",
        "local_hour",
        "time_of_day",
        "tod",
        "america_new_york",
    }
    for cols in (glucose_cols, workout_cols):
        assert local_features.isdisjoint(cols)
    assert [row.timestamp for row in glucose] == sorted(row.timestamp for row in glucose)
    # CGM gap at 12:10 → 12:20 is preserved; no interpolated 12:15 row.
    deltas = [
        (later.timestamp - earlier.timestamp).total_seconds() / 60
        for earlier, later in zip(glucose, glucose[1:], strict=False)
    ]
    assert deltas == [5, 5, 10]


def test_empty_sources_still_write_parquet(tmp_path: Path):
    result = build_snapshot(START, END, tmp_path, client=FakeHealthClient())
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == EXPECTED_FILES
    assert result.manifest.row_counts == {
        "glucose": 0,
        "meals": 0,
        "workouts": 0,
        "sleep": 0,
        "weight": 0,
    }
    schemas = {
        "glucose.parquet": GLUCOSE_ARROW_SCHEMA,
        "meals.parquet": MEALS_ARROW_SCHEMA,
        "workouts.parquet": WORKOUTS_ARROW_SCHEMA,
        "sleep.parquet": SLEEP_ARROW_SCHEMA,
        "weight.parquet": WEIGHT_ARROW_SCHEMA,
    }
    for filename, schema in schemas.items():
        table = pq.read_table(result.output_dir / filename)
        assert table.num_rows == 0
        assert table.schema.equals(schema)
    assert "glucose category is empty" in result.diagnostics.format()


def test_existing_snapshot_is_not_overwritten(tmp_path: Path, populated_client: FakeHealthClient):
    first = build_snapshot(START, END, tmp_path, client=populated_client)
    with pytest.raises(SnapshotExistsError):
        build_snapshot(START, END, tmp_path, client=populated_client)
    second = build_snapshot(
        START,
        END,
        tmp_path,
        client=FakeHealthClient(),
        overwrite=True,
    )
    assert second.output_dir == first.output_dir
    assert second.manifest.row_counts["glucose"] == 0


def test_snapshot_id_is_deterministic():
    assert snapshot_id_for(START, END) == snapshot_id_for(START, END)
    assert "v0.1_" in snapshot_id_for(START, END)


def test_glucose_out_of_range_is_warning_not_dropped(tmp_path: Path):
    client = FakeHealthClient(
        glucose=[
            GlucoseRecord(
                timestamp=datetime(2026, 8, 5, 16, 0, tzinfo=UTC),
                glucose_mg_dl=900.0,
            )
        ]
    )
    result = build_snapshot(START, END, tmp_path, client=client)
    assert result.glucose[0].glucose_mg_dl == 900.0
    assert result.diagnostics.glucose.extra["out_of_range"] == 1
    assert result.manifest.diagnostics.glucose.out_of_range == 1


def test_duplicate_meal_ids_fail(tmp_path: Path, populated_client: FakeHealthClient):
    populated_client.meals.append(populated_client.meals[0].model_copy())
    with pytest.raises(SnapshotValidationError) as raised:
        build_snapshot(START, END, tmp_path, client=populated_client)
    assert "duplicate meal_id" in str(raised.value)


def test_naive_bounds_are_rejected(tmp_path: Path):
    with pytest.raises(InvalidRangeError):
        build_snapshot(datetime(2026, 8, 1), END, tmp_path, client=FakeHealthClient())


def test_diagnostics_count_large_glucose_gap(tmp_path: Path):
    t0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
    client = FakeHealthClient(
        glucose=[
            GlucoseRecord(timestamp=t0, glucose_mg_dl=100.0),
            GlucoseRecord(timestamp=t0 + timedelta(hours=2), glucose_mg_dl=110.0),
        ]
    )
    result = build_snapshot(START, END, tmp_path, client=client)
    assert result.diagnostics.glucose.extra["gaps_over_15m"] == 1
    assert result.diagnostics.glucose.extra["gaps_over_60m"] == 1
    text = result.diagnostics.format()
    assert "gaps > 15m: 1" in text
    assert "rows: 2" in text
    assert "1 CGM gap(s) greater than 15 minutes" in text
    assert result.manifest.diagnostics.glucose.gaps_over_15m == 1
    assert result.manifest.diagnostics.glucose.gaps_over_60m == 1


def test_half_open_point_bounds(tmp_path: Path):
    client = FakeHealthClient(
        glucose=[
            GlucoseRecord(timestamp=START, glucose_mg_dl=90.0),
            GlucoseRecord(timestamp=END, glucose_mg_dl=99.0),
        ]
    )
    result = build_snapshot(START, END, tmp_path, client=client)
    assert [row.timestamp for row in result.glucose] == [START]
    assert result.diagnostics.glucose.extra["out_of_window"] == 0


def test_out_of_window_point_is_preserved_and_counted(tmp_path: Path):
    outsider = GlucoseRecord(timestamp=END, glucose_mg_dl=99.0)

    class Passthrough(FakeHealthClient):
        def get_glucose(self, start: datetime, end: datetime) -> list[GlucoseRecord]:
            return list(self.glucose)

    client = Passthrough(glucose=[GlucoseRecord(timestamp=START, glucose_mg_dl=90.0), outsider])
    result = build_snapshot(START, END, tmp_path, client=client)
    assert len(result.glucose) == 2
    assert result.diagnostics.glucose.extra["out_of_window"] == 1


def test_overlapping_interval_is_not_clipped(tmp_path: Path):
    workout = WorkoutRecord(
        workout_id="span-1",
        start=START - timedelta(hours=2),
        end=START + timedelta(hours=1),
        sport="running",
        source="apple_health",
    )
    sleep = SleepInterval(
        sleep_id="span-sleep",
        start=END - timedelta(hours=1),
        end=END + timedelta(hours=3),
        stage="asleep",
        source="apple_health",
    )
    client = FakeHealthClient(workouts=[workout], sleep=[sleep])
    result = build_snapshot(START, END, tmp_path, client=client)
    assert result.workouts[0].start == workout.start
    assert result.workouts[0].end == workout.end
    assert result.sleep[0].start == sleep.start
    assert result.sleep[0].end == sleep.end
    assert result.diagnostics.workouts.extra["out_of_window"] == 1
    assert result.diagnostics.sleep.extra["out_of_window"] == 1
    assert result.manifest.diagnostics.workouts.out_of_window == 1


def test_duplicate_glucose_timestamps_are_kept(tmp_path: Path):
    t0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
    client = FakeHealthClient(
        glucose=[
            GlucoseRecord(timestamp=t0, glucose_mg_dl=100.0),
            GlucoseRecord(timestamp=t0, glucose_mg_dl=101.0),
        ]
    )
    result = build_snapshot(START, END, tmp_path, client=client)
    assert len(result.glucose) == 2
    assert result.diagnostics.glucose.extra["duplicate_timestamps"] == 1
    assert result.manifest.diagnostics.glucose.duplicate_timestamps == 1


def test_dst_gap_uses_elapsed_utc_not_local_clock(tmp_path: Path):
    # America/New_York springs forward 2026-03-08 02:00 -> 03:00.
    # These UTC instants are 20 minutes apart; local wall clock jumps ~80 minutes.
    earlier = datetime(2026, 3, 8, 6, 50, tzinfo=UTC)
    later = datetime(2026, 3, 8, 7, 10, tzinfo=UTC)
    assert (later - earlier) == timedelta(minutes=20)
    ny = ZoneInfo("America/New_York")
    assert later.astimezone(ny).hour - earlier.astimezone(ny).hour != 0
    client = FakeHealthClient(
        glucose=[
            GlucoseRecord(timestamp=earlier, glucose_mg_dl=100.0),
            GlucoseRecord(timestamp=later, glucose_mg_dl=110.0),
        ]
    )
    start = datetime(2026, 3, 8, tzinfo=UTC)
    end = datetime(2026, 3, 9, tzinfo=UTC)
    result = build_snapshot(start, end, tmp_path, client=client, timezone="America/New_York")
    assert result.diagnostics.glucose.extra["gaps_over_15m"] == 1
    assert result.diagnostics.glucose.extra["gaps_over_60m"] == 0


def test_artifact_checksums_match_parquet_bytes(tmp_path: Path, populated_client: FakeHealthClient):
    result = build_snapshot(START, END, tmp_path, client=populated_client)
    for name, artifact in result.manifest.artifacts.items():
        path = result.output_dir / artifact.file
        assert sha256_file(path) == artifact.sha256
        assert artifact.sha256 == result.manifest.checksums[name]


def test_created_at_is_not_snapshot_identity(tmp_path: Path):
    first = snapshot_id_for(START, END)
    second = snapshot_id_for(START, END)
    assert first == second
    assert "20260816T180000Z" not in first
