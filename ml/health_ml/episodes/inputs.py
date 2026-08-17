"""Load and integrity-check one snapshot plus its matching diagnostics artifact.

Reads only. Never writes under the input directories or calls the Query API.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from health_ml.datasets.manifest import sha256_file
from health_ml.diagnostics.config import DIAGNOSTICS_SCHEMA_VERSION
from health_ml.diagnostics.loader import (
    SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS,
    LoadedSnapshot,
    load_snapshot,
)
from health_ml.diagnostics.models import (
    FORECAST_READINESS_ARROW_SCHEMA,
    DiagnosticsManifest,
    ForecastReadinessRow,
)
from health_ml.errors import DiagnosticsValidationError, EpisodeValidationError

REQUIRED_DIAGNOSTICS_FILES = (
    "forecast_readiness.parquet",
    "diagnostics.json",
    "manifest.json",
)
SUPPORTED_DIAGNOSTICS_SCHEMA_VERSIONS = frozenset({DIAGNOSTICS_SCHEMA_VERSION})
SUPPORTED_FORECAST_READINESS_STATUSES = frozenset(
    {
        "eligible_unique",
        "eligible_ambiguous",
        "missing_target",
        "insufficient_history",
    }
)


@dataclass(frozen=True)
class LoadedDiagnostics:
    diagnostics_dir: Path
    diagnostics_id: str
    manifest: DiagnosticsManifest
    manifest_sha256: str
    readiness_rows: tuple[ForecastReadinessRow, ...]


@dataclass(frozen=True)
class EpisodeInputs:
    snapshot: LoadedSnapshot
    diagnostics: LoadedDiagnostics


def load_episode_inputs(
    snapshot_dir: Path,
    diagnostics_dir: Path,
    progress: Callable[[str, str], None] | None = None,
) -> EpisodeInputs:
    log = progress or (lambda _phase, _name: None)
    snapshot_path = Path(snapshot_dir)
    diagnostics_path = Path(diagnostics_dir)

    try:
        snapshot = load_snapshot(snapshot_path, progress=log)
    except DiagnosticsValidationError as exc:
        raise EpisodeValidationError(str(exc), exc.problems) from exc

    diagnostics = load_diagnostics_artifact(diagnostics_path, progress=log)
    _validate_pairing(snapshot, diagnostics)
    return EpisodeInputs(snapshot=snapshot, diagnostics=diagnostics)


def load_diagnostics_artifact(
    diagnostics_dir: Path,
    progress: Callable[[str, str], None] | None = None,
) -> LoadedDiagnostics:
    log = progress or (lambda _phase, _name: None)
    path = Path(diagnostics_dir)
    if not path.exists() or not path.is_dir():
        raise EpisodeValidationError(f"Diagnostics directory does not exist: {path}")

    missing = [name for name in REQUIRED_DIAGNOSTICS_FILES if not (path / name).is_file()]
    if missing:
        raise EpisodeValidationError(
            "Diagnostics artifact is missing required files:\n"
            + "\n".join(f"  - {name}" for name in missing),
            missing,
        )

    log("load", "diagnostics/manifest.json")
    manifest_path = path / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = DiagnosticsManifest.model_validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        raise EpisodeValidationError(f"Diagnostics manifest is invalid: {manifest_path}") from exc
    log("ok", "diagnostics/manifest.json")

    if manifest.diagnostics_schema_version not in SUPPORTED_DIAGNOSTICS_SCHEMA_VERSIONS:
        raise EpisodeValidationError(
            "Unsupported diagnostics_schema_version "
            f"{manifest.diagnostics_schema_version!r}; expected one of "
            f"{sorted(SUPPORTED_DIAGNOSTICS_SCHEMA_VERSIONS)}"
        )
    if path.name != manifest.diagnostics_id:
        raise EpisodeValidationError(
            "Diagnostics directory name does not match manifest diagnostics_id"
        )

    _verify_diagnostics_checksums(path, manifest)
    _validate_diagnostics_json(path / "diagnostics.json", manifest.diagnostics_id, progress=log)
    readiness = _read_forecast_readiness(path / "forecast_readiness.parquet", progress=log)

    return LoadedDiagnostics(
        diagnostics_dir=path,
        diagnostics_id=manifest.diagnostics_id,
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
        readiness_rows=readiness,
    )


def _validate_pairing(snapshot: LoadedSnapshot, diagnostics: LoadedDiagnostics) -> None:
    problems: list[str] = []
    if snapshot.manifest.schema_version not in SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS:
        problems.append(
            "Unsupported snapshot schema_version "
            f"{snapshot.manifest.schema_version!r}; expected one of "
            f"{sorted(SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS)}"
        )
    if diagnostics.manifest.input_snapshot_id != snapshot.snapshot_id:
        problems.append(
            "Diagnostics input_snapshot_id does not match the supplied snapshot "
            f"({diagnostics.manifest.input_snapshot_id!r} vs {snapshot.snapshot_id!r})"
        )
    if diagnostics.manifest.input_snapshot_manifest_sha256 != snapshot.manifest_sha256:
        problems.append(
            "Diagnostics input_snapshot_manifest_sha256 does not match the supplied "
            "snapshot manifest SHA-256"
        )
    if problems:
        raise EpisodeValidationError(
            "Snapshot and diagnostics are not a valid matched pair:\n"
            + "\n".join(f"  - {item}" for item in problems),
            problems,
        )


def _verify_diagnostics_checksums(diagnostics_dir: Path, manifest: DiagnosticsManifest) -> None:
    problems: list[str] = []
    for spec in manifest.files.values():
        filename = spec.path
        file_path = diagnostics_dir / filename
        if not file_path.is_file():
            problems.append(f"{filename} listed in the diagnostics manifest is missing")
            continue
        actual = sha256_file(file_path)
        if actual != spec.sha256:
            problems.append(f"{filename} SHA-256 does not match the diagnostics manifest")
    if problems:
        raise EpisodeValidationError(
            "Diagnostics artifact checksum verification failed:\n"
            + "\n".join(f"  - {item}" for item in problems),
            problems,
        )


def _validate_diagnostics_json(
    path: Path,
    diagnostics_id: str,
    progress: Callable[[str, str], None] | None = None,
) -> None:
    log = progress or (lambda _phase, _name: None)
    log("load", path.name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EpisodeValidationError(f"diagnostics.json is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise EpisodeValidationError("diagnostics.json must be a JSON object")
    reported_id = payload.get("diagnostics_id")
    if reported_id != diagnostics_id:
        raise EpisodeValidationError(
            "diagnostics.json diagnostics_id does not match the diagnostics manifest"
        )
    log("ok", path.name)


def _read_forecast_readiness(
    path: Path,
    progress: Callable[[str, str], None] | None = None,
) -> tuple[ForecastReadinessRow, ...]:
    log = progress or (lambda _phase, _name: None)
    log("load", path.name)
    try:
        table = pq.read_table(path)
    except (OSError, pa.ArrowInvalid) as exc:
        raise EpisodeValidationError(f"Failed to read {path.name}") from exc
    if not _schema_compatible(table.schema, FORECAST_READINESS_ARROW_SCHEMA):
        raise EpisodeValidationError(
            f"{path.name} schema is not a supported forecast_readiness schema "
            f"(expected columns {list(FORECAST_READINESS_ARROW_SCHEMA.names)}, "
            f"found {list(table.schema.names)})"
        )
    rows: list[ForecastReadinessRow] = []
    for item in table.to_pylist():
        status = item["status"]
        if status not in SUPPORTED_FORECAST_READINESS_STATUSES:
            raise EpisodeValidationError(
                f"{path.name} has unsupported readiness status {status!r}"
            )
        rows.append(
            ForecastReadinessRow(
                anchor_timestamp_utc=_arrow_datetime(item["anchor_timestamp_utc"]),
                horizon_minutes=int(item["horizon_minutes"]),
                ideal_target_timestamp_utc=_arrow_datetime(item["ideal_target_timestamp_utc"]),
                candidate_observation_count=int(item["candidate_observation_count"]),
                nearest_observation_timestamp_utc=_optional_datetime(
                    item.get("nearest_observation_timestamp_utc")
                ),
                nearest_offset_seconds=_optional_float(item.get("nearest_offset_seconds")),
                status=status,
                reason=item.get("reason"),
            )
        )
    log("ok", path.name)
    return tuple(rows)


def _schema_compatible(actual: pa.Schema, expected: pa.Schema) -> bool:
    if actual.names != expected.names:
        return False
    for name in expected.names:
        if actual.field(name).type != expected.field(name).type:
            return False
    return True


def _arrow_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _arrow_datetime(value)


def _optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value)
