"""Historical event context for accepted episodes. No nutrition or label inference."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from health_ml.episodes.contracts import EventRow, EventType
from health_ml.schemas.canonical import (
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
    require_aware_utc,
)
from health_ml.times import interval_overlaps


def select_event_context(
    *,
    episode_id: str,
    history_start: datetime,
    anchor: datetime,
    meals: Sequence[MealRecord],
    workouts: Sequence[WorkoutRecord],
    sleep: Sequence[SleepInterval],
    weight: Sequence[WeightRecord],
) -> tuple[EventRow, ...]:
    """Return source events that belong to the historical input window only."""
    start = require_aware_utc(history_start, field_name="history_start")
    end = require_aware_utc(anchor, field_name="anchor")
    rows: list[EventRow] = []
    for meal in meals:
        if _point_in_closed(meal.timestamp, start, end):
            rows.append(
                _point_event(
                    episode_id=episode_id,
                    event_type="meal",
                    event_id=meal.meal_id,
                    timestamp=meal.timestamp,
                    source=meal.source,
                )
            )
    for item in weight:
        if _point_in_closed(item.timestamp, start, end):
            rows.append(
                _point_event(
                    episode_id=episode_id,
                    event_type="weight",
                    event_id=item.weight_id,
                    timestamp=item.timestamp,
                    source=item.source,
                )
            )
    for workout in workouts:
        if interval_overlaps(workout.start, workout.end, start, end):
            rows.append(
                _interval_event(
                    episode_id=episode_id,
                    event_type="workout",
                    event_id=workout.workout_id,
                    start_ts=workout.start,
                    end_ts=workout.end,
                    source=workout.source,
                    history_start=start,
                    anchor=end,
                )
            )
    for interval in sleep:
        if interval_overlaps(interval.start, interval.end, start, end):
            rows.append(
                _interval_event(
                    episode_id=episode_id,
                    event_type="sleep_interval",
                    event_id=interval.sleep_id,
                    start_ts=interval.start,
                    end_ts=interval.end,
                    source=interval.source,
                    history_start=start,
                    anchor=end,
                )
            )
    rows.sort(
        key=lambda row: (
            row.event_type,
            row.event_timestamp_utc or row.event_start_timestamp_utc or datetime.min.replace(tzinfo=UTC),
            row.event_id or "",
        )
    )
    return tuple(rows)


def context_counts(rows: Sequence[EventRow]) -> tuple[int, int, int, int]:
    meals = sum(1 for row in rows if row.event_type == "meal")
    workouts = sum(1 for row in rows if row.event_type == "workout")
    sleep = sum(1 for row in rows if row.event_type == "sleep_interval")
    weight = sum(1 for row in rows if row.event_type == "weight")
    return meals, workouts, sleep, weight


def overlap_duration_seconds(
    event_start: datetime,
    event_end: datetime,
    history_start: datetime,
    anchor: datetime,
) -> float:
    overlap_start = max(event_start, history_start)
    overlap_end = min(event_end, anchor)
    elapsed = (overlap_end - overlap_start).total_seconds()
    return elapsed if elapsed > 0 else 0.0


def _point_event(
    *,
    episode_id: str,
    event_type: EventType,
    event_id: str,
    timestamp: datetime,
    source: str | None,
) -> EventRow:
    return EventRow(
        episode_id=episode_id,
        event_type=event_type,
        event_id=event_id,
        event_timestamp_utc=timestamp,
        event_start_timestamp_utc=None,
        event_end_timestamp_utc=None,
        source=source,
        overlap_duration_seconds=None,
    )


def _interval_event(
    *,
    episode_id: str,
    event_type: EventType,
    event_id: str,
    start_ts: datetime,
    end_ts: datetime,
    source: str | None,
    history_start: datetime,
    anchor: datetime,
) -> EventRow:
    return EventRow(
        episode_id=episode_id,
        event_type=event_type,
        event_id=event_id,
        event_timestamp_utc=None,
        event_start_timestamp_utc=start_ts,
        event_end_timestamp_utc=end_ts,
        source=source,
        overlap_duration_seconds=overlap_duration_seconds(
            start_ts, end_ts, history_start, anchor
        ),
    )


def _point_in_closed(timestamp: datetime, start: datetime, end: datetime) -> bool:
    return start <= timestamp <= end
