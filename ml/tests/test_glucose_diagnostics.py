"""Glucose gap and sampling diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from health_ml.datasets.manifest import sha256_file
from health_ml.datasets.snapshot import GLUCOSE_ARROW_SCHEMA
from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.glucose import classify_gap, detect_glucose_gaps, writable_gaps
from health_ml.diagnostics.loader import load_snapshot
from health_ml.diagnostics.models import GLUCOSE_GAPS_ARROW_SCHEMA
from health_ml.diagnostics.runner import run_diagnostics
from health_ml.schemas.canonical import GlucoseRecord
from tests.conftest import (
    FakeHealthClient,
    replace_category_parquet,
    write_snapshot,
)

T0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
CONFIG = DiagnosticsConfig()


def test_regular_five_minute_readings_have_no_warning_gaps():
    stamps = [T0 + timedelta(minutes=5 * index) for index in range(12)]
    gaps = detect_glucose_gaps(stamps, CONFIG)
    assert gaps
    assert all(gap.classification == "normal" for gap in gaps)
    assert writable_gaps(gaps) == ()


def test_twenty_minute_gap_is_warning():
    stamps = [T0, T0 + timedelta(minutes=20)]
    gaps = detect_glucose_gaps(stamps, CONFIG)
    assert [gap.classification for gap in gaps] == ["warning"]
    assert gaps[0].elapsed_minutes == 20


def test_sixty_one_minute_gap_is_major():
    stamps = [T0, T0 + timedelta(minutes=61)]
    gaps = detect_glucose_gaps(stamps, CONFIG)
    assert [gap.classification for gap in gaps] == ["major"]


def test_exact_gap_thresholds():
    assert classify_gap(timedelta(minutes=15), CONFIG) == "normal"
    assert classify_gap(timedelta(minutes=15, microseconds=1), CONFIG) == "warning"
    assert classify_gap(timedelta(minutes=60), CONFIG) == "warning"
    assert classify_gap(timedelta(minutes=60, microseconds=1), CONFIG) == "major"


def test_duplicate_and_non_increasing_timestamps_are_reported_not_removed(tmp_path: Path):
    t1 = T0 + timedelta(minutes=5)
    table = pa_glucose(
        [
            (t1, 101.0),
            (T0, 100.0),
            (T0, 100.5),
        ]
    )
    snapshot = write_snapshot(tmp_path, FakeHealthClient(glucose=[GlucoseRecord(timestamp=T0, glucose_mg_dl=100.0)]))
    replace_category_parquet(snapshot.output_dir, "glucose", table)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    sampling = result.report.glucose_sampling_summary
    assert sampling.observed_record_count == 3
    assert sampling.duplicate_timestamp_count == 1
    assert sampling.non_increasing_timestamp_pair_count == 1
    loaded = load_snapshot(snapshot.output_dir)
    assert [row.timestamp for row in loaded.glucose_source_order] == [t1, T0, T0]


def test_fewer_than_two_glucose_records_writes_empty_schema_valid_gaps(tmp_path: Path):
    snapshot = write_snapshot(
        tmp_path,
        FakeHealthClient(glucose=[GlucoseRecord(timestamp=T0, glucose_mg_dl=100.0)]),
    )
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    table = pq.read_table(result.output_dir / "glucose_gaps.parquet")
    assert table.num_rows == 0
    assert table.schema.equals(GLUCOSE_GAPS_ARROW_SCHEMA)
    assert result.report.glucose_sampling_summary.interval_distribution_available is False


def test_source_glucose_is_unchanged_after_diagnostics(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client)
    before = sha256_file(snapshot.output_dir / "glucose.parquet")
    before_rows = pq.read_table(snapshot.output_dir / "glucose.parquet").to_pylist()
    run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    assert sha256_file(snapshot.output_dir / "glucose.parquet") == before
    assert pq.read_table(snapshot.output_dir / "glucose.parquet").to_pylist() == before_rows


def test_dst_gap_uses_utc_elapsed_time_not_local_clock(tmp_path: Path):
    earlier = datetime(2026, 3, 8, 6, 50, tzinfo=UTC)
    later = datetime(2026, 3, 8, 7, 10, tzinfo=UTC)
    assert (later - earlier) == timedelta(minutes=20)
    ny = ZoneInfo("America/New_York")
    assert later.astimezone(ny).hour - earlier.astimezone(ny).hour != 0
    snapshot = write_snapshot(
        tmp_path,
        FakeHealthClient(
            glucose=[
                GlucoseRecord(timestamp=earlier, glucose_mg_dl=100.0),
                GlucoseRecord(timestamp=later, glucose_mg_dl=110.0),
            ]
        ),
        start=datetime(2026, 3, 8, tzinfo=UTC),
        end=datetime(2026, 3, 9, tzinfo=UTC),
    )
    gaps = detect_glucose_gaps(
        [row.timestamp for row in load_snapshot(snapshot.output_dir).glucose],
        CONFIG,
    )
    assert [gap.classification for gap in gaps] == ["warning"]
    assert gaps[0].elapsed_minutes == 20


def test_aware_timestamps_required_for_gap_classification():
    stamps = [T0, T0 + timedelta(minutes=20)]
    for stamp in stamps:
        assert stamp.tzinfo is not None
    assert all(gap.previous_timestamp_utc.tzinfo is not None for gap in detect_glucose_gaps(stamps, CONFIG))


def pa_glucose(rows: list[tuple[datetime, float]]):
    import pyarrow as pa

    return pa.Table.from_pylist(
        [{"timestamp": stamp, "glucose_mg_dl": value} for stamp, value in rows],
        schema=GLUCOSE_ARROW_SCHEMA,
    )
