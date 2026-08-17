"""Fixed-grid mapping tests. No interpolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from health_ml.datasets.snapshot import sort_glucose
from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.grid import grid_timestamps, map_history_grid
from health_ml.schemas.canonical import GlucoseRecord
from tests.conftest import regular_glucose

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
ANCHOR = T0 + timedelta(minutes=120)
CONFIG = EpisodeConfig()
REGULAR = regular_glucose(T0, count=25, value=142.5)


def test_default_grid_has_25_positions_from_history_start_to_anchor():
    stamps = grid_timestamps(ANCHOR, CONFIG)
    assert len(stamps) == 25
    assert stamps[0] == T0
    assert stamps[-1] == ANCHOR
    assert all(
        (later - earlier).total_seconds() == 300
        for earlier, later in zip(stamps, stamps[1:], strict=False)
    )


def test_grid_increments_five_elapsed_minutes_across_dst():
    eastern = ZoneInfo("America/New_York")
    # 2026-03-08 02:00 EST skipped; UTC elapsed time is unchanged.
    anchor = datetime(2026, 3, 8, 8, 0, tzinfo=UTC)  # 03:00 EDT
    stamps = grid_timestamps(anchor, CONFIG)
    assert stamps[-1] == anchor
    assert all(
        (later - earlier) == timedelta(minutes=5)
        for earlier, later in zip(stamps, stamps[1:], strict=False)
    )
    local = [item.astimezone(eastern) for item in stamps]
    assert any(stamp.fold == 0 for stamp in local)
    assert (stamps[-1] - stamps[0]).total_seconds() == 120 * 60


def test_exact_source_values_are_copied_unchanged():
    mapping = map_history_grid(sort_glucose(REGULAR), ANCHOR, CONFIG)
    assert not mapping.ambiguous
    observed = [slot for slot in mapping.slots if slot.observed_mask]
    assert len(observed) == 25
    by_ts = {row.timestamp: row.glucose_mg_dl for row in REGULAR}
    for slot in observed:
        assert slot.glucose_mg_dl == by_ts[slot.source_timestamp_utc]
        assert slot.glucose_mg_dl == 142.5
        assert slot.source_timestamp_utc is not None
        assert abs((slot.source_timestamp_utc - slot.grid_timestamp_utc).total_seconds()) <= 2.5 * 60


def test_missing_slots_are_null_false_null_and_not_filled():
    records = [row for row in REGULAR if row.timestamp != T0 + timedelta(minutes=60)]
    mapping = map_history_grid(sort_glucose(records), ANCHOR, CONFIG)
    missing = [slot for slot in mapping.slots if slot.grid_index == 12]
    assert len(missing) == 1
    slot = missing[0]
    assert slot.observed_mask is False
    assert slot.glucose_mg_dl is None
    assert slot.source_timestamp_utc is None
    assert slot.source is None
    neighbors = [item for item in mapping.slots if item.grid_index in {11, 13}]
    assert all(item.observed_mask for item in neighbors)
    assert all(item.glucose_mg_dl == 142.5 for item in neighbors)


def test_multiple_records_in_one_slot_reject_rather_than_average():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=60, seconds=15), glucose_mg_dl=200.0)
    mapping = map_history_grid(sort_glucose(REGULAR + [extra]), ANCHOR, CONFIG)
    assert mapping.ambiguous
    slot = mapping.slots[12]
    assert slot.observed_mask is False
    assert slot.glucose_mg_dl is None


def test_future_point_does_not_map_onto_anchor_grid_slot():
    future = GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=1), glucose_mg_dl=50.0)
    mapping = map_history_grid(sort_glucose(REGULAR + [future]), ANCHOR, CONFIG)
    last = mapping.slots[-1]
    assert last.grid_timestamp_utc == ANCHOR
    assert last.glucose_mg_dl != 50.0
    if last.source_timestamp_utc is not None:
        assert last.source_timestamp_utc <= ANCHOR


def test_midpoint_observation_maps_to_exactly_one_slot():
    midpoint = GlucoseRecord(timestamp=T0 + timedelta(minutes=62, seconds=30), glucose_mg_dl=88.0)
    drop = {T0 + timedelta(minutes=60), T0 + timedelta(minutes=65)}
    records = [row for row in REGULAR if row.timestamp not in drop]
    mapping = map_history_grid(sort_glucose(records + [midpoint]), ANCHOR, CONFIG)
    slot_60 = mapping.slots[12]
    slot_65 = mapping.slots[13]
    assert slot_60.grid_timestamp_utc == T0 + timedelta(minutes=60)
    assert slot_65.grid_timestamp_utc == T0 + timedelta(minutes=65)
    assert slot_60.observed_mask is False
    assert slot_60.glucose_mg_dl is None
    assert slot_65.observed_mask is True
    assert slot_65.glucose_mg_dl == 88.0
    assert slot_65.source_timestamp_utc == midpoint.timestamp
    assert not mapping.ambiguous
    mapped = [slot for slot in mapping.slots if slot.source_timestamp_utc == midpoint.timestamp]
    assert len(mapped) == 1
