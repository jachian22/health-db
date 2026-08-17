"""Pure eligibility rules for Phase 2 forecast episodes.

No I/O. Source glucose values and timestamps are never modified.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from health_ml.datasets.snapshot import sort_glucose
from health_ml.episodes.config import EpisodeConfig, target_rejection_code
from health_ml.episodes.contracts import HorizonDiagnostic
from health_ml.episodes.grid import GridMapping, map_history_grid
from health_ml.schemas.canonical import GlucoseRecord, require_aware_utc

CODE_MISSING_HISTORY_START = "MISSING_HISTORY_START"
CODE_AMBIGUOUS_HISTORY_START = "AMBIGUOUS_HISTORY_START"
CODE_HISTORY_GAP_EXCEEDS_MAX = "HISTORY_GAP_EXCEEDS_MAX"
CODE_HISTORY_INSUFFICIENT_OBSERVATIONS = "HISTORY_INSUFFICIENT_OBSERVATIONS"
CODE_DUPLICATE_OR_NONINCREASING = "DUPLICATE_OR_NONINCREASING_HISTORY_TIMESTAMP"
CODE_AMBIGUOUS_GRID_MAPPING = "AMBIGUOUS_GRID_MAPPING"

_SKIPPED_GRID = GridMapping(slots=(), ambiguous=False)


@dataclass(frozen=True)
class HorizonEvaluation:
    horizon_minutes: int
    ideal_target_timestamp_utc: datetime
    candidate_count: int
    target_record: GlucoseRecord | None
    rejection_code: str | None

    def diagnostic(self) -> HorizonDiagnostic:
        return HorizonDiagnostic(
            horizon_minutes=self.horizon_minutes,
            target_candidate_count=self.candidate_count,
            rejection_code=self.rejection_code,
        )


@dataclass(frozen=True)
class AnchorEvaluation:
    anchor_timestamp_utc: datetime
    history_start_timestamp_utc: datetime
    history_end_timestamp_utc: datetime
    maximum_target_timestamp_utc: datetime
    rejection_codes: tuple[str, ...]
    history_observed_count: int
    history_max_observed_gap_minutes: float | None
    history_start_record: GlucoseRecord | None
    horizons: tuple[HorizonEvaluation, ...]
    grid: GridMapping

    @property
    def accepted(self) -> bool:
        return not self.rejection_codes


def history_start_utc(anchor: datetime, config: EpisodeConfig) -> datetime:
    return require_aware_utc(anchor, field_name="anchor") - timedelta(minutes=config.history_minutes)


def maximum_target_utc(anchor: datetime, config: EpisodeConfig) -> datetime:
    return require_aware_utc(anchor, field_name="anchor") + timedelta(
        minutes=config.max_horizon_minutes
    )


def evaluate_anchor(
    records: Sequence[GlucoseRecord],
    anchor: datetime,
    config: EpisodeConfig,
    *,
    timestamps: Sequence[datetime] | None = None,
) -> AnchorEvaluation:
    """Evaluate one candidate glucose timestamp against the Phase 2 policy."""
    anchor_utc = require_aware_utc(anchor, field_name="anchor")
    start = history_start_utc(anchor_utc, config)
    end = anchor_utc
    max_target = maximum_target_utc(anchor_utc, config)
    stamp_index = timestamps if timestamps is not None else [row.timestamp for row in records]
    codes: list[str] = []

    start_matches = records_in_closed_band(
        records,
        start - timedelta(minutes=config.history_start_tolerance_minutes),
        start + timedelta(minutes=config.history_start_tolerance_minutes),
        timestamps=stamp_index,
    )
    history_start_record: GlucoseRecord | None = None
    if len(start_matches) == 0:
        codes.append(CODE_MISSING_HISTORY_START)
    elif len(start_matches) > 1:
        codes.append(CODE_AMBIGUOUS_HISTORY_START)
    else:
        history_start_record = start_matches[0]

    history = records_in_closed_band(records, start, end, timestamps=stamp_index)
    history_count = len(history)
    max_gap: float | None = None
    duplicate_history = False
    if history_count < 2:
        codes.append(CODE_HISTORY_INSUFFICIENT_OBSERVATIONS)
    else:
        history_stamps = [row.timestamp for row in history]
        if any(
            current <= previous
            for previous, current in zip(history_stamps, history_stamps[1:], strict=False)
        ):
            codes.append(CODE_DUPLICATE_OR_NONINCREASING)
            duplicate_history = True
        gaps = [
            (current - previous).total_seconds() / 60.0
            for previous, current in zip(history_stamps, history_stamps[1:], strict=False)
        ]
        max_gap = max(gaps)
        threshold = config.max_history_gap_minutes
        if any(gap > threshold for gap in gaps):
            codes.append(CODE_HISTORY_GAP_EXCEEDS_MAX)

    horizons = tuple(
        evaluate_target(records, anchor_utc, horizon, config, timestamps=stamp_index)
        for horizon in config.horizons_minutes
    )
    for horizon in horizons:
        if horizon.rejection_code is not None:
            codes.append(horizon.rejection_code)

    if duplicate_history:
        grid = _SKIPPED_GRID
    else:
        grid = map_history_grid(records, anchor_utc, config, timestamps=stamp_index)
        if grid.ambiguous:
            codes.append(CODE_AMBIGUOUS_GRID_MAPPING)

    return AnchorEvaluation(
        anchor_timestamp_utc=anchor_utc,
        history_start_timestamp_utc=start,
        history_end_timestamp_utc=end,
        maximum_target_timestamp_utc=max_target,
        rejection_codes=tuple(codes),
        history_observed_count=history_count,
        history_max_observed_gap_minutes=max_gap,
        history_start_record=history_start_record,
        horizons=horizons,
        grid=grid,
    )


def evaluate_target(
    records: Sequence[GlucoseRecord],
    anchor: datetime,
    horizon_minutes: int,
    config: EpisodeConfig,
    *,
    timestamps: Sequence[datetime] | None = None,
) -> HorizonEvaluation:
    """Select a unique observed future target, or record why it is unavailable."""
    anchor_utc = require_aware_utc(anchor, field_name="anchor")
    ideal = anchor_utc + timedelta(minutes=horizon_minutes)
    tolerance = timedelta(minutes=config.target_tolerance_minutes)
    in_band = records_in_closed_band(
        records,
        ideal - tolerance,
        ideal + tolerance,
        timestamps=timestamps,
    )
    future = [row for row in in_band if row.timestamp > anchor_utc]
    code: str | None
    target: GlucoseRecord | None
    if len(future) == 0:
        code = target_rejection_code("MISSING", horizon_minutes)
        target = None
    elif len(future) > 1:
        code = target_rejection_code("AMBIGUOUS", horizon_minutes)
        target = None
    else:
        code = None
        target = future[0]
    return HorizonEvaluation(
        horizon_minutes=horizon_minutes,
        ideal_target_timestamp_utc=ideal,
        candidate_count=len(future),
        target_record=target,
        rejection_code=code,
    )


def evaluate_all_anchors(
    records: Sequence[GlucoseRecord],
    config: EpisodeConfig,
) -> tuple[AnchorEvaluation, ...]:
    """Evaluate every observed glucose record as a candidate anchor."""
    ordered = sort_glucose(records)
    timestamps = [row.timestamp for row in ordered]
    return tuple(
        evaluate_anchor(ordered, row.timestamp, config, timestamps=timestamps) for row in ordered
    )


def records_in_closed_band(
    records: Sequence[GlucoseRecord],
    start: datetime,
    end: datetime,
    *,
    timestamps: Sequence[datetime] | None = None,
) -> list[GlucoseRecord]:
    """Inclusive [start, end] lookup. `records` must be sorted by timestamp."""
    start_utc = require_aware_utc(start, field_name="band start")
    end_utc = require_aware_utc(end, field_name="band end")
    stamp_index = timestamps if timestamps is not None else [row.timestamp for row in records]
    left = bisect_left(stamp_index, start_utc)
    right = bisect_right(stamp_index, end_utc)
    return list(records[left:right])
