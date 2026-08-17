"""CLI tests for build-episodes. Aggregate output only; no raw personal values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from health_ml.cli import main
from health_ml.schemas.canonical import MealRecord
from tests.conftest import FakeHealthClient, regular_glucose, write_snapshot_and_diagnostics

T0 = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
FOOD = "secret-food-xyz"
GLUCOSE_VALUE = 187.3


def _argv(snapshot: Path, diagnostics: Path, output: Path, *extra: str) -> list[str]:
    return [
        "build-episodes",
        "--snapshot",
        str(snapshot),
        "--diagnostics",
        str(diagnostics),
        "--output",
        str(output),
        *extra,
    ]


def test_valid_fixture_invocation_succeeds(tmp_path: Path, capsys):
    snapshot, diagnostics = write_snapshot_and_diagnostics(
        tmp_path,
        FakeHealthClient(
            glucose=regular_glucose(T0, count=49, value=GLUCOSE_VALUE),
            meals=[
                MealRecord(
                    meal_id="meal-1",
                    timestamp=T0 + timedelta(minutes=30),
                    foods=[FOOD],
                    source="manual",
                )
            ],
        ),
    )
    code = main(_argv(snapshot.output_dir, diagnostics.output_dir, tmp_path / "episodes"))
    captured = capsys.readouterr()
    assert code == 0
    assert "Episode dataset complete" in captured.out
    assert "Accepted episodes: 1" in captured.out
    assert "No interpolation, imputation, feature engineering, splitting, or model training occurred." in captured.out
    assert FOOD not in captured.out
    assert str(GLUCOSE_VALUE) not in captured.out
    assert "http://" not in captured.out
    assert "Authorization" not in captured.out


def test_valid_zero_episode_invocation_succeeds(tmp_path: Path, populated_client, capsys):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    code = main(_argv(snapshot.output_dir, diagnostics.output_dir, tmp_path / "episodes"))
    captured = capsys.readouterr()
    assert code == 0
    assert "Accepted episodes: 0" in captured.out
    assert "Rejected anchors:" in captured.out
    assert "Top rejection reasons:" in captured.out


def test_invalid_config_returns_nonzero(tmp_path: Path, populated_client):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    code = main(
        _argv(
            snapshot.output_dir,
            diagnostics.output_dir,
            tmp_path / "episodes",
            "--history-minutes",
            "120",
            "--grid-cadence-minutes",
            "7",
        )
    )
    assert code != 0


def test_mismatched_snapshot_diagnostics_returns_nonzero(tmp_path: Path, populated_client):
    _first, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    from tests.conftest import write_snapshot

    other = write_snapshot(tmp_path / "other", FakeHealthClient())
    code = main(_argv(other.output_dir, diagnostics.output_dir, tmp_path / "episodes"))
    assert code != 0


def test_output_collision_returns_nonzero(tmp_path: Path, populated_client):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    output = tmp_path / "episodes"
    assert main(_argv(snapshot.output_dir, diagnostics.output_dir, output)) == 0
    assert main(_argv(snapshot.output_dir, diagnostics.output_dir, output)) == 1
    assert main(_argv(snapshot.output_dir, diagnostics.output_dir, output, "--overwrite")) == 0


def test_unsupported_target_policy_returns_nonzero(tmp_path: Path, populated_client):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    code = main(
        _argv(
            snapshot.output_dir,
            diagnostics.output_dir,
            tmp_path / "episodes",
            "--target-policy",
            "nearest",
        )
    )
    assert code != 0
