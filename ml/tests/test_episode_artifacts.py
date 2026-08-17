"""Episode artifact, provenance, target, and causal-leakage tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from health_ml.datasets.manifest import sha256_file
from health_ml.diagnostics.loader import load_snapshot
from health_ml.diagnostics.models import ForecastReadinessRow
from health_ml.episodes.artifacts import (
    OUTPUT_FILES,
    compare_with_readiness,
    validate_causal_history,
    validate_history_invariants,
    validate_source_values_unchanged,
    validate_target_invariants,
)
from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.contracts import (
    EPISODE_EVENTS_ARROW_SCHEMA,
    EPISODE_GLUCOSE_HISTORY_ARROW_SCHEMA,
    EPISODE_TARGETS_ARROW_SCHEMA,
    EPISODES_ARROW_SCHEMA,
    NO_CLINICAL_DISCLOSURE,
    NO_INTERPOLATION_DISCLOSURE,
    REJECTED_ANCHORS_ARROW_SCHEMA,
    EpisodeRow,
    EventRow,
    GlucoseHistoryRow,
    TargetRow,
)
from health_ml.episodes.runner import run_episodes
from health_ml.errors import EpisodeError, EpisodeExistsError
from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)
from tests.conftest import FakeHealthClient, regular_glucose, write_snapshot_and_diagnostics

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
ANCHOR = T0 + timedelta(minutes=120)
FOOD = "secret-food-xyz"
GLUCOSE_VALUE = 187.3


def _ready_client(**extra) -> FakeHealthClient:
    meals = extra.get(
        "meals",
        [
            MealRecord(
                meal_id="meal-history",
                timestamp=T0 + timedelta(minutes=30),
                foods=[FOOD],
                source="manual",
            ),
            MealRecord(
                meal_id="meal-future",
                timestamp=ANCHOR + timedelta(minutes=10),
                foods=[FOOD],
                source="manual",
            ),
        ],
    )
    workouts = extra.get(
        "workouts",
        [
            WorkoutRecord(
                workout_id="workout-history",
                start=T0 + timedelta(minutes=40),
                end=T0 + timedelta(minutes=70),
                sport="running",
                source="apple_health",
            ),
            WorkoutRecord(
                workout_id="workout-future",
                start=ANCHOR + timedelta(minutes=5),
                end=ANCHOR + timedelta(minutes=25),
                sport="running",
                source="apple_health",
            ),
        ],
    )
    sleep = extra.get(
        "sleep",
        [
            SleepInterval(
                sleep_id="sleep-history",
                start=T0 + timedelta(minutes=80),
                end=T0 + timedelta(minutes=100),
                stage="asleep",
                source="apple_health",
            )
        ],
    )
    weight = extra.get(
        "weight",
        [
            WeightRecord(
                weight_id="weight-history",
                timestamp=T0 + timedelta(minutes=15),
                weight_kg=80.0,
                source="apple_health",
            ),
            WeightRecord(
                weight_id="weight-future",
                timestamp=ANCHOR + timedelta(minutes=3),
                weight_kg=80.5,
                source="apple_health",
            ),
        ],
    )
    glucose = extra.get("glucose", regular_glucose(T0, count=49, value=GLUCOSE_VALUE))
    return FakeHealthClient(glucose=glucose, meals=meals, workouts=workouts, sleep=sleep, weight=weight)


def _build(tmp_path: Path, client: FakeHealthClient | None = None, **kwargs):
    snapshot, diagnostics = write_snapshot_and_diagnostics(
        tmp_path, client if client is not None else _ready_client()
    )
    result = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        git_sha="test-git",
        **kwargs,
    )
    return snapshot, diagnostics, result


def test_successful_build_creates_required_files(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    names = {path.name for path in result.output_dir.iterdir()}
    assert names == set(OUTPUT_FILES)
    assert result.accepted_episode_count == 1
    assert result.rejected_anchor_count == 48


def test_parquet_round_trip_schemas(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    schemas = {
        "episodes.parquet": EPISODES_ARROW_SCHEMA,
        "episode_glucose_history.parquet": EPISODE_GLUCOSE_HISTORY_ARROW_SCHEMA,
        "episode_targets.parquet": EPISODE_TARGETS_ARROW_SCHEMA,
        "episode_events.parquet": EPISODE_EVENTS_ARROW_SCHEMA,
        "rejected_anchors.parquet": REJECTED_ANCHORS_ARROW_SCHEMA,
    }
    for filename, schema in schemas.items():
        table = pq.read_table(result.output_dir / filename)
        assert table.schema.equals(schema)
        assert pq.read_table(result.output_dir / filename).to_pylist() == table.to_pylist()


def test_zero_accepted_episode_build_is_schema_valid(tmp_path: Path, populated_client: FakeHealthClient):
    _snapshot, _diagnostics, result = _build(tmp_path, populated_client)
    assert result.accepted_episode_count == 0
    assert result.rejected_anchor_count == 4
    for name in (
        "episodes.parquet",
        "episode_glucose_history.parquet",
        "episode_targets.parquet",
        "episode_events.parquet",
    ):
        table = pq.read_table(result.output_dir / name)
        assert table.num_rows == 0
        assert table.schema.names
    rejected = pq.read_table(result.output_dir / "rejected_anchors.parquet")
    assert rejected.num_rows == 4
    payload = json.loads((result.output_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert payload["accepted_episode_count"] == 0
    assert payload["rejections_by_code"]


def test_file_hashes_match_manifest(tmp_path: Path):
    snapshot, diagnostics, result = _build(tmp_path)
    payload = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    for spec in payload["files"].values():
        path = result.output_dir / spec["path"]
        assert path.is_file()
        assert sha256_file(path) == spec["sha256"]
    assert payload["input_snapshot_id"] == snapshot.snapshot_id
    assert payload["input_snapshot_manifest_sha256"] == sha256_file(snapshot.output_dir / "manifest.json")
    assert payload["input_diagnostics_id"] == diagnostics.diagnostics_id
    assert payload["input_diagnostics_manifest_sha256"] == sha256_file(diagnostics.output_dir / "manifest.json")
    assert "created_at" not in result.episode_dataset_id


def test_output_collision_fails_by_default(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, _ready_client())
    first = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        git_sha="test-git",
        created_at=datetime(2026, 8, 16, 18, 0, tzinfo=UTC),
    )
    with pytest.raises(EpisodeExistsError):
        run_episodes(
            snapshot.output_dir,
            diagnostics.output_dir,
            tmp_path / "episodes",
            git_sha="test-git",
            created_at=datetime(2026, 8, 16, 19, 0, tzinfo=UTC),
        )
    second = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        git_sha="test-git",
        overwrite=True,
        created_at=datetime(2026, 8, 16, 20, 0, tzinfo=UTC),
    )
    assert second.episode_dataset_id == first.episode_dataset_id
    assert second.output_dir == first.output_dir


def test_readme_contains_limitations(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    readme = (result.output_dir / "README.md").read_text(encoding="utf-8")
    assert NO_INTERPOLATION_DISCLOSURE in readme
    assert NO_CLINICAL_DISCLOSURE in readme
    assert "Train/validation/test splits" in readme
    assert FOOD not in readme
    assert str(GLUCOSE_VALUE) not in readme
    json_text = (result.output_dir / "diagnostics.json").read_text(encoding="utf-8")
    assert FOOD not in json_text
    assert str(GLUCOSE_VALUE) not in json_text
    assert FOOD not in result.cli_summary
    assert str(GLUCOSE_VALUE) not in result.cli_summary


def test_targets_are_generic_unique_and_exact(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    episodes = pq.read_table(result.output_dir / "episodes.parquet").to_pylist()
    targets = pq.read_table(result.output_dir / "episode_targets.parquet").to_pylist()
    assert len(episodes) == 1
    episode = episodes[0]
    episode_targets = [row for row in targets if row["episode_id"] == episode["episode_id"]]
    assert [row["horizon_minutes"] for row in episode_targets] == [30, 60, 120]
    for row in episode_targets:
        assert row["target_source_timestamp_utc"] > episode["anchor_timestamp_utc"]
        assert abs(row["target_offset_seconds"]) <= 2.5 * 60
        assert row["target_glucose_mg_dl"] == GLUCOSE_VALUE
    rejected = pq.read_table(result.output_dir / "rejected_anchors.parquet").to_pylist()
    sample = rejected[0]
    assert "horizon_diagnostics" in sample
    assert {item["horizon_minutes"] for item in sample["horizon_diagnostics"]} == {30, 60, 120}


def test_missing_one_horizon_rejects_under_multi_horizon_policy(tmp_path: Path):
    glucose = [row for row in regular_glucose(T0, count=49, value=GLUCOSE_VALUE) if row.timestamp != ANCHOR + timedelta(minutes=60)]
    _snapshot, _diagnostics, result = _build(tmp_path, _ready_client(glucose=glucose))
    assert result.accepted_episode_count == 0
    rejected = pq.read_table(result.output_dir / "rejected_anchors.parquet").to_pylist()
    matching = [row for row in rejected if row["anchor_timestamp_utc"] == ANCHOR]
    assert matching
    assert "MISSING_TARGET_60M" in matching[0]["rejection_codes"]


def test_custom_horizons_use_nested_diagnostics_not_hardcoded_behavior(tmp_path: Path):
    glucose = regular_glucose(T0, count=37, step_minutes=5, value=100.0)
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, FakeHealthClient(glucose=glucose))
    result = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        config=EpisodeConfig(horizons_minutes=(15, 45), history_minutes=120),
        git_sha="test-git",
    )
    rejected = pq.read_table(result.output_dir / "rejected_anchors.parquet").to_pylist()
    assert rejected
    item = rejected[0]
    assert {row["horizon_minutes"] for row in item["horizon_diagnostics"]} == {15, 45}
    assert "target_candidate_count_30m" not in item
    assert "target_candidate_count_60m" not in item
    assert "target_candidate_count_120m" not in item


def test_history_grid_preserves_missing_mask_and_exact_values(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    history = pq.read_table(result.output_dir / "episode_glucose_history.parquet").to_pylist()
    assert len(history) == 25
    assert [row["grid_index"] for row in history] == list(range(25))
    assert all(row["observed_mask"] for row in history)
    assert all(row["glucose_mg_dl"] == GLUCOSE_VALUE for row in history)
    episodes = pq.read_table(result.output_dir / "episodes.parquet").to_pylist()
    assert history[-1]["grid_timestamp_utc"] == episodes[0]["anchor_timestamp_utc"]


def test_include_event_context_false_writes_empty_events(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, _ready_client())
    result = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        config=EpisodeConfig(include_event_context=False),
        git_sha="test-git",
    )
    events = pq.read_table(result.output_dir / "episode_events.parquet")
    assert events.num_rows == 0
    assert events.schema.equals(EPISODE_EVENTS_ARROW_SCHEMA)
    episode = pq.read_table(result.output_dir / "episodes.parquet").to_pylist()[0]
    assert episode["meal_context_count"] == 0
    assert episode["workout_context_count"] == 0
    assert episode["sleep_context_count"] == 0
    assert episode["weight_context_count"] == 0


def test_events_exclude_future_and_foods(tmp_path: Path):
    _snapshot, _diagnostics, result = _build(tmp_path)
    events = pq.read_table(result.output_dir / "episode_events.parquet")
    assert "foods" not in events.column_names
    rows = events.to_pylist()
    types = {row["event_type"] for row in rows}
    assert types == {"meal", "workout", "sleep_interval", "weight"}
    ids = {row["event_id"] for row in rows}
    assert "meal-history" in ids
    assert "meal-future" not in ids
    assert "workout-future" not in ids
    assert "weight-future" not in ids
    blob = str(rows)
    assert FOOD not in blob
    assert "asleep" not in blob


def test_causal_leakage_suite(tmp_path: Path):
    future_glucose = GlucoseRecord(timestamp=ANCHOR + timedelta(minutes=7), glucose_mg_dl=50.0)
    glucose = regular_glucose(T0, count=49, value=GLUCOSE_VALUE) + [future_glucose]
    future_meal = MealRecord(
        meal_id="leaky-meal",
        timestamp=ANCHOR + timedelta(minutes=8),
        foods=[FOOD],
        source="manual",
    )
    _snapshot, _diagnostics, result = _build(
        tmp_path,
        _ready_client(glucose=glucose, meals=[future_meal]),
    )
    history = pq.read_table(result.output_dir / "episode_glucose_history.parquet").to_pylist()
    events = pq.read_table(result.output_dir / "episode_events.parquet").to_pylist()
    targets = pq.read_table(result.output_dir / "episode_targets.parquet").to_pylist()
    assert all(row["glucose_mg_dl"] != 50.0 for row in history)
    assert all(
        row["source_timestamp_utc"] is None or row["source_timestamp_utc"] <= ANCHOR for row in history
    )
    assert all(row["event_id"] != "leaky-meal" for row in events)
    assert all(row["target_source_timestamp_utc"] != future_glucose.timestamp for row in targets)
    without_future = regular_glucose(T0, count=49, value=GLUCOSE_VALUE)
    snapshot2, diagnostics2 = write_snapshot_and_diagnostics(
        tmp_path / "baseline", FakeHealthClient(glucose=without_future)
    )
    baseline = run_episodes(
        snapshot2.output_dir,
        diagnostics2.output_dir,
        tmp_path / "episodes-baseline",
        git_sha="test-git",
    )
    baseline_history = pq.read_table(baseline.output_dir / "episode_glucose_history.parquet").to_pylist()
    assert [row["glucose_mg_dl"] for row in history] == [row["glucose_mg_dl"] for row in baseline_history]
    assert [row["observed_mask"] for row in history] == [row["observed_mask"] for row in baseline_history]


def test_refuses_to_write_under_input_directories(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, _ready_client())
    with pytest.raises(EpisodeError, match="must not be written under"):
        run_episodes(snapshot.output_dir, diagnostics.output_dir, snapshot.output_dir, git_sha="test-git")
    with pytest.raises(EpisodeError, match="must not be written under"):
        run_episodes(
            snapshot.output_dir,
            diagnostics.output_dir,
            diagnostics.output_dir,
            git_sha="test-git",
        )


def test_inputs_are_unchanged_after_build(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, _ready_client())
    before_s = {path.name: sha256_file(path) for path in snapshot.output_dir.iterdir() if path.is_file()}
    before_d = {path.name: sha256_file(path) for path in diagnostics.output_dir.iterdir() if path.is_file()}
    run_episodes(snapshot.output_dir, diagnostics.output_dir, tmp_path / "episodes", git_sha="test-git")
    after_s = {path.name: sha256_file(path) for path in snapshot.output_dir.iterdir() if path.is_file()}
    after_d = {path.name: sha256_file(path) for path in diagnostics.output_dir.iterdir() if path.is_file()}
    assert after_s == before_s
    assert after_d == before_d


def test_self_check_validators_pass_after_successful_build(tmp_path: Path):
    snapshot, _diagnostics, result = _build(tmp_path)
    config = EpisodeConfig()
    episodes = tuple(
        EpisodeRow.model_validate(row)
        for row in pq.read_table(result.output_dir / "episodes.parquet").to_pylist()
    )
    history = tuple(
        GlucoseHistoryRow.model_validate(row)
        for row in pq.read_table(result.output_dir / "episode_glucose_history.parquet").to_pylist()
    )
    targets = tuple(
        TargetRow.model_validate(row)
        for row in pq.read_table(result.output_dir / "episode_targets.parquet").to_pylist()
    )
    events = tuple(
        EventRow.model_validate(row)
        for row in pq.read_table(result.output_dir / "episode_events.parquet").to_pylist()
    )
    loaded = load_snapshot(snapshot.output_dir)
    validate_history_invariants(history, config)
    validate_target_invariants(targets, episodes, config)
    validate_causal_history(history, events, episodes)
    validate_source_values_unchanged(history, targets, loaded.glucose)


def test_readiness_cardinality_mismatch_skips_comparison_instead_of_zeros():
    evaluations = [SimpleNamespace(accepted=True), SimpleNamespace(accepted=False)]
    comparison = compare_with_readiness(
        evaluations,  # type: ignore[arg-type]
        (),
        episode_horizons=(30, 60, 120),
        diagnostics_horizons=(30, 60, 120),
    )
    assert comparison.comparison_status == "skipped_cardinality_mismatch"
    assert comparison.mismatch_summary == {}
    assert "unique_all_requested_horizons" not in comparison.phase_1_5_readiness_counts
    assert comparison.phase_2_final_outcome_counts["accepted_episode_count"] == 1


def test_readiness_matching_cardinality_reports_mismatch_counts():
    stamp = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    readiness = tuple(
        ForecastReadinessRow(
            anchor_timestamp_utc=stamp,
            horizon_minutes=horizon,
            ideal_target_timestamp_utc=stamp + timedelta(minutes=horizon),
            candidate_observation_count=1,
            status="eligible_unique",
        )
        for _ in range(2)
        for horizon in (30, 60, 120)
    )
    evaluations = [SimpleNamespace(accepted=True), SimpleNamespace(accepted=False)]
    comparison = compare_with_readiness(
        evaluations,  # type: ignore[arg-type]
        readiness,
        episode_horizons=(30, 60, 120),
        diagnostics_horizons=(30, 60, 120),
    )
    assert comparison.comparison_status == "compared"
    assert comparison.phase_1_5_readiness_counts["unique_all_requested_horizons"] == 2
    assert comparison.mismatch_summary["phase_1_5_unique_all_horizons_but_phase_2_rejected"] == 1
    assert comparison.mismatch_summary["phase_2_accepted_but_phase_1_5_not_unique_all_horizons"] == 0


def test_rejections_by_horizon_use_full_codes_only(tmp_path: Path):
    glucose = [
        row
        for row in regular_glucose(T0, count=49, value=GLUCOSE_VALUE)
        if row.timestamp != ANCHOR + timedelta(minutes=60)
    ]
    _snapshot, _diagnostics, result = _build(tmp_path, _ready_client(glucose=glucose))
    payload = json.loads((result.output_dir / "diagnostics.json").read_text(encoding="utf-8"))
    by_horizon = payload["rejections_by_horizon"]
    assert "MISSING_TARGET" not in by_horizon["60"]
    assert "AMBIGUOUS_TARGET" not in by_horizon["60"]
    assert by_horizon["60"]["MISSING_TARGET_60M"] >= 1
    assert payload["diagnostics_comparison"]["comparison_status"] == "compared"
