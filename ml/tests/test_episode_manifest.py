"""Episode manifest and identity provenance tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.manifest import episode_dataset_id_for
from health_ml.episodes.runner import run_episodes
from tests.conftest import FakeHealthClient, regular_glucose, write_snapshot_and_diagnostics

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def test_manifest_lists_checksummed_outputs(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(
        tmp_path, FakeHealthClient(glucose=regular_glucose(T0, count=49))
    )
    result = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        git_sha="test-git",
    )
    manifest = result.manifest
    assert manifest.episode_dataset_schema_version == "2.0"
    assert set(manifest.files) == {
        "episodes.parquet",
        "episode_glucose_history.parquet",
        "episode_targets.parquet",
        "episode_events.parquet",
        "rejected_anchors.parquet",
        "diagnostics.json",
        "README.md",
    }
    assert "manifest.json" not in manifest.files
    assert manifest.configuration.history_minutes == 120
    assert manifest.git_sha == "test-git"


def test_created_at_does_not_change_dataset_id(tmp_path: Path):
    snapshot, diagnostics = write_snapshot_and_diagnostics(
        tmp_path, FakeHealthClient(glucose=regular_glucose(T0, count=4))
    )
    first = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes",
        git_sha="test-git",
        created_at=datetime(2026, 8, 16, 10, 0, tzinfo=UTC),
    )
    second = run_episodes(
        snapshot.output_dir,
        diagnostics.output_dir,
        tmp_path / "episodes-other",
        git_sha="test-git",
        created_at=datetime(2026, 8, 16, 11, 0, tzinfo=UTC),
    )
    assert first.episode_dataset_id == second.episode_dataset_id
    assert first.output_dir.name == second.output_dir.name


def test_semantic_config_change_changes_dataset_id():
    kwargs = dict(
        snapshot_id="snap",
        snapshot_manifest_sha256="a" * 64,
        diagnostics_id="diag",
        diagnostics_manifest_sha256="b" * 64,
        code_version="0.1.0",
        git_sha=None,
    )
    left = episode_dataset_id_for(config=EpisodeConfig(), **kwargs)
    right = episode_dataset_id_for(config=EpisodeConfig(max_history_gap_minutes=10), **kwargs)
    assert left != right
