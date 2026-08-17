"""Episode-dataset identity and manifest helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from health_ml import __version__
from health_ml.datasets.manifest import sha256_file
from health_ml.episodes.config import EPISODE_DATASET_SCHEMA_VERSION, EpisodeConfig
from health_ml.episodes.contracts import EpisodeArtifactFileRef
from health_ml.times import to_iso8601


def episode_dataset_id_for(
    *,
    snapshot_id: str,
    snapshot_manifest_sha256: str,
    diagnostics_id: str,
    diagnostics_manifest_sha256: str,
    config: EpisodeConfig,
    code_version: str = __version__,
    git_sha: str | None = None,
    schema_version: str = EPISODE_DATASET_SCHEMA_VERSION,
) -> str:
    payload = {
        "episode_schema_version": schema_version,
        "input_snapshot_id": snapshot_id,
        "input_snapshot_manifest_sha256": snapshot_manifest_sha256,
        "input_diagnostics_id": diagnostics_id,
        "input_diagnostics_manifest_sha256": diagnostics_manifest_sha256,
        "episode_config": config.identity_payload(),
        "episode_generator_code_version": code_version,
        "git_sha": git_sha,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"e{schema_version}_{digest}"


def episode_id_for(dataset_id: str, anchor: datetime) -> str:
    compact = anchor.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(f"{dataset_id}|{to_iso8601(anchor)}".encode()).hexdigest()[:12]
    return f"ep_{compact}_{digest}"


def file_ref(path: Path, *, row_count: int) -> EpisodeArtifactFileRef:
    return EpisodeArtifactFileRef(path=path.name, row_count=row_count, sha256=sha256_file(path))
