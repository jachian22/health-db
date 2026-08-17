"""Snapshot manifest — request identity vs artifact identity (checksums)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from health_ml import SCHEMA_VERSION, __version__
from health_ml.config import QUERY_API_CONTRACT, RANGE_SEMANTICS
from health_ml.times import to_iso8601

SNAPSHOT_FILES = ("glucose", "meals", "workouts", "sleep", "weight")


class ArtifactRef(BaseModel):
    """Identity of a produced Parquet file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    sha256: str
    rows: int


class SnapshotRequest(BaseModel):
    """Semantic request identity. `created_at` is not part of this."""

    model_config = ConfigDict(extra="forbid")

    source_start: datetime
    source_end: datetime
    timezone: str
    range_semantics: str = RANGE_SEMANTICS
    schema_version: str = SCHEMA_VERSION
    extractor_package: str = "health-ml"
    extractor_version: str = __version__
    api_contract: str = QUERY_API_CONTRACT
    api_url: str | None = None
    glucose_path: str = "/v1/query/glucose/series"
    glucose_resolution: str = "raw"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source_start": to_iso8601(self.source_start),
            "source_end": to_iso8601(self.source_end),
            "timezone": self.timezone,
            "range_semantics": self.range_semantics,
            "schema_version": self.schema_version,
            "extractor_package": self.extractor_package,
            "extractor_version": self.extractor_version,
            "api_contract": self.api_contract,
            "api_url": self.api_url,
            "glucose_path": self.glucose_path,
            "glucose_resolution": self.glucose_resolution,
        }


class CategoryDiagnosticCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: int
    duplicate_timestamps: int = 0
    duplicate_ids: int = 0
    out_of_window: int = 0
    gaps_over_15m: int = 0
    gaps_over_60m: int = 0
    out_of_range: int = 0

    def to_json_dict(self) -> dict[str, int]:
        return self.model_dump()


class SnapshotDiagnosticsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    glucose: CategoryDiagnosticCounts
    meals: CategoryDiagnosticCounts
    workouts: CategoryDiagnosticCounts
    sleep: CategoryDiagnosticCounts
    weight: CategoryDiagnosticCounts

    def to_json_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name).to_json_dict()
            for name in SNAPSHOT_FILES
        }


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    request: SnapshotRequest
    artifacts: dict[str, ArtifactRef]
    diagnostics: SnapshotDiagnosticsManifest
    files: dict[str, str] = Field(default_factory=dict)
    row_counts: dict[str, int] = Field(default_factory=dict)
    checksums: dict[str, str] = Field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": to_iso8601(self.created_at),
            "request": self.request.to_json_dict(),
            "artifacts": {
                name: ref.model_dump() for name, ref in self.artifacts.items()
            },
            "diagnostics": self.diagnostics.to_json_dict(),
            "files": self.files,
            "row_counts": self.row_counts,
            "checksums": self.checksums,
        }

    @property
    def source_start(self) -> datetime:
        return self.request.source_start

    @property
    def source_end(self) -> datetime:
        return self.request.source_end

    @property
    def timezone(self) -> str:
        return self.request.timezone


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: Path, manifest: SnapshotManifest) -> None:
    path.write_text(json.dumps(manifest.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> SnapshotManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SnapshotManifest.model_validate(payload)
