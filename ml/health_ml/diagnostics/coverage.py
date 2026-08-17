"""Daily coverage and structural category summaries. Pure functions; no I/O."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.loader import LoadedSnapshot
from health_ml.diagnostics.models import (
    CategoryStructuralSummary,
    DailyCoverageRow,
    DailyCoverageSummary,
    NumericDistribution,
    SchemaStatus,
)
from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)
from health_ml.times import interval_extends_beyond_bounds, interval_overlaps, point_in_range

__all__ = [
    "coverage_ratio_estimate",
    "daily_coverage",
    "interval_overlaps",
    "local_day_bounds",
    "local_dates_touched",
    "point_in_range",
    "structural_summaries",
]


def coverage_ratio_estimate(observed: int, expected: int) -> float | None:
    if expected == 0:
        return None
    return observed / expected


def local_day_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=timezone)
    next_day = day + timedelta(days=1)
    end_local = datetime.combine(next_day, time.min, tzinfo=timezone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def local_dates_touched(start: datetime, end: datetime, timezone: ZoneInfo) -> tuple[date, ...]:
    if end <= start:
        return ()
    first = start.astimezone(timezone).date()
    last = (end - timedelta(microseconds=1)).astimezone(timezone).date()
    days: list[date] = []
    current = first
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def daily_coverage(
    snapshot: LoadedSnapshot,
    config: DiagnosticsConfig,
) -> DailyCoverageSummary:
    tz = ZoneInfo(config.display_timezone)
    start = snapshot.manifest.source_start
    end = snapshot.manifest.source_end
    days = local_dates_touched(start, end, tz)
    allowed = set(days)
    glucose_counts = _point_counts(snapshot.glucose, lambda row: row.timestamp, tz, allowed)
    meal_counts = _point_counts(snapshot.meals, lambda row: row.timestamp, tz, allowed)
    weight_counts = _point_counts(snapshot.weight, lambda row: row.timestamp, tz, allowed)
    workout_counts = _interval_overlap_counts(snapshot.workouts, tz, allowed)
    sleep_counts = _interval_overlap_counts(snapshot.sleep, tz, allowed)

    rows: list[DailyCoverageRow] = []
    for day in days:
        day_start, day_end = local_day_bounds(day, tz)
        elapsed_minutes = (day_end - day_start).total_seconds() / 60.0
        expected = int(elapsed_minutes // config.expected_cgm_cadence_minutes)
        observed = glucose_counts[day]
        rows.append(
            DailyCoverageRow(
                local_date=day,
                glucose_observed_count=observed,
                glucose_expected_count_estimate=expected,
                glucose_coverage_ratio_estimate=coverage_ratio_estimate(observed, expected),
                meal_count=meal_counts[day],
                workout_overlap_count=workout_counts[day],
                raw_sleep_interval_overlap_count=sleep_counts[day],
                weight_count=weight_counts[day],
            )
        )
    return DailyCoverageSummary(
        local_day_count=len(rows),
        local_days_with_glucose=sum(1 for row in rows if row.glucose_observed_count > 0),
        rows=tuple(rows),
    )


def _point_counts(
    records: Sequence[GlucoseRecord | MealRecord | WeightRecord],
    timestamp_of: Callable[[GlucoseRecord | MealRecord | WeightRecord], datetime],
    tz: ZoneInfo,
    allowed: set[date],
) -> Counter[date]:
    counts: Counter[date] = Counter()
    for row in records:
        day = timestamp_of(row).astimezone(tz).date()
        if day in allowed:
            counts[day] += 1
    return counts


def _interval_overlap_counts(
    records: Sequence[WorkoutRecord | SleepInterval],
    tz: ZoneInfo,
    allowed: set[date],
) -> Counter[date]:
    counts: Counter[date] = Counter()
    for row in records:
        for day in _local_days_overlapping_interval(row.start, row.end, tz):
            if day in allowed:
                counts[day] += 1
    return counts


def _local_days_overlapping_interval(
    interval_start: datetime,
    interval_end: datetime,
    tz: ZoneInfo,
) -> tuple[date, ...]:
    if interval_end <= interval_start:
        return ()
    first = interval_start.astimezone(tz).date()
    last = (interval_end - timedelta(microseconds=1)).astimezone(tz).date()
    days: list[date] = []
    current = first
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def structural_summaries(snapshot: LoadedSnapshot) -> dict[str, CategoryStructuralSummary]:
    start = snapshot.manifest.source_start
    end = snapshot.manifest.source_end
    return {
        "glucose": _point_summary(
            category="glucose",
            records=snapshot.glucose,
            timestamps=[row.timestamp for row in snapshot.glucose],
            start=start,
            end=end,
            sort_status=snapshot.category_meta["glucose"].source_sort_status,
            duplicate_identifier_count=None,
            duplicate_timestamp_count=_duplicate_count([row.timestamp for row in snapshot.glucose]),
        ),
        "meals": _point_summary(
            category="meals",
            records=snapshot.meals,
            timestamps=[row.timestamp for row in snapshot.meals],
            start=start,
            end=end,
            sort_status=snapshot.category_meta["meals"].source_sort_status,
            duplicate_identifier_count=_duplicate_count([row.meal_id for row in snapshot.meals]),
        ),
        "workouts": _interval_summary(
            category="workouts",
            records=snapshot.workouts,
            start=start,
            end=end,
            sort_status=snapshot.category_meta["workouts"].source_sort_status,
            duplicate_identifier_count=_duplicate_count(
                [row.workout_id for row in snapshot.workouts]
            ),
            invalid_interval_count=snapshot.category_meta["workouts"].invalid_interval_count,
        ),
        "sleep": _interval_summary(
            category="sleep",
            records=snapshot.sleep,
            start=start,
            end=end,
            sort_status=snapshot.category_meta["sleep"].source_sort_status,
            duplicate_identifier_count=_duplicate_count([row.sleep_id for row in snapshot.sleep]),
            invalid_interval_count=snapshot.category_meta["sleep"].invalid_interval_count,
        ),
        "weight": _point_summary(
            category="weight",
            records=snapshot.weight,
            timestamps=[row.timestamp for row in snapshot.weight],
            start=start,
            end=end,
            sort_status=snapshot.category_meta["weight"].source_sort_status,
            duplicate_identifier_count=_duplicate_count(
                [row.weight_id for row in snapshot.weight]
            ),
        ),
    }


def _duplicate_count(values: Sequence[object]) -> int:
    return len(values) - len(set(values))


def _schema_status(invalid_interval_count: int) -> SchemaStatus:
    return "invalid_intervals" if invalid_interval_count else "valid"


def _point_summary(
    *,
    category: str,
    records: Sequence[GlucoseRecord | MealRecord | WeightRecord],
    timestamps: Sequence[datetime],
    start: datetime,
    end: datetime,
    sort_status,
    duplicate_identifier_count: int | None,
    duplicate_timestamp_count: int | None = None,
) -> CategoryStructuralSummary:
    in_range = sum(1 for ts in timestamps if point_in_range(ts, start, end))
    return CategoryStructuralSummary(
        category=category,
        row_count=len(records),
        earliest_timestamp_utc=min(timestamps) if timestamps else None,
        latest_timestamp_utc=max(timestamps) if timestamps else None,
        in_declared_interval_count=in_range,
        outside_declared_interval_count=len(timestamps) - in_range,
        duplicate_identifier_count=duplicate_identifier_count,
        source_sort_status=sort_status,
        duplicate_timestamp_count=duplicate_timestamp_count,
    )


def _interval_summary(
    *,
    category: str,
    records: Sequence[WorkoutRecord | SleepInterval],
    start: datetime,
    end: datetime,
    sort_status,
    duplicate_identifier_count: int,
    invalid_interval_count: int,
) -> CategoryStructuralSummary:
    overlapping = sum(1 for row in records if interval_overlaps(row.start, row.end, start, end))
    non_overlapping = len(records) - overlapping
    extends_beyond = sum(
        1 for row in records if interval_extends_beyond_bounds(row.start, row.end, start, end)
    )
    durations = [(row.end - row.start).total_seconds() / 60.0 for row in records]
    starts = [row.start for row in records]
    ends = [row.end for row in records]
    earliest = min(starts) if starts else None
    latest = max(ends) if ends else None
    return CategoryStructuralSummary(
        category=category,
        row_count=len(records) + invalid_interval_count,
        earliest_timestamp_utc=earliest,
        latest_timestamp_utc=latest,
        in_declared_interval_count=overlapping,
        outside_declared_interval_count=non_overlapping,
        duplicate_identifier_count=duplicate_identifier_count,
        source_sort_status=sort_status,
        schema_validation_status=_schema_status(invalid_interval_count),
        invalid_interval_count=invalid_interval_count,
        overlapping_snapshot_count=overlapping,
        non_overlapping_count=non_overlapping,
        extends_beyond_bounds_count=extends_beyond,
        duration_minutes=NumericDistribution.from_values(durations, include_mean=True),
    )
