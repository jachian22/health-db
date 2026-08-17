"""Daily coverage and interval/point semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.coverage import (
    coverage_ratio_estimate,
    daily_coverage,
    interval_overlaps,
    local_day_bounds,
    point_in_range,
    structural_summaries,
)
from health_ml.diagnostics.loader import load_snapshot
from health_ml.diagnostics.runner import run_diagnostics
from health_ml.schemas.canonical import GlucoseRecord, MealRecord, SleepInterval, WorkoutRecord
from health_ml.times import interval_extends_beyond_bounds
from tests.conftest import START, FakeHealthClient, write_snapshot

NY = ZoneInfo("America/New_York")
CONFIG = DiagnosticsConfig(display_timezone="America/New_York")


def test_point_half_open_bounds():
    start = datetime(2026, 8, 5, tzinfo=UTC)
    end = datetime(2026, 8, 6, tzinfo=UTC)
    assert point_in_range(start, start, end) is True
    assert point_in_range(end, start, end) is False
    assert start.tzinfo is not None


def test_interval_overlap_bounds():
    start = datetime(2026, 8, 5, tzinfo=UTC)
    end = datetime(2026, 8, 6, tzinfo=UTC)
    assert interval_overlaps(start - timedelta(hours=1), start, start, end) is False
    assert interval_overlaps(end, end + timedelta(hours=1), start, end) is False
    assert interval_overlaps(start - timedelta(minutes=1), start + timedelta(minutes=1), start, end) is True
    assert interval_overlaps(end - timedelta(minutes=1), end + timedelta(minutes=1), start, end) is True
    assert interval_extends_beyond_bounds(start - timedelta(hours=1), start + timedelta(hours=1), start, end) is True
    assert interval_extends_beyond_bounds(start, end, start, end) is False
    assert interval_extends_beyond_bounds(start + timedelta(hours=1), end - timedelta(hours=1), start, end) is False


def test_point_records_count_on_exactly_one_local_day(tmp_path: Path):
    midnight = datetime(2026, 8, 5, 4, 0, tzinfo=UTC)  # 00:00 America/New_York
    snapshot = write_snapshot(
        tmp_path,
        FakeHealthClient(
            glucose=[GlucoseRecord(timestamp=midnight, glucose_mg_dl=100.0)],
            meals=[
                MealRecord(
                    meal_id="meal-midnight",
                    timestamp=midnight,
                    foods=["fixture-food-xyz"],
                    source="manual",
                )
            ],
        ),
    )
    loaded = load_snapshot(snapshot.output_dir)
    summary = daily_coverage(loaded, CONFIG)
    matching = [row for row in summary.rows if row.glucose_observed_count]
    assert len(matching) == 1
    assert matching[0].local_date.isoformat() == "2026-08-05"
    assert matching[0].meal_count == 1
    assert sum(row.glucose_observed_count for row in summary.rows) == 1


def test_interval_crossing_midnight_counts_both_local_days(tmp_path: Path):
    sleep = SleepInterval(
        sleep_id="overnight",
        start=datetime(2026, 8, 5, 3, 30, tzinfo=UTC),
        end=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
        stage="asleep",
        source="apple_health",
    )
    workout = WorkoutRecord(
        workout_id="overnight-workout",
        start=datetime(2026, 8, 5, 3, 45, tzinfo=UTC),
        end=datetime(2026, 8, 5, 4, 30, tzinfo=UTC),
        sport="walking",
        source="apple_health",
    )
    snapshot = write_snapshot(tmp_path, FakeHealthClient(sleep=[sleep], workouts=[workout]))
    loaded = load_snapshot(snapshot.output_dir)
    summary = daily_coverage(loaded, CONFIG)
    by_date = {row.local_date.isoformat(): row for row in summary.rows}
    assert by_date["2026-08-04"].raw_sleep_interval_overlap_count == 1
    assert by_date["2026-08-05"].raw_sleep_interval_overlap_count == 1
    assert by_date["2026-08-04"].workout_overlap_count == 1
    assert by_date["2026-08-05"].workout_overlap_count == 1
    assert sum(row.raw_sleep_interval_overlap_count for row in summary.rows) == 2
    assert sum(row.workout_overlap_count for row in summary.rows) == 2


def test_dst_day_expected_count_uses_elapsed_duration(tmp_path: Path):
    start = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    end = datetime(2026, 3, 9, 4, 0, tzinfo=UTC)
    day_start, day_end = local_day_bounds(start.astimezone(NY).date(), NY)
    assert day_start == start
    assert day_end == end
    elapsed_minutes = (day_end - day_start).total_seconds() / 60.0
    assert elapsed_minutes == 23 * 60
    snapshot = write_snapshot(tmp_path, FakeHealthClient(), start=start, end=end)
    loaded = load_snapshot(snapshot.output_dir)
    summary = daily_coverage(loaded, CONFIG)
    assert summary.local_day_count == 1
    row = summary.rows[0]
    assert row.local_date.isoformat() == "2026-03-08"
    assert row.glucose_expected_count_estimate == 276
    assert row.glucose_coverage_ratio_estimate == 0.0


def test_fall_back_dst_day_is_twenty_five_hours():
    day_start, day_end = local_day_bounds(datetime(2026, 11, 1, tzinfo=NY).date(), NY)
    assert (day_end - day_start) == timedelta(hours=25)
    expected = int(((day_end - day_start).total_seconds() / 60.0) // 5)
    assert expected == 300


def test_coverage_ratio_is_null_iff_expected_is_zero():
    assert coverage_ratio_estimate(0, 0) is None
    assert coverage_ratio_estimate(12, 0) is None
    assert coverage_ratio_estimate(0, 288) == 0.0
    assert coverage_ratio_estimate(288, 288) == 1.0


def test_civil_days_have_nonzero_expected_count_at_default_cadence():
    for day in (
        datetime(2026, 3, 8, tzinfo=NY).date(),
        datetime(2026, 8, 5, tzinfo=NY).date(),
        datetime(2026, 11, 1, tzinfo=NY).date(),
    ):
        start, end = local_day_bounds(day, NY)
        expected = int(((end - start).total_seconds() / 60.0) // 5)
        assert expected > 0


def test_spanning_workout_is_overlapping_and_extends_beyond(tmp_path: Path):
    workout = WorkoutRecord(
        workout_id="span-1",
        start=START - timedelta(hours=2),
        end=START + timedelta(hours=1),
        sport="running",
        source="apple_health",
    )
    snapshot = write_snapshot(tmp_path, FakeHealthClient(workouts=[workout]))
    loaded = load_snapshot(snapshot.output_dir)
    summary = structural_summaries(loaded)["workouts"]
    assert summary.overlapping_snapshot_count == 1
    assert summary.non_overlapping_count == 0
    assert summary.extends_beyond_bounds_count == 1
    assert summary.in_declared_interval_count == 1
    assert summary.outside_declared_interval_count == 0


def test_diagnostics_writes_daily_coverage_parquet(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client)
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    assert (result.output_dir / "daily_coverage.parquet").is_file()
    assert result.report.daily_coverage_summary.local_day_count > 0
