"""CLI tests for diagnose-snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from health_ml.cli import main
from health_ml.datasets.snapshot import WORKOUTS_ARROW_SCHEMA
from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.errors import DiagnosticsValidationError
from tests.conftest import FakeHealthClient, replace_category_parquet, write_snapshot


def _argv(snapshot: Path, output: Path, *extra: str) -> list[str]:
    return ["diagnose-snapshot", "--snapshot", str(snapshot), "--output", str(output), *extra]


def test_valid_invocation_succeeds(tmp_path: Path, populated_client: FakeHealthClient, capsys):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    code = main(_argv(snapshot, tmp_path / "diagnostics"))
    captured = capsys.readouterr()
    assert code == 0
    assert "Snapshot diagnostics complete" in captured.out
    assert "Input snapshot:" in captured.out
    assert "warning gaps" in captured.out
    assert "Forecast readiness:" in captured.out
    assert "+30m:" in captured.out
    assert "No episodes, targets, features, or models were generated." in captured.out
    assert "rice" not in captured.out
    assert "chicken" not in captured.out


def test_invalid_config_returns_nonzero(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    code = main(
        _argv(
            snapshot,
            tmp_path / "diagnostics",
            "--gap-warning-minutes",
            "30",
            "--gap-major-minutes",
            "10",
        )
    )
    assert code != 0


def test_invalid_snapshot_returns_nonzero(tmp_path: Path):
    snapshot = write_snapshot(tmp_path, FakeHealthClient()).output_dir
    t0 = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    table = pa.Table.from_pylist(
        [
            {
                "workout_id": "bad-interval",
                "start": t0,
                "end": t0,
                "sport": "running",
                "distance_meters": None,
                "source": "apple_health",
            }
        ],
        schema=WORKOUTS_ARROW_SCHEMA,
    )
    replace_category_parquet(snapshot, "workouts", table)
    assert main(_argv(snapshot, tmp_path / "diagnostics")) != 0


def test_missing_snapshot_returns_nonzero(tmp_path: Path):
    code = main(_argv(tmp_path / "missing-snapshot", tmp_path / "diagnostics"))
    assert code != 0


def test_existing_artifact_collision_returns_nonzero(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    output = tmp_path / "diagnostics"
    assert main(_argv(snapshot, output)) == 0
    assert main(_argv(snapshot, output)) == 1
    assert main(_argv(snapshot, output, "--overwrite")) == 0


def test_config_rejects_tolerance_at_least_min_horizon():
    with pytest.raises(DiagnosticsValidationError, match="target-tolerance-minutes"):
        DiagnosticsConfig(target_tolerance_minutes=30, horizons_minutes=(30, 60, 120))


def test_final_summary_is_aggregate_only(tmp_path: Path, populated_client: FakeHealthClient, capsys):
    snapshot = write_snapshot(tmp_path, populated_client).output_dir
    main(_argv(snapshot, tmp_path / "diagnostics"))
    out = capsys.readouterr().out
    assert "http://" not in out
    assert "Authorization" not in out
    assert "query-api.test" not in out
    assert "Bearer" not in out
