"""Glucose sampling-interval and gap diagnostics. Pure functions; no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.models import (
    GapClassification,
    GlucoseGap,
    GlucoseGapSummary,
    GlucoseSamplingSummary,
    GlucoseSegment,
    GlucoseValueSummary,
    NumericDistribution,
    percentile,
)
from health_ml.schemas.canonical import GlucoseRecord


def classify_gap(elapsed: timedelta, config: DiagnosticsConfig) -> GapClassification:
    warning = timedelta(minutes=config.gap_warning_minutes)
    major = timedelta(minutes=config.gap_major_minutes)
    if elapsed <= warning:
        return "normal"
    if elapsed <= major:
        return "warning"
    return "major"


def detect_glucose_gaps(
    timestamps: Sequence[datetime],
    config: DiagnosticsConfig,
) -> tuple[GlucoseGap, ...]:
    """Adjacent-timestamp gaps over chronologically sorted UTC instants.

    Duplicate timestamps are preserved (elapsed 0). No interpolation or repair.
    """
    ordered = list(timestamps)
    if len(ordered) < 2:
        return ()
    gaps: list[GlucoseGap] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        elapsed = current - previous
        elapsed_minutes = elapsed.total_seconds() / 60.0
        classification = classify_gap(elapsed, config)
        gaps.append(
            GlucoseGap(
                previous_timestamp_utc=previous,
                next_timestamp_utc=current,
                elapsed_minutes=elapsed_minutes,
                classification=classification,
            )
        )
    return tuple(gaps)


def writable_gaps(gaps: Sequence[GlucoseGap]) -> tuple[GlucoseGap, ...]:
    return tuple(gap for gap in gaps if gap.classification in {"warning", "major"})


def glucose_value_summary(records: Sequence[GlucoseRecord]) -> GlucoseValueSummary:
    if not records:
        return GlucoseValueSummary()
    values = [row.glucose_mg_dl for row in records]
    return GlucoseValueSummary(
        min=min(values),
        max=max(values),
        mean=sum(values) / len(values),
        median=percentile(values, 50),
    )


def non_increasing_pair_count(timestamps: Sequence[datetime]) -> int:
    count = 0
    for previous, current in zip(timestamps, timestamps[1:], strict=False):
        if current < previous:
            count += 1
    return count


def duplicate_timestamp_count(timestamps: Sequence[datetime]) -> int:
    return len(timestamps) - len(set(timestamps))


def sampling_summary(
    records: Sequence[GlucoseRecord],
    source_order: Sequence[GlucoseRecord],
    gaps: Sequence[GlucoseGap],
) -> GlucoseSamplingSummary:
    timestamps = [row.timestamp for row in records]
    source_timestamps = [row.timestamp for row in source_order]
    intervals = [gap.elapsed_minutes for gap in gaps]
    warning = [gap for gap in gaps if gap.classification == "warning"]
    major = [gap for gap in gaps if gap.classification == "major"]
    return GlucoseSamplingSummary(
        observed_record_count=len(records),
        duplicate_timestamp_count=duplicate_timestamp_count(timestamps),
        non_increasing_timestamp_pair_count=non_increasing_pair_count(source_timestamps),
        value_summary=glucose_value_summary(records),
        sampling_interval_minutes=NumericDistribution.from_values(intervals),
        warning_gap_count=len(warning),
        major_gap_count=len(major),
        warning_gap_duration_minutes_total=sum(gap.elapsed_minutes for gap in warning),
        major_gap_duration_minutes_total=sum(gap.elapsed_minutes for gap in major),
        interval_distribution_available=len(records) >= 2,
    )


def gap_summary(gaps: Sequence[GlucoseGap]) -> GlucoseGapSummary:
    warning = [gap for gap in gaps if gap.classification == "warning"]
    major = [gap for gap in gaps if gap.classification == "major"]
    written = writable_gaps(gaps)
    return GlucoseGapSummary(
        warning_count=len(warning),
        major_count=len(major),
        warning_duration_minutes_total=sum(gap.elapsed_minutes for gap in warning),
        major_duration_minutes_total=sum(gap.elapsed_minutes for gap in major),
        written_row_count=len(written),
    )


def contiguous_segments(
    timestamps: Sequence[datetime],
    config: DiagnosticsConfig,
) -> tuple[GlucoseSegment, ...]:
    """Split chronologically sorted timestamps on major gaps only."""
    ordered = list(timestamps)
    if not ordered:
        return ()
    major = timedelta(minutes=config.gap_major_minutes)
    segments: list[GlucoseSegment] = []
    start = ordered[0]
    count = 1
    end = ordered[0]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current - previous > major:
            segments.append(
                GlucoseSegment(
                    start_timestamp_utc=start,
                    end_timestamp_utc=end,
                    observation_count=count,
                    elapsed_minutes=(end - start).total_seconds() / 60.0,
                )
            )
            start = current
            count = 1
            end = current
        else:
            count += 1
            end = current
    segments.append(
        GlucoseSegment(
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            observation_count=count,
            elapsed_minutes=(end - start).total_seconds() / 60.0,
        )
    )
    return tuple(segments)
