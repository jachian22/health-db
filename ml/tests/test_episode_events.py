"""Historical event-context tests. No food payloads or future leakage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from health_ml.episodes.events import overlap_duration_seconds, select_event_context
from health_ml.schemas.canonical import MealRecord, SleepInterval, WeightRecord, WorkoutRecord

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
ANCHOR = T0 + timedelta(minutes=120)
FOOD = "secret-food-xyz"


def _events(**kwargs):
    return select_event_context(
        episode_id="ep-test",
        history_start=T0,
        anchor=ANCHOR,
        meals=kwargs.get("meals", ()),
        workouts=kwargs.get("workouts", ()),
        sleep=kwargs.get("sleep", ()),
        weight=kwargs.get("weight", ()),
    )


def test_point_events_only_inside_closed_history_window():
    meals = [
        MealRecord(meal_id="before", timestamp=T0 - timedelta(seconds=1), foods=[FOOD], source="manual"),
        MealRecord(meal_id="start", timestamp=T0, foods=[FOOD], source="manual"),
        MealRecord(meal_id="mid", timestamp=T0 + timedelta(minutes=60), foods=[FOOD], source="manual"),
        MealRecord(meal_id="anchor", timestamp=ANCHOR, foods=[FOOD], source="manual"),
        MealRecord(meal_id="future", timestamp=ANCHOR + timedelta(seconds=1), foods=[FOOD], source="manual"),
    ]
    weights = [
        WeightRecord(weight_id="w-before", timestamp=T0 - timedelta(minutes=1), weight_kg=80.0, source="apple_health"),
        WeightRecord(weight_id="w-ok", timestamp=T0 + timedelta(minutes=10), weight_kg=80.1, source="apple_health"),
        WeightRecord(weight_id="w-future", timestamp=ANCHOR + timedelta(minutes=5), weight_kg=80.2, source="apple_health"),
    ]
    rows = _events(meals=meals, weight=weights)
    meal_ids = [row.event_id for row in rows if row.event_type == "meal"]
    weight_ids = [row.event_id for row in rows if row.event_type == "weight"]
    assert meal_ids == ["start", "mid", "anchor"]
    assert "future" not in meal_ids
    assert "before" not in meal_ids
    assert weight_ids == ["w-ok"]
    assert all(row.event_timestamp_utc is not None and row.event_timestamp_utc <= ANCHOR for row in rows if row.event_type in {"meal", "weight"})
    assert all("foods" not in row.to_row() for row in rows)
    serialized = str([row.to_row() for row in rows])
    assert FOOD not in serialized


def test_future_point_events_are_excluded():
    rows = _events(
        meals=[
            MealRecord(
                meal_id="future-meal",
                timestamp=ANCHOR + timedelta(minutes=10),
                foods=["rice"],
                source="manual",
            )
        ]
    )
    assert rows == ()


def test_interval_overlap_rules_and_unclipped_bounds():
    workouts = [
        WorkoutRecord(
            workout_id="ending-at-start",
            start=T0 - timedelta(minutes=30),
            end=T0,
            sport="run",
            source="apple_health",
        ),
        WorkoutRecord(
            workout_id="starting-at-anchor",
            start=ANCHOR,
            end=ANCHOR + timedelta(minutes=30),
            sport="run",
            source="apple_health",
        ),
        WorkoutRecord(
            workout_id="overlap",
            start=T0 - timedelta(minutes=15),
            end=T0 + timedelta(minutes=15),
            sport="run",
            source="apple_health",
        ),
    ]
    sleep = [
        SleepInterval(
            sleep_id="sleep-overlap",
            start=ANCHOR - timedelta(minutes=10),
            end=ANCHOR + timedelta(minutes=40),
            stage="asleep",
            source="apple_health",
        )
    ]
    rows = _events(workouts=workouts, sleep=sleep)
    workout_ids = [row.event_id for row in rows if row.event_type == "workout"]
    assert workout_ids == ["overlap"]
    overlap = rows[0] if rows[0].event_type == "workout" else next(row for row in rows if row.event_id == "overlap")
    assert overlap.event_start_timestamp_utc == T0 - timedelta(minutes=15)
    assert overlap.event_end_timestamp_utc == T0 + timedelta(minutes=15)
    assert overlap.overlap_duration_seconds == 15 * 60
    sleep_row = next(row for row in rows if row.event_type == "sleep_interval")
    assert sleep_row.event_start_timestamp_utc == ANCHOR - timedelta(minutes=10)
    assert sleep_row.event_end_timestamp_utc == ANCHOR + timedelta(minutes=40)
    assert sleep_row.overlap_duration_seconds == 10 * 60
    assert sleep_row.event_id == "sleep-overlap"


def test_derived_overlap_duration_helper():
    start = T0 - timedelta(minutes=30)
    end = T0 + timedelta(minutes=15)
    assert overlap_duration_seconds(start, end, T0, ANCHOR) == 15 * 60
    assert overlap_duration_seconds(T0, ANCHOR, T0, ANCHOR) == 120 * 60
