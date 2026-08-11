"""Unit tests for Pydantic export / entity validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.schemas.export import (
    GlucoseSampleIn,
    HealthExportPayload,
    MealEventIn,
    SleepIntervalIn,
    WeightMeasurementIn,
    WorkoutIn,
)


def _base_payload(**overrides):
    data = {
        "schema_version": 1,
        "exported_at": "2026-08-10T20:00:00Z",
        "data_start": "2026-07-30T00:00:00Z",
        "data_end": "2026-08-10T20:00:00Z",
        "glucose_samples": [],
        "workouts": [],
        "sleep_sessions": [],
        "weight_measurements": [],
        "meal_events": [],
    }
    data.update(overrides)
    return data


def test_valid_schema_version_1_parses():
    payload = HealthExportPayload.model_validate(_base_payload())
    payload.enforce_phase1_contract()
    assert payload.schema_version == 1


def test_unsupported_schema_version_rejected():
    payload = HealthExportPayload.model_validate(_base_payload(schema_version=2))
    with pytest.raises(AppError) as exc:
        payload.enforce_phase1_contract()
    assert exc.value.code == "UNSUPPORTED_SCHEMA_VERSION"
    assert exc.value.details == {"supported_versions": [1]}


def test_unknown_optional_top_level_keys_ignored():
    payload = HealthExportPayload.model_validate(
        _base_payload(complete=True, errors=[], export_id="ignored")
    )
    payload.enforce_phase1_contract()
    assert payload.schema_version == 1


def test_data_end_before_start_rejected():
    payload = HealthExportPayload.model_validate(
        _base_payload(
            data_start="2026-08-10T00:00:00Z",
            data_end="2026-08-01T00:00:00Z",
        )
    )
    with pytest.raises(AppError) as exc:
        payload.enforce_phase1_contract()
    assert exc.value.code == "INVALID_TIMESTAMP"


def test_glucose_unit_other_than_mg_dl_rejected():
    with pytest.raises(ValidationError):
        GlucoseSampleIn.model_validate(
            {
                "source": "apple_health",
                "source_sample_id": "g1",
                "sample_time": "2026-08-01T00:00:00Z",
                "value": 100,
                "unit": "mmol/L",
            }
        )


def test_glucose_value_uses_decimal():
    sample = GlucoseSampleIn.model_validate(
        {
            "source": "apple_health",
            "source_sample_id": "g1",
            "sample_time": "2026-08-01T00:00:00Z",
            "value": "95.5",
            "unit": "mg/dL",
        }
    )
    assert isinstance(sample.value, Decimal)
    assert sample.value == Decimal("95.5")


def test_weight_unit_other_than_kg_rejected():
    with pytest.raises(ValidationError):
        WeightMeasurementIn.model_validate(
            {
                "source": "apple_health",
                "source_sample_id": "w1",
                "measured_at": "2026-08-01T00:00:00Z",
                "value": 180,
                "unit": "lb",
            }
        )


def test_meal_missing_completed_at_rejected():
    with pytest.raises(ValidationError):
        MealEventIn.model_validate(
            {
                "source": "manual",
                "source_sample_id": "m1",
                "foods": ["eggs"],
            }
        )


def test_meal_start_forbidden():
    with pytest.raises(ValidationError):
        MealEventIn.model_validate(
            {
                "source": "manual",
                "source_sample_id": "m1",
                "meal_completed_at": "2026-08-01T12:00:00Z",
                "meal_start": "2026-08-01T11:00:00Z",
            }
        )


def test_missing_meal_foods_normalizes_to_empty_list():
    meal = MealEventIn.model_validate(
        {
            "source": "manual",
            "source_sample_id": "m1",
            "meal_completed_at": "2026-08-01T12:00:00Z",
        }
    )
    assert meal.foods == []


def test_missing_meal_notes_normalizes_to_null():
    meal = MealEventIn.model_validate(
        {
            "source": "manual",
            "source_sample_id": "m1",
            "meal_completed_at": "2026-08-01T12:00:00Z",
            "foods": [],
        }
    )
    assert meal.notes is None


def test_unknown_sleep_stage_preserved():
    sleep = SleepIntervalIn.model_validate(
        {
            "source": "apple_health",
            "source_sample_id": "s1",
            "start_time": "2026-08-01T01:00:00Z",
            "end_time": "2026-08-01T01:20:00Z",
            "stage": "light",
        }
    )
    assert sleep.stage == "light"


def test_workout_end_before_start_rejected():
    with pytest.raises(ValidationError):
        WorkoutIn.model_validate(
            {
                "source": "apple_health",
                "source_name": "Strava",
                "source_sample_id": "r1",
                "sport": "running",
                "start_time": "2026-08-01T02:00:00Z",
                "end_time": "2026-08-01T01:00:00Z",
            }
        )


def test_non_running_workout_rejected():
    with pytest.raises(ValidationError):
        WorkoutIn.model_validate(
            {
                "source": "apple_health",
                "source_name": "Strava",
                "source_sample_id": "r1",
                "sport": "cycling",
                "start_time": "2026-08-01T01:00:00Z",
                "end_time": "2026-08-01T02:00:00Z",
            }
        )


def test_non_strava_workout_rejected():
    with pytest.raises(ValidationError) as exc:
        WorkoutIn.model_validate(
            {
                "source": "apple_health",
                "source_name": "Nike Run Club",
                "source_sample_id": "r1",
                "sport": "running",
                "start_time": "2026-08-01T01:00:00Z",
                "end_time": "2026-08-01T02:00:00Z",
            }
        )
    assert "UNSUPPORTED_WORKOUT_SOURCE" in str(exc.value)
