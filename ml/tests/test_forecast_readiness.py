"""Forecast-readiness counting. No episodes or labels are constructed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.models import FORECAST_READINESS_ARROW_SCHEMA
from health_ml.diagnostics.readiness import forecast_readiness_rows
from health_ml.diagnostics.runner import OUTPUT_FILES, run_diagnostics
from health_ml.schemas.canonical import GlucoseRecord
from tests.conftest import FakeHealthClient, regular_glucose, write_snapshot

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
CONFIG = DiagnosticsConfig()
REGULAR = regular_glucose(T0, count=49)


def _row(rows, *, offset_minutes: int, horizon: int):
    anchor = T0 + timedelta(minutes=offset_minutes)
    matches = [
        row
        for row in rows
        if row.anchor_timestamp_utc == anchor and row.horizon_minutes == horizon
    ]
    assert len(matches) == 1
    return matches[0]


def test_unique_targets_at_default_horizons():
    rows = forecast_readiness_rows(REGULAR, CONFIG)
    for horizon in (30, 60, 120):
        item = _row(rows, offset_minutes=120, horizon=horizon)
        assert item.status == "eligible_unique"
        assert item.candidate_observation_count == 1
        assert item.nearest_offset_seconds == 0
        assert item.reason is None


def test_missing_target_when_outside_tolerance():
    records = [row for row in REGULAR if row.timestamp != T0 + timedelta(minutes=150)]
    rows = forecast_readiness_rows(records, CONFIG)
    item = _row(rows, offset_minutes=120, horizon=30)
    assert item.status == "missing_target"
    assert item.candidate_observation_count == 0
    assert item.nearest_observation_timestamp_utc is None


def test_candidate_inside_tolerance_is_eligible():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=150, seconds=60), glucose_mg_dl=100.0)
    records = [row for row in REGULAR if row.timestamp != T0 + timedelta(minutes=150)] + [extra]
    rows = forecast_readiness_rows(records, CONFIG)
    item = _row(rows, offset_minutes=120, horizon=30)
    assert item.status == "eligible_unique"
    assert item.nearest_offset_seconds == 60


def test_multiple_observations_in_band_are_ambiguous():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=151), glucose_mg_dl=100.0)
    records = REGULAR + [extra]
    rows = forecast_readiness_rows(records, CONFIG)
    item = _row(rows, offset_minutes=120, horizon=30)
    assert item.status == "eligible_ambiguous"
    assert item.candidate_observation_count == 2
    assert item.nearest_observation_timestamp_utc == T0 + timedelta(minutes=150)


def test_tie_uses_earlier_timestamp_for_display_only():
    records = [row for row in REGULAR if row.timestamp != T0 + timedelta(minutes=150)] + [
        GlucoseRecord(timestamp=T0 + timedelta(minutes=149), glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=T0 + timedelta(minutes=151), glucose_mg_dl=100.0),
    ]
    rows = forecast_readiness_rows(records, CONFIG)
    item = _row(rows, offset_minutes=120, horizon=30)
    assert item.status == "eligible_ambiguous"
    assert item.candidate_observation_count == 2
    assert item.nearest_observation_timestamp_utc == T0 + timedelta(minutes=149)
    assert abs(item.nearest_offset_seconds) == 60


def test_history_span_below_120_is_insufficient():
    rows = forecast_readiness_rows(REGULAR, CONFIG)
    item = _row(rows, offset_minutes=0, horizon=30)
    assert item.status == "insufficient_history"
    assert item.candidate_observation_count == 1


def test_target_match_with_insufficient_history_stays_insufficient():
    rows = forecast_readiness_rows(REGULAR, CONFIG)
    item = _row(rows, offset_minutes=60, horizon=30)
    assert item.candidate_observation_count == 1
    assert item.status == "insufficient_history"


def test_gap_is_not_interpolated_into_a_target():
    records = [
        GlucoseRecord(timestamp=T0 + timedelta(minutes=120), glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=T0, glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=T0 + timedelta(minutes=140), glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=T0 + timedelta(minutes=160), glucose_mg_dl=100.0),
    ]
    rows = forecast_readiness_rows(records, CONFIG)
    item = _row(rows, offset_minutes=120, horizon=30)
    assert item.status == "missing_target"
    assert item.candidate_observation_count == 0


def test_output_cardinality_is_anchors_times_horizons(tmp_path: Path):
    snapshot = write_snapshot(tmp_path, FakeHealthClient(glucose=REGULAR))
    result = run_diagnostics(snapshot.output_dir, tmp_path / "diagnostics")
    table = pq.read_table(result.output_dir / "forecast_readiness.parquet")
    assert table.num_rows == 49 * 3
    assert table.schema.equals(FORECAST_READINESS_ARROW_SCHEMA)
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == set(OUTPUT_FILES)
    assert "episodes.parquet" not in names
    assert "labels.parquet" not in names
    assert "targets.parquet" not in names
