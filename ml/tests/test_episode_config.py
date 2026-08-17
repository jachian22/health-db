"""Episode configuration and dataset-identity tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.manifest import episode_dataset_id_for
from health_ml.errors import EpisodeValidationError


def test_valid_default_config():
    config = EpisodeConfig()
    assert config.history_minutes == 120
    assert config.grid_cadence_minutes == 5
    assert config.grid_position_count == 25
    assert config.horizons_minutes == (30, 60, 120)
    assert config.target_policy == "unique-only"
    assert config.include_event_context is True


def test_non_divisible_history_and_cadence_fails():
    with pytest.raises(EpisodeValidationError, match="divisible"):
        EpisodeConfig(history_minutes=120, grid_cadence_minutes=7)


def test_non_positive_values_fail():
    with pytest.raises(EpisodeValidationError, match="history-minutes"):
        EpisodeConfig(history_minutes=0)
    with pytest.raises(EpisodeValidationError, match="grid-cadence-minutes"):
        EpisodeConfig(grid_cadence_minutes=0)
    with pytest.raises(EpisodeValidationError, match="max-history-gap-minutes"):
        EpisodeConfig(max_history_gap_minutes=0)
    with pytest.raises(EpisodeValidationError, match="target-tolerance-minutes"):
        EpisodeConfig(target_tolerance_minutes=0)
    with pytest.raises(EpisodeValidationError, match="history-start-tolerance-minutes"):
        EpisodeConfig(history_start_tolerance_minutes=-1)


def test_duplicate_and_unsorted_horizons_fail():
    with pytest.raises(EpisodeValidationError, match="unique"):
        EpisodeConfig(horizons_minutes=(30, 30, 60))
    with pytest.raises(EpisodeValidationError, match="ascending"):
        EpisodeConfig(horizons_minutes=(60, 30, 120))
    with pytest.raises(EpisodeValidationError, match="positive"):
        EpisodeConfig(horizons_minutes=(-30, 60, 120))


def test_unsupported_target_policy_fails():
    with pytest.raises((EpisodeValidationError, ValidationError)):
        EpisodeConfig(target_policy="nearest")  # type: ignore[arg-type]


def test_artifact_id_is_stable_and_semantic():
    kwargs = dict(
        snapshot_id="snap-1",
        snapshot_manifest_sha256="a" * 64,
        diagnostics_id="d1.5_abc",
        diagnostics_manifest_sha256="b" * 64,
        config=EpisodeConfig(),
        code_version="0.1.0",
        git_sha="abc123",
    )
    first = episode_dataset_id_for(**kwargs)
    second = episode_dataset_id_for(**kwargs)
    assert first == second
    assert first.startswith("e2.0_")

    changed = episode_dataset_id_for(**{**kwargs, "config": EpisodeConfig(history_minutes=180)})
    assert changed != first

    no_git = episode_dataset_id_for(**{**kwargs, "git_sha": None})
    assert no_git != first


def test_created_at_is_not_part_of_identity():
    kwargs = dict(
        snapshot_id="snap-1",
        snapshot_manifest_sha256="a" * 64,
        diagnostics_id="d1.5_abc",
        diagnostics_manifest_sha256="b" * 64,
        config=EpisodeConfig(),
    )
    assert "created_at" not in episode_dataset_id_for(**kwargs)
