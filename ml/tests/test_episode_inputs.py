"""Episode input integrity: snapshot + diagnostics pairing. No network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from health_ml.datasets.manifest import sha256_file
from health_ml.episodes.inputs import load_episode_inputs
from health_ml.errors import EpisodeValidationError
from tests.conftest import (
    FakeHealthClient,
    replace_category_parquet,
    write_snapshot,
    write_snapshot_and_diagnostics,
)


def _digests(path: Path) -> dict[str, str]:
    return {item.name: sha256_file(item) for item in sorted(path.iterdir()) if item.is_file()}


def test_valid_matching_inputs_load(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    loaded = load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)
    assert loaded.snapshot.snapshot_id == snapshot.snapshot_id
    assert loaded.diagnostics.diagnostics_id == diagnostics.diagnostics_id
    assert loaded.diagnostics.manifest.input_snapshot_id == snapshot.snapshot_id
    assert loaded.diagnostics.manifest.input_snapshot_manifest_sha256 == loaded.snapshot.manifest_sha256


def test_diagnostics_linked_to_different_snapshot_fails(tmp_path: Path, populated_client: FakeHealthClient):
    _first, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    other = write_snapshot(tmp_path / "other", FakeHealthClient())
    with pytest.raises(EpisodeValidationError, match="does not match"):
        load_episode_inputs(other.output_dir, diagnostics.output_dir)


def test_snapshot_manifest_checksum_mismatch_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    payload = json.loads((snapshot.output_dir / "manifest.json").read_text(encoding="utf-8"))
    payload["checksums"]["glucose"] = "0" * 64
    payload["artifacts"]["glucose"]["sha256"] = "0" * 64
    (snapshot.output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EpisodeValidationError, match="SHA-256"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_diagnostics_manifest_checksum_mismatch_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    payload = json.loads((diagnostics.output_dir / "manifest.json").read_text(encoding="utf-8"))
    payload["files"]["forecast_readiness.parquet"]["sha256"] = "0" * 64
    (diagnostics.output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EpisodeValidationError, match="SHA-256"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_unsupported_diagnostics_schema_version_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    payload = json.loads((diagnostics.output_dir / "manifest.json").read_text(encoding="utf-8"))
    payload["diagnostics_schema_version"] = "9.9"
    (diagnostics.output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EpisodeValidationError, match="Unsupported diagnostics_schema_version"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_missing_required_source_file_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    (snapshot.output_dir / "weight.parquet").unlink()
    with pytest.raises(EpisodeValidationError, match="missing required"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_missing_required_diagnostics_file_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    (diagnostics.output_dir / "forecast_readiness.parquet").unlink()
    with pytest.raises(EpisodeValidationError, match="missing required"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_unsupported_forecast_readiness_schema_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    table = pa.table({"value": [1.0]})
    path = diagnostics.output_dir / "forecast_readiness.parquet"
    pq.write_table(table, path, compression="zstd")
    payload = json.loads((diagnostics.output_dir / "manifest.json").read_text(encoding="utf-8"))
    payload["files"]["forecast_readiness.parquet"]["sha256"] = sha256_file(path)
    (diagnostics.output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EpisodeValidationError, match="forecast_readiness"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_loader_never_writes_to_input_directories(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    before_snap = _digests(snapshot.output_dir)
    before_diag = _digests(diagnostics.output_dir)
    snap_mtimes = {path.name: path.stat().st_mtime_ns for path in snapshot.output_dir.iterdir()}
    diag_mtimes = {path.name: path.stat().st_mtime_ns for path in diagnostics.output_dir.iterdir()}
    load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)
    assert _digests(snapshot.output_dir) == before_snap
    assert _digests(diagnostics.output_dir) == before_diag
    assert {path.name: path.stat().st_mtime_ns for path in snapshot.output_dir.iterdir()} == snap_mtimes
    assert {path.name: path.stat().st_mtime_ns for path in diagnostics.output_dir.iterdir()} == diag_mtimes


def test_canonical_schema_mismatch_fails(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    table = pa.table({"value": [1.0]})
    replace_category_parquet(snapshot.output_dir, "glucose", table)
    with pytest.raises(EpisodeValidationError, match="schema does not match"):
        load_episode_inputs(snapshot.output_dir, diagnostics.output_dir)


def test_diagnostics_under_different_parent_dir_still_loads(tmp_path: Path, populated_client: FakeHealthClient):
    snapshot, diagnostics = write_snapshot_and_diagnostics(tmp_path, populated_client)
    relocated = tmp_path / "relocated-diagnostics" / diagnostics.output_dir.name
    shutil.copytree(diagnostics.output_dir, relocated)
    loaded = load_episode_inputs(snapshot.output_dir, relocated)
    assert loaded.snapshot.snapshot_id == snapshot.snapshot_id
    assert loaded.diagnostics.diagnostics_id == diagnostics.diagnostics_id
    assert relocated.parent.name != snapshot.snapshot_id
