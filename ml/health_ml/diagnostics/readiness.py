"""Forecast-readiness counting. Does not construct episodes, features, or labels."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import datetime, timedelta

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.models import (
    ELIGIBILITY_DISCLOSURE,
    ForecastReadinessRow,
    ForecastReadinessSummary,
    HorizonReadinessSummary,
    NumericDistribution,
)
from health_ml.schemas.canonical import GlucoseRecord

REASON_INSUFFICIENT_HISTORY = (
    "no observed glucose timestamp at or before the configured minimum-history instant"
)
REASON_MISSING_TARGET = "no observed glucose timestamp within the configured target-tolerance band"
REASON_AMBIGUOUS = "multiple observed glucose timestamps within the configured target-tolerance band"


def forecast_readiness_rows(
    records: Sequence[GlucoseRecord],
    config: DiagnosticsConfig,
) -> tuple[ForecastReadinessRow, ...]:
    timestamps = [row.timestamp for row in records]
    if not timestamps:
        return ()
    order = sorted(range(len(timestamps)), key=lambda index: (timestamps[index], index))
    sorted_ts = [timestamps[index] for index in order]
    earliest = sorted_ts[0]
    tolerance = timedelta(minutes=config.target_tolerance_minutes)
    history = timedelta(minutes=config.minimum_history_minutes)
    rows: list[ForecastReadinessRow] = []
    for index, anchor in enumerate(timestamps):
        has_history = earliest <= anchor - history
        for horizon in config.horizons_minutes:
            ideal = anchor + timedelta(minutes=horizon)
            band_start = ideal - tolerance
            band_end = ideal + tolerance
            left = bisect_left(sorted_ts, band_start)
            right = bisect_right(sorted_ts, band_end)
            candidates: list[tuple[datetime, float]] = []
            for position in range(left, right):
                other_index = order[position]
                if other_index == index:
                    continue
                observed = sorted_ts[position]
                candidates.append((observed, (observed - ideal).total_seconds()))
            nearest_ts, nearest_offset = _nearest(candidates)
            if not has_history:
                status = "insufficient_history"
                reason = REASON_INSUFFICIENT_HISTORY
            elif not candidates:
                status = "missing_target"
                reason = REASON_MISSING_TARGET
            elif len(candidates) == 1:
                status = "eligible_unique"
                reason = None
            else:
                status = "eligible_ambiguous"
                reason = REASON_AMBIGUOUS
            rows.append(
                ForecastReadinessRow(
                    anchor_timestamp_utc=anchor,
                    horizon_minutes=horizon,
                    ideal_target_timestamp_utc=ideal,
                    candidate_observation_count=len(candidates),
                    nearest_observation_timestamp_utc=nearest_ts,
                    nearest_offset_seconds=nearest_offset,
                    status=status,
                    reason=reason,
                )
            )
    return tuple(rows)


def _nearest(candidates: Sequence[tuple[datetime, float]]) -> tuple[datetime | None, float | None]:
    if not candidates:
        return None, None
    best_ts, best_offset = min(candidates, key=lambda item: (abs(item[1]), item[0]))
    return best_ts, best_offset


def forecast_readiness_summary(
    rows: Sequence[ForecastReadinessRow],
    config: DiagnosticsConfig,
) -> ForecastReadinessSummary:
    horizons: list[HorizonReadinessSummary] = []
    for horizon in config.horizons_minutes:
        subset = [row for row in rows if row.horizon_minutes == horizon]
        unique = sum(1 for row in subset if row.status == "eligible_unique")
        ambiguous = sum(1 for row in subset if row.status == "eligible_ambiguous")
        missing = sum(1 for row in subset if row.status == "missing_target")
        insufficient = sum(1 for row in subset if row.status == "insufficient_history")
        eligible_any = unique + ambiguous
        offsets = [
            row.nearest_offset_seconds
            for row in subset
            if row.candidate_observation_count > 0 and row.nearest_offset_seconds is not None
        ]
        count = len(subset)
        horizons.append(
            HorizonReadinessSummary(
                horizon_minutes=horizon,
                anchor_count=count,
                insufficient_history_count=insufficient,
                missing_target_count=missing,
                eligible_unique_count=unique,
                eligible_ambiguous_count=ambiguous,
                eligible_any_count=eligible_any,
                eligible_unique_rate=None if count == 0 else unique / count,
                eligible_any_rate=None if count == 0 else eligible_any / count,
                nearest_target_offset_seconds=NumericDistribution.from_values(offsets),
            )
        )
    return ForecastReadinessSummary(
        row_count=len(rows),
        horizons=tuple(horizons),
        eligibility_disclosure=ELIGIBILITY_DISCLOSURE,
    )
