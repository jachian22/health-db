"""Canonical ML schema tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)

AWARE = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
NAIVE = datetime(2026, 8, 5, 16, 0)


def test_valid_glucose_normalizes_to_utc():
    eastern = datetime.fromisoformat("2026-08-05T12:00:00-04:00")
    record = GlucoseRecord(timestamp=eastern, glucose_mg_dl=96.0)
    assert record.timestamp.tzinfo is not None
    assert record.timestamp == datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
    assert record.trend is None
    assert record.source is None


def test_glucose_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        GlucoseRecord(timestamp=NAIVE, glucose_mg_dl=96.0)


def test_glucose_rejects_non_finite_values():
    with pytest.raises(ValidationError):
        GlucoseRecord(timestamp=AWARE, glucose_mg_dl=float("nan"))
    with pytest.raises(ValidationError):
        GlucoseRecord(timestamp=AWARE, glucose_mg_dl=float("inf"))


def test_meal_uses_recorded_timestamp_only():
    record = MealRecord(
        meal_id="meal-1",
        timestamp=AWARE,
        foods=["rice"],
        source="manual",
    )
    assert record.timestamp == AWARE
    assert not hasattr(record, "start")


def test_workout_optional_fields_nullable():
    record = WorkoutRecord(
        workout_id="w1",
        start=AWARE,
        end=AWARE + timedelta(hours=1),
        sport="running",
        source="apple_health",
    )
    assert record.distance_meters is None
    assert record.active_energy is None
    assert record.average_hr is None
    assert record.max_hr is None


def test_workout_rejects_end_before_start():
    with pytest.raises(ValidationError):
        WorkoutRecord(
            workout_id="w1",
            start=AWARE,
            end=AWARE,
            sport="running",
            source="apple_health",
        )


def test_sleep_preserves_interval():
    record = SleepInterval(
        sleep_id="sleep-1",
        start=AWARE,
        end=AWARE + timedelta(hours=7),
        stage="deep",
        source="apple_health",
    )
    assert record.end - record.start == timedelta(hours=7)
    assert record.sleep_id == "sleep-1"


def test_sleep_rejects_inverted_interval():
    with pytest.raises(ValidationError):
        SleepInterval(
            sleep_id="sleep-1",
            start=AWARE + timedelta(hours=1),
            end=AWARE,
            stage="asleep",
            source="apple_health",
        )


def test_weight_requires_positive_finite_kg():
    record = WeightRecord(
        weight_id="weight-1", timestamp=AWARE, weight_kg=82.4, source="apple_health"
    )
    assert record.weight_kg == 82.4
    assert record.weight_id == "weight-1"
    with pytest.raises(ValidationError):
        WeightRecord(weight_id="weight-1", timestamp=AWARE, weight_kg=0, source="apple_health")
    with pytest.raises(ValidationError):
        WeightRecord(weight_id="weight-1", timestamp=NAIVE, weight_kg=82.4, source="apple_health")
