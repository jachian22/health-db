"""Pure eligibility-rule tests. Synthetic timezone-aware fixtures only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from health_ml.datasets.snapshot import sort_glucose
from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.eligibility import (
    CODE_AMBIGUOUS_GRID_MAPPING,
    CODE_AMBIGUOUS_HISTORY_START,
    CODE_DUPLICATE_OR_NONINCREASING,
    CODE_HISTORY_GAP_EXCEEDS_MAX,
    CODE_HISTORY_INSUFFICIENT_OBSERVATIONS,
    CODE_MISSING_HISTORY_START,
    evaluate_all_anchors,
    evaluate_anchor,
)
from health_ml.schemas.canonical import GlucoseRecord
from tests.conftest import regular_glucose

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
ANCHOR = T0 + timedelta(minutes=120)
CONFIG = EpisodeConfig()
REGULAR = regular_glucose(T0, count=49, value=111.0)


def _eval(records, anchor=ANCHOR, config=CONFIG):
    return evaluate_anchor(sort_glucose(records), anchor, config)


def _drop(*stamps: datetime) -> list[GlucoseRecord]:
    skip = set(stamps)
    return [row for row in REGULAR if row.timestamp not in skip]


def test_default_regular_anchor_is_accepted():
    result = _eval(REGULAR)
    assert result.accepted
    assert result.rejection_codes == ()
    assert result.history_observed_count == 25
    assert all(horizon.target_record is not None for horizon in result.horizons)


def test_missing_history_start():
    result = _eval(_drop(T0))
    assert not result.accepted
    assert CODE_MISSING_HISTORY_START in result.rejection_codes


def test_ambiguous_history_start():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=1), glucose_mg_dl=111.0)
    result = _eval(REGULAR + [extra])
    assert not result.accepted
    assert CODE_AMBIGUOUS_HISTORY_START in result.rejection_codes


def test_history_gap_exceeds_max():
    records = _drop(
        T0 + timedelta(minutes=60),
        T0 + timedelta(minutes=65),
        T0 + timedelta(minutes=70),
    )
    result = _eval(records)
    assert not result.accepted
    assert CODE_HISTORY_GAP_EXCEEDS_MAX in result.rejection_codes


def test_exact_15_minute_history_gap_is_accepted():
    records = regular_glucose(T0, count=17, step_minutes=15)
    result = _eval(records)
    assert result.history_max_observed_gap_minutes == 15
    assert CODE_HISTORY_GAP_EXCEEDS_MAX not in result.rejection_codes
    assert result.accepted


def test_fifteen_minutes_plus_one_second_is_rejected():
    stamps = [
        T0,
        T0 + timedelta(minutes=15),
        T0 + timedelta(minutes=30),
        T0 + timedelta(minutes=45),
        T0 + timedelta(minutes=60, seconds=1),
        T0 + timedelta(minutes=75),
        T0 + timedelta(minutes=90),
        T0 + timedelta(minutes=105),
        T0 + timedelta(minutes=120),
        T0 + timedelta(minutes=150),
        T0 + timedelta(minutes=180),
        T0 + timedelta(minutes=240),
    ]
    records = [GlucoseRecord(timestamp=stamp, glucose_mg_dl=100.0) for stamp in stamps]
    result = _eval(records)
    assert CODE_HISTORY_GAP_EXCEEDS_MAX in result.rejection_codes


def test_history_insufficient_observations():
    records = [
        GlucoseRecord(timestamp=ANCHOR, glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=30), glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=60), glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=120), glucose_mg_dl=100.0),
    ]
    result = _eval(records)
    assert CODE_HISTORY_INSUFFICIENT_OBSERVATIONS in result.rejection_codes


def test_duplicate_or_nonincreasing_history_timestamp():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=60), glucose_mg_dl=222.0)
    result = _eval(REGULAR + [extra])
    assert not result.accepted
    assert CODE_DUPLICATE_OR_NONINCREASING in result.rejection_codes
    assert CODE_AMBIGUOUS_GRID_MAPPING not in result.rejection_codes
    assert result.grid.slots == ()


def test_missing_targets_per_horizon():
    for minutes, code in ((30, "MISSING_TARGET_30M"), (60, "MISSING_TARGET_60M"), (120, "MISSING_TARGET_120M")):
        result = _eval(_drop(ANCHOR + timedelta(minutes=minutes)))
        assert not result.accepted
        assert code in result.rejection_codes


def test_ambiguous_targets_per_horizon():
    for minutes, code in (
        (30, "AMBIGUOUS_TARGET_30M"),
        (60, "AMBIGUOUS_TARGET_60M"),
        (120, "AMBIGUOUS_TARGET_120M"),
    ):
        extra = GlucoseRecord(
            timestamp=ANCHOR + timedelta(minutes=minutes, seconds=30),
            glucose_mg_dl=100.0,
        )
        result = _eval(REGULAR + [extra])
        assert not result.accepted
        assert code in result.rejection_codes
        horizon = next(item for item in result.horizons if item.horizon_minutes == minutes)
        assert horizon.target_record is None
        assert horizon.candidate_count == 2


def test_ambiguous_grid_mapping():
    extra = GlucoseRecord(timestamp=T0 + timedelta(minutes=60, seconds=30), glucose_mg_dl=100.0)
    result = _eval(REGULAR + [extra])
    assert not result.accepted
    assert CODE_AMBIGUOUS_GRID_MAPPING in result.rejection_codes


def test_exact_target_tolerance_boundary_is_accepted():
    extra = GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=32, seconds=30), glucose_mg_dl=150.0)
    records = _drop(ANCHOR + timedelta(minutes=30)) + [extra]
    result = _eval(records)
    target = next(item for item in result.horizons if item.horizon_minutes == 30)
    assert target.rejection_code is None
    assert target.target_record is not None
    assert target.target_record.glucose_mg_dl == 150.0
    assert abs(target.target_record.timestamp - extra.timestamp) == timedelta(0)


def test_just_outside_target_tolerance_is_rejected():
    extra = GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=32, seconds=31), glucose_mg_dl=150.0)
    records = _drop(ANCHOR + timedelta(minutes=30)) + [extra]
    result = _eval(records)
    assert "MISSING_TARGET_30M" in result.rejection_codes


def test_target_timestamp_equal_to_anchor_is_rejected():
    config = EpisodeConfig(
        history_minutes=10,
        grid_cadence_minutes=5,
        horizons_minutes=(5,),
        target_tolerance_minutes=5,
        max_history_gap_minutes=15,
    )
    start = ANCHOR - timedelta(minutes=10)
    records = [
        GlucoseRecord(timestamp=start, glucose_mg_dl=100.0),
        GlucoseRecord(timestamp=start + timedelta(minutes=5), glucose_mg_dl=101.0),
        GlucoseRecord(timestamp=ANCHOR, glucose_mg_dl=102.0),
    ]
    result = _eval(records, config=config)
    assert result.horizons[0].target_record is None
    assert result.horizons[0].candidate_count == 0
    assert "MISSING_TARGET_5M" in result.rejection_codes


def test_history_crossing_replacement_gap_is_rejected_then_later_anchor_accepted():
    pre = regular_glucose(T0, count=13)  # 0..60
    post_start = T0 + timedelta(minutes=120)
    post = regular_glucose(post_start, count=49)  # 120..360
    records = pre + post
    crossing = _eval(records, anchor=post_start + timedelta(minutes=60))
    assert CODE_HISTORY_GAP_EXCEEDS_MAX in crossing.rejection_codes

    ready_anchor = post_start + timedelta(minutes=120)
    ready = _eval(records, anchor=ready_anchor)
    assert ready.accepted
    assert ready.history_start_timestamp_utc == post_start
    assert all(row.timestamp >= post_start for row in records if ready.history_start_timestamp_utc <= row.timestamp <= ready_anchor)


def test_no_source_record_after_anchor_in_history():
    future = GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=1), glucose_mg_dl=999.0)
    result = _eval(REGULAR + [future])
    assert result.accepted
    for slot in result.grid.slots:
        if slot.source_timestamp_utc is not None:
            assert slot.source_timestamp_utc <= ANCHOR
        assert slot.glucose_mg_dl != 999.0
    assert all(horizon.target_record is None or horizon.target_record.timestamp != future.timestamp for horizon in result.horizons)


def test_evaluate_all_anchors_counts_every_observation():
    results = evaluate_all_anchors(REGULAR, CONFIG)
    assert len(results) == 49
    accepted = [item for item in results if item.accepted]
    assert len(accepted) == 1
    assert accepted[0].anchor_timestamp_utc == ANCHOR
