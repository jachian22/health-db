"""CLI argument parsing and exit codes."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from health_ml.cli import main, parse_cli_datetime
from health_ml.config import get_settings
from tests.conftest import FakeHealthClient


def test_parse_cli_datetime_accepts_z_suffix():
    value = parse_cli_datetime("2026-08-01T00:00:00Z")
    assert value.tzinfo is not None
    assert value.hour == 0


def test_parse_cli_datetime_rejects_naive():
    with pytest.raises(argparse.ArgumentTypeError, match="timezone"):
        parse_cli_datetime("2026-08-01T00:00:00")


def test_cli_build_dataset_success(tmp_path: Path, populated_client: FakeHealthClient, capsys):
    code = main(
        [
            "build-dataset",
            "--start",
            "2026-08-01T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
            "--output",
            str(tmp_path),
        ],
        client=populated_client,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Snapshot complete" in captured.out
    assert "rows:" in captured.out
    assert "duplicate timestamps:" in captured.out
    assert "out of window:" in captured.out
    assert "gaps > 15m:" in captured.out
    assert (tmp_path / "v0.1_20260801T000000Z_20260816T000000Z" / "manifest.json").exists()


def test_cli_refuses_to_overwrite(tmp_path: Path, populated_client: FakeHealthClient):
    argv = [
        "build-dataset",
        "--start",
        "2026-08-01T00:00:00Z",
        "--end",
        "2026-08-16T00:00:00Z",
        "--output",
        str(tmp_path),
    ]
    assert main(argv, client=populated_client) == 0
    assert main(argv, client=populated_client) == 1
    assert main([*argv, "--overwrite"], client=populated_client) == 0


def test_cli_missing_config_exits_nonzero(monkeypatch, tmp_path: Path):
    def boom() -> None:
        from pydantic import BaseModel

        class Required(BaseModel):
            health_api_url: str

        Required()

    monkeypatch.setattr("health_ml.cli.get_settings", boom)
    get_settings.cache_clear()
    code = main(
        [
            "build-dataset",
            "--start",
            "2026-08-01T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
            "--output",
            str(tmp_path),
        ]
    )
    assert code == 1
