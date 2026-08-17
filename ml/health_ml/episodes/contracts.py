"""Typed contracts and Arrow schemas for Phase 2 episode artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from health_ml.datasets.snapshot import UTC_TIMESTAMP
from health_ml.diagnostics.models import NumericDistribution
from health_ml.episodes.config import EpisodeConfig
from health_ml.times import to_iso8601

EventType = Literal["meal", "workout", "sleep_interval", "weight"]

NO_INTERPOLATION_DISCLOSURE = (
    "Episodes contain only recorded historical context at or before each anchor and "
    "observed future glucose values within configured target-tolerance windows. No "
    "glucose values were interpolated, imputed, resampled, or otherwise manufactured."
)
NO_CLINICAL_DISCLOSURE = (
    "Episode eligibility reflects this dataset configuration only. It does not "
    "establish clinical validity, explain glucose changes, provide medical advice, "
    "or measure forecasting performance."
)

HORIZON_DIAGNOSTIC_STRUCT = pa.struct(
    [
        ("horizon_minutes", pa.int64()),
        ("target_candidate_count", pa.int64()),
        ("rejection_code", pa.string()),
    ]
)

EPISODES_ARROW_SCHEMA = pa.schema(
    [
        ("episode_id", pa.string()),
        ("anchor_timestamp_utc", UTC_TIMESTAMP),
        ("history_start_timestamp_utc", UTC_TIMESTAMP),
        ("history_end_timestamp_utc", UTC_TIMESTAMP),
        ("maximum_target_timestamp_utc", UTC_TIMESTAMP),
        ("snapshot_id", pa.string()),
        ("snapshot_manifest_sha256", pa.string()),
        ("diagnostics_id", pa.string()),
        ("diagnostics_manifest_sha256", pa.string()),
        ("history_minutes", pa.int64()),
        ("grid_cadence_minutes", pa.int64()),
        ("grid_position_count", pa.int64()),
        ("max_history_gap_minutes", pa.float64()),
        ("history_start_tolerance_minutes", pa.float64()),
        ("target_tolerance_minutes", pa.float64()),
        ("horizons_minutes", pa.list_(pa.int64())),
        ("history_observed_count", pa.int64()),
        ("history_missing_grid_count", pa.int64()),
        ("history_max_observed_gap_minutes", pa.float64()),
        ("history_start_source_timestamp_utc", UTC_TIMESTAMP),
        ("anchor_source_timestamp_utc", UTC_TIMESTAMP),
        ("meal_context_count", pa.int64()),
        ("workout_context_count", pa.int64()),
        ("sleep_context_count", pa.int64()),
        ("weight_context_count", pa.int64()),
    ]
)
EPISODE_GLUCOSE_HISTORY_ARROW_SCHEMA = pa.schema(
    [
        ("episode_id", pa.string()),
        ("grid_index", pa.int64()),
        ("grid_timestamp_utc", UTC_TIMESTAMP),
        ("glucose_mg_dl", pa.float64()),
        ("observed_mask", pa.bool_()),
        ("source_timestamp_utc", UTC_TIMESTAMP),
        ("source", pa.string()),
    ]
)
EPISODE_TARGETS_ARROW_SCHEMA = pa.schema(
    [
        ("episode_id", pa.string()),
        ("horizon_minutes", pa.int64()),
        ("ideal_target_timestamp_utc", UTC_TIMESTAMP),
        ("target_source_timestamp_utc", UTC_TIMESTAMP),
        ("target_glucose_mg_dl", pa.float64()),
        ("target_offset_seconds", pa.float64()),
        ("source", pa.string()),
    ]
)
EPISODE_EVENTS_ARROW_SCHEMA = pa.schema(
    [
        ("episode_id", pa.string()),
        ("event_type", pa.string()),
        ("event_id", pa.string()),
        ("event_timestamp_utc", UTC_TIMESTAMP),
        ("event_start_timestamp_utc", UTC_TIMESTAMP),
        ("event_end_timestamp_utc", UTC_TIMESTAMP),
        ("source", pa.string()),
        ("overlap_duration_seconds", pa.float64()),
    ]
)
REJECTED_ANCHORS_ARROW_SCHEMA = pa.schema(
    [
        ("anchor_timestamp_utc", UTC_TIMESTAMP),
        ("snapshot_id", pa.string()),
        ("history_start_timestamp_utc", UTC_TIMESTAMP),
        ("status", pa.string()),
        ("rejection_codes", pa.list_(pa.string())),
        ("history_observed_count", pa.int64()),
        ("history_max_observed_gap_minutes", pa.float64()),
        ("horizon_diagnostics", pa.list_(HORIZON_DIAGNOSTIC_STRUCT)),
    ]
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EpisodeRow(ContractModel):
    episode_id: str
    anchor_timestamp_utc: datetime
    history_start_timestamp_utc: datetime
    history_end_timestamp_utc: datetime
    maximum_target_timestamp_utc: datetime
    snapshot_id: str
    snapshot_manifest_sha256: str
    diagnostics_id: str
    diagnostics_manifest_sha256: str
    history_minutes: int
    grid_cadence_minutes: int
    grid_position_count: int
    max_history_gap_minutes: float
    history_start_tolerance_minutes: float
    target_tolerance_minutes: float
    horizons_minutes: tuple[int, ...]
    history_observed_count: int
    history_missing_grid_count: int
    history_max_observed_gap_minutes: float
    history_start_source_timestamp_utc: datetime
    anchor_source_timestamp_utc: datetime
    meal_context_count: int
    workout_context_count: int
    sleep_context_count: int
    weight_context_count: int

    def to_row(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "anchor_timestamp_utc": self.anchor_timestamp_utc,
            "history_start_timestamp_utc": self.history_start_timestamp_utc,
            "history_end_timestamp_utc": self.history_end_timestamp_utc,
            "maximum_target_timestamp_utc": self.maximum_target_timestamp_utc,
            "snapshot_id": self.snapshot_id,
            "snapshot_manifest_sha256": self.snapshot_manifest_sha256,
            "diagnostics_id": self.diagnostics_id,
            "diagnostics_manifest_sha256": self.diagnostics_manifest_sha256,
            "history_minutes": self.history_minutes,
            "grid_cadence_minutes": self.grid_cadence_minutes,
            "grid_position_count": self.grid_position_count,
            "max_history_gap_minutes": self.max_history_gap_minutes,
            "history_start_tolerance_minutes": self.history_start_tolerance_minutes,
            "target_tolerance_minutes": self.target_tolerance_minutes,
            "horizons_minutes": list(self.horizons_minutes),
            "history_observed_count": self.history_observed_count,
            "history_missing_grid_count": self.history_missing_grid_count,
            "history_max_observed_gap_minutes": self.history_max_observed_gap_minutes,
            "history_start_source_timestamp_utc": self.history_start_source_timestamp_utc,
            "anchor_source_timestamp_utc": self.anchor_source_timestamp_utc,
            "meal_context_count": self.meal_context_count,
            "workout_context_count": self.workout_context_count,
            "sleep_context_count": self.sleep_context_count,
            "weight_context_count": self.weight_context_count,
        }


class GlucoseHistoryRow(ContractModel):
    episode_id: str
    grid_index: int
    grid_timestamp_utc: datetime
    glucose_mg_dl: float | None
    observed_mask: bool
    source_timestamp_utc: datetime | None
    source: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "grid_index": self.grid_index,
            "grid_timestamp_utc": self.grid_timestamp_utc,
            "glucose_mg_dl": self.glucose_mg_dl,
            "observed_mask": self.observed_mask,
            "source_timestamp_utc": self.source_timestamp_utc,
            "source": self.source,
        }


class TargetRow(ContractModel):
    episode_id: str
    horizon_minutes: int
    ideal_target_timestamp_utc: datetime
    target_source_timestamp_utc: datetime
    target_glucose_mg_dl: float
    target_offset_seconds: float
    source: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "horizon_minutes": self.horizon_minutes,
            "ideal_target_timestamp_utc": self.ideal_target_timestamp_utc,
            "target_source_timestamp_utc": self.target_source_timestamp_utc,
            "target_glucose_mg_dl": self.target_glucose_mg_dl,
            "target_offset_seconds": self.target_offset_seconds,
            "source": self.source,
        }


class EventRow(ContractModel):
    episode_id: str
    event_type: EventType
    event_id: str | None
    event_timestamp_utc: datetime | None
    event_start_timestamp_utc: datetime | None
    event_end_timestamp_utc: datetime | None
    source: str | None
    overlap_duration_seconds: float | None

    def to_row(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "event_timestamp_utc": self.event_timestamp_utc,
            "event_start_timestamp_utc": self.event_start_timestamp_utc,
            "event_end_timestamp_utc": self.event_end_timestamp_utc,
            "source": self.source,
            "overlap_duration_seconds": self.overlap_duration_seconds,
        }


class HorizonDiagnostic(ContractModel):
    horizon_minutes: int
    target_candidate_count: int
    rejection_code: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "target_candidate_count": self.target_candidate_count,
            "rejection_code": self.rejection_code,
        }


class RejectedAnchorRow(ContractModel):
    anchor_timestamp_utc: datetime
    snapshot_id: str
    history_start_timestamp_utc: datetime
    status: Literal["rejected"] = "rejected"
    rejection_codes: tuple[str, ...]
    history_observed_count: int
    history_max_observed_gap_minutes: float | None
    horizon_diagnostics: tuple[HorizonDiagnostic, ...]

    def to_row(self) -> dict[str, Any]:
        return {
            "anchor_timestamp_utc": self.anchor_timestamp_utc,
            "snapshot_id": self.snapshot_id,
            "history_start_timestamp_utc": self.history_start_timestamp_utc,
            "status": self.status,
            "rejection_codes": list(self.rejection_codes),
            "history_observed_count": self.history_observed_count,
            "history_max_observed_gap_minutes": self.history_max_observed_gap_minutes,
            "horizon_diagnostics": [item.to_row() for item in self.horizon_diagnostics],
        }


class EpisodeArtifactFileRef(ContractModel):
    path: str
    row_count: int
    sha256: str

    def to_json_dict(self) -> dict[str, Any]:
        return {"path": self.path, "row_count": self.row_count, "sha256": self.sha256}


class InputSnapshotIdentity(ContractModel):
    snapshot_id: str
    manifest_sha256: str
    schema_version: str
    source_timezone: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "manifest_sha256": self.manifest_sha256,
            "schema_version": self.schema_version,
            "source_timezone": self.source_timezone,
        }


class InputDiagnosticsIdentity(ContractModel):
    diagnostics_id: str
    manifest_sha256: str
    diagnostics_schema_version: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "diagnostics_id": self.diagnostics_id,
            "manifest_sha256": self.manifest_sha256,
            "diagnostics_schema_version": self.diagnostics_schema_version,
        }


class EpisodeManifest(ContractModel):
    episode_dataset_schema_version: str
    episode_dataset_id: str
    created_at: datetime
    input_snapshot_id: str
    input_snapshot_manifest_sha256: str
    input_diagnostics_id: str
    input_diagnostics_manifest_sha256: str
    episode_generator_code_version: str
    git_sha: str | None = None
    configuration: EpisodeConfig
    files: dict[str, EpisodeArtifactFileRef]
    accepted_episode_count: int
    rejected_anchor_count: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "episode_dataset_schema_version": self.episode_dataset_schema_version,
            "episode_dataset_id": self.episode_dataset_id,
            "created_at": to_iso8601(self.created_at),
            "input_snapshot_id": self.input_snapshot_id,
            "input_snapshot_manifest_sha256": self.input_snapshot_manifest_sha256,
            "input_diagnostics_id": self.input_diagnostics_id,
            "input_diagnostics_manifest_sha256": self.input_diagnostics_manifest_sha256,
            "episode_generator_code_version": self.episode_generator_code_version,
            "git_sha": self.git_sha,
            "configuration": self.configuration.to_json_dict(),
            "files": {name: ref.to_json_dict() for name, ref in self.files.items()},
            "accepted_episode_count": self.accepted_episode_count,
            "rejected_anchor_count": self.rejected_anchor_count,
        }


class DiagnosticsComparison(ContractModel):
    comparison_status: Literal["compared", "skipped_cardinality_mismatch"]
    phase_1_5_readiness_counts: dict[str, int]
    phase_2_final_outcome_counts: dict[str, int]
    mismatch_summary: dict[str, int]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "comparison_status": self.comparison_status,
            "phase_1_5_readiness_counts": dict(self.phase_1_5_readiness_counts),
            "phase_2_final_outcome_counts": dict(self.phase_2_final_outcome_counts),
            "mismatch_summary": dict(self.mismatch_summary),
        }


class HistoryQuality(ContractModel):
    accepted_history_observed_count: NumericDistribution
    accepted_history_missing_grid_count: NumericDistribution
    accepted_history_max_gap_minutes: NumericDistribution

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "accepted_history_observed_count": self.accepted_history_observed_count.to_json_dict(),
            "accepted_history_missing_grid_count": self.accepted_history_missing_grid_count.to_json_dict(),
            "accepted_history_max_gap_minutes": self.accepted_history_max_gap_minutes.to_json_dict(),
        }


class HorizonTargetQuality(ContractModel):
    horizon_minutes: int
    accepted_count: int
    target_offset_seconds: NumericDistribution
    source_counts: dict[str, int] = Field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "accepted_count": self.accepted_count,
            "target_offset_seconds": self.target_offset_seconds.to_json_dict(),
            "source_counts": dict(self.source_counts),
        }


class EpisodeDiagnosticsReport(ContractModel):
    episode_diagnostics_schema_version: str
    episode_dataset_id: str
    input_snapshot_identity: InputSnapshotIdentity
    input_diagnostics_identity: InputDiagnosticsIdentity
    configuration: EpisodeConfig
    candidate_anchor_count: int
    accepted_episode_count: int
    rejected_anchor_count: int
    acceptance_rate: float | None
    rejections_by_code: dict[str, int]
    rejections_by_horizon: dict[str, dict[str, int]]
    history_quality: HistoryQuality
    target_quality_per_horizon: tuple[HorizonTargetQuality, ...]
    diagnostics_comparison: DiagnosticsComparison
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "episode_diagnostics_schema_version": self.episode_diagnostics_schema_version,
            "episode_dataset_id": self.episode_dataset_id,
            "input_snapshot_identity": self.input_snapshot_identity.to_json_dict(),
            "input_diagnostics_identity": self.input_diagnostics_identity.to_json_dict(),
            "configuration": self.configuration.to_json_dict(),
            "candidate_anchor_count": self.candidate_anchor_count,
            "accepted_episode_count": self.accepted_episode_count,
            "rejected_anchor_count": self.rejected_anchor_count,
            "acceptance_rate": self.acceptance_rate,
            "rejections_by_code": dict(self.rejections_by_code),
            "rejections_by_horizon": {
                key: dict(value) for key, value in self.rejections_by_horizon.items()
            },
            "history_quality": self.history_quality.to_json_dict(),
            "target_quality_per_horizon": [row.to_json_dict() for row in self.target_quality_per_horizon],
            "diagnostics_comparison": self.diagnostics_comparison.to_json_dict(),
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
        }
