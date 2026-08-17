"""Pydantic contracts for Phase 1.5 diagnostics artifacts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from health_ml.datasets.snapshot import UTC_TIMESTAMP
from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.times import to_iso8601

GapClassification = Literal["normal", "warning", "major"]
ReadinessStatus = Literal[
    "eligible_unique",
    "eligible_ambiguous",
    "missing_target",
    "insufficient_history",
]
SchemaStatus = Literal["valid", "invalid_intervals"]
SortStatus = Literal["already_sorted", "sorted_for_analysis"]

ELIGIBILITY_DISCLOSURE = (
    "Eligibility in this phase means only that observed timestamps satisfy the "
    "configured history-span and target-tolerance rules. It does not indicate "
    "continuous context coverage, final episode eligibility, target selection policy, "
    "clinical validity, or forecasting performance."
)
SPLIT_DISCLOSURE = (
    "No model split is created by this diagnostics phase. Any future forecast "
    "evaluation must be chronological and must prevent information from later times "
    "from entering feature fitting, normalization, target construction, or model "
    "selection."
)

GLUCOSE_GAPS_ARROW_SCHEMA = pa.schema(
    [
        ("previous_timestamp_utc", UTC_TIMESTAMP),
        ("next_timestamp_utc", UTC_TIMESTAMP),
        ("elapsed_minutes", pa.float64()),
        ("classification", pa.string()),
    ]
)
DAILY_COVERAGE_ARROW_SCHEMA = pa.schema(
    [
        ("local_date", pa.date32()),
        ("glucose_observed_count", pa.int64()),
        ("glucose_expected_count_estimate", pa.int64()),
        ("glucose_coverage_ratio_estimate", pa.float64()),
        ("meal_count", pa.int64()),
        ("workout_overlap_count", pa.int64()),
        ("raw_sleep_interval_overlap_count", pa.int64()),
        ("weight_count", pa.int64()),
    ]
)
FORECAST_READINESS_ARROW_SCHEMA = pa.schema(
    [
        ("anchor_timestamp_utc", UTC_TIMESTAMP),
        ("horizon_minutes", pa.int64()),
        ("ideal_target_timestamp_utc", UTC_TIMESTAMP),
        ("candidate_observation_count", pa.int64()),
        ("nearest_observation_timestamp_utc", UTC_TIMESTAMP),
        ("nearest_offset_seconds", pa.float64()),
        ("status", pa.string()),
        ("reason", pa.string()),
    ]
)


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile. Empty input is None, never fabricated."""
    if not values:
        return None
    return percentile_sorted(sorted(float(item) for item in values), p)


def percentile_sorted(ordered: Sequence[float], p: float) -> float | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low]) * (1.0 - weight) + float(ordered[high]) * weight


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NumericDistribution(ContractModel):
    count: int
    min: float | None = None
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    max: float | None = None
    mean: float | None = None

    @classmethod
    def from_values(cls, values: Sequence[float], *, include_mean: bool = False) -> NumericDistribution:
        if not values:
            return cls(count=0)
        ordered = sorted(float(item) for item in values)
        return cls(
            count=len(ordered),
            min=ordered[0],
            p50=percentile_sorted(ordered, 50),
            p90=percentile_sorted(ordered, 90),
            p95=percentile_sorted(ordered, 95),
            max=ordered[-1],
            mean=mean(ordered) if include_mean else None,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "min": self.min,
            "p50": self.p50,
            "p90": self.p90,
            "p95": self.p95,
            "max": self.max,
            "mean": self.mean,
        }


class GlucoseGap(ContractModel):
    previous_timestamp_utc: datetime
    next_timestamp_utc: datetime
    elapsed_minutes: float
    classification: GapClassification

    def to_row(self) -> dict[str, Any]:
        return {
            "previous_timestamp_utc": self.previous_timestamp_utc,
            "next_timestamp_utc": self.next_timestamp_utc,
            "elapsed_minutes": self.elapsed_minutes,
            "classification": self.classification,
        }


class ForecastReadinessRow(ContractModel):
    anchor_timestamp_utc: datetime
    horizon_minutes: int
    ideal_target_timestamp_utc: datetime
    candidate_observation_count: int
    nearest_observation_timestamp_utc: datetime | None = None
    nearest_offset_seconds: float | None = None
    status: ReadinessStatus
    reason: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "anchor_timestamp_utc": self.anchor_timestamp_utc,
            "horizon_minutes": self.horizon_minutes,
            "ideal_target_timestamp_utc": self.ideal_target_timestamp_utc,
            "candidate_observation_count": self.candidate_observation_count,
            "nearest_observation_timestamp_utc": self.nearest_observation_timestamp_utc,
            "nearest_offset_seconds": self.nearest_offset_seconds,
            "status": self.status,
            "reason": self.reason,
        }


class DailyCoverageRow(ContractModel):
    local_date: date
    glucose_observed_count: int
    glucose_expected_count_estimate: int
    glucose_coverage_ratio_estimate: float | None
    meal_count: int
    workout_overlap_count: int
    raw_sleep_interval_overlap_count: int
    weight_count: int

    def to_row(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date,
            "glucose_observed_count": self.glucose_observed_count,
            "glucose_expected_count_estimate": self.glucose_expected_count_estimate,
            "glucose_coverage_ratio_estimate": self.glucose_coverage_ratio_estimate,
            "meal_count": self.meal_count,
            "workout_overlap_count": self.workout_overlap_count,
            "raw_sleep_interval_overlap_count": self.raw_sleep_interval_overlap_count,
            "weight_count": self.weight_count,
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date.isoformat(),
            "glucose_observed_count": self.glucose_observed_count,
            "glucose_expected_count_estimate": self.glucose_expected_count_estimate,
            "glucose_coverage_ratio_estimate": self.glucose_coverage_ratio_estimate,
            "meal_count": self.meal_count,
            "workout_overlap_count": self.workout_overlap_count,
            "raw_sleep_interval_overlap_count": self.raw_sleep_interval_overlap_count,
            "weight_count": self.weight_count,
        }


class CategoryStructuralSummary(ContractModel):
    category: str
    row_count: int
    earliest_timestamp_utc: datetime | None = None
    latest_timestamp_utc: datetime | None = None
    in_declared_interval_count: int
    outside_declared_interval_count: int
    duplicate_identifier_count: int | None = None
    source_sort_status: SortStatus
    schema_validation_status: SchemaStatus = "valid"
    invalid_interval_count: int | None = None
    overlapping_snapshot_count: int | None = None
    non_overlapping_count: int | None = None
    extends_beyond_bounds_count: int | None = None
    duration_minutes: NumericDistribution | None = None
    duplicate_timestamp_count: int | None = None

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": self.category,
            "row_count": self.row_count,
            "earliest_timestamp_utc": _iso(self.earliest_timestamp_utc),
            "latest_timestamp_utc": _iso(self.latest_timestamp_utc),
            "in_declared_interval_count": self.in_declared_interval_count,
            "outside_declared_interval_count": self.outside_declared_interval_count,
            "duplicate_identifier_count": self.duplicate_identifier_count,
            "source_sort_status": self.source_sort_status,
            "schema_validation_status": self.schema_validation_status,
        }
        if self.invalid_interval_count is not None:
            payload["invalid_interval_count"] = self.invalid_interval_count
        if self.overlapping_snapshot_count is not None:
            payload["overlapping_snapshot_count"] = self.overlapping_snapshot_count
        if self.non_overlapping_count is not None:
            payload["non_overlapping_count"] = self.non_overlapping_count
        if self.extends_beyond_bounds_count is not None:
            payload["extends_beyond_bounds_count"] = self.extends_beyond_bounds_count
        if self.duration_minutes is not None:
            payload["duration_minutes"] = self.duration_minutes.to_json_dict()
        if self.duplicate_timestamp_count is not None:
            payload["duplicate_timestamp_count"] = self.duplicate_timestamp_count
        return payload


class GlucoseValueSummary(ContractModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "median": self.median,
        }


class GlucoseSamplingSummary(ContractModel):
    observed_record_count: int
    duplicate_timestamp_count: int
    non_increasing_timestamp_pair_count: int
    value_summary: GlucoseValueSummary
    sampling_interval_minutes: NumericDistribution
    warning_gap_count: int
    major_gap_count: int
    warning_gap_duration_minutes_total: float
    major_gap_duration_minutes_total: float
    interval_distribution_available: bool

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "observed_record_count": self.observed_record_count,
            "duplicate_timestamp_count": self.duplicate_timestamp_count,
            "non_increasing_timestamp_pair_count": self.non_increasing_timestamp_pair_count,
            "value_summary": self.value_summary.to_json_dict(),
            "sampling_interval_minutes": self.sampling_interval_minutes.to_json_dict(),
            "warning_gap_count": self.warning_gap_count,
            "major_gap_count": self.major_gap_count,
            "warning_gap_duration_minutes_total": self.warning_gap_duration_minutes_total,
            "major_gap_duration_minutes_total": self.major_gap_duration_minutes_total,
            "interval_distribution_available": self.interval_distribution_available,
        }


class GlucoseGapSummary(ContractModel):
    warning_count: int
    major_count: int
    warning_duration_minutes_total: float
    major_duration_minutes_total: float
    written_row_count: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "warning_count": self.warning_count,
            "major_count": self.major_count,
            "warning_duration_minutes_total": self.warning_duration_minutes_total,
            "major_duration_minutes_total": self.major_duration_minutes_total,
            "written_row_count": self.written_row_count,
        }


class DailyCoverageSummary(ContractModel):
    local_day_count: int
    local_days_with_glucose: int
    rows: tuple[DailyCoverageRow, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "local_day_count": self.local_day_count,
            "local_days_with_glucose": self.local_days_with_glucose,
            "rows": [row.to_json_dict() for row in self.rows],
        }


class HorizonReadinessSummary(ContractModel):
    horizon_minutes: int
    anchor_count: int
    insufficient_history_count: int
    missing_target_count: int
    eligible_unique_count: int
    eligible_ambiguous_count: int
    eligible_any_count: int
    eligible_unique_rate: float | None
    eligible_any_rate: float | None
    nearest_target_offset_seconds: NumericDistribution

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "anchor_count": self.anchor_count,
            "insufficient_history_count": self.insufficient_history_count,
            "missing_target_count": self.missing_target_count,
            "eligible_unique_count": self.eligible_unique_count,
            "eligible_ambiguous_count": self.eligible_ambiguous_count,
            "eligible_any_count": self.eligible_any_count,
            "eligible_unique_rate": self.eligible_unique_rate,
            "eligible_any_rate": self.eligible_any_rate,
            "nearest_target_offset_seconds": self.nearest_target_offset_seconds.to_json_dict(),
        }


class ForecastReadinessSummary(ContractModel):
    row_count: int
    horizons: tuple[HorizonReadinessSummary, ...]
    eligibility_disclosure: str = ELIGIBILITY_DISCLOSURE

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "horizons": [row.to_json_dict() for row in self.horizons],
            "eligibility_disclosure": self.eligibility_disclosure,
        }


class GlucoseSegment(ContractModel):
    start_timestamp_utc: datetime
    end_timestamp_utc: datetime
    observation_count: int
    elapsed_minutes: float

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "start_timestamp_utc": to_iso8601(self.start_timestamp_utc),
            "end_timestamp_utc": to_iso8601(self.end_timestamp_utc),
            "observation_count": self.observation_count,
            "elapsed_minutes": self.elapsed_minutes,
        }


class ChronologicalCoverageSummary(ContractModel):
    snapshot_start_utc: datetime
    snapshot_end_utc: datetime
    earliest_observed_by_category: dict[str, datetime | None]
    latest_observed_by_category: dict[str, datetime | None]
    local_days_with_glucose: int
    local_days_with_major_glucose_gaps: int
    contiguous_glucose_segments: tuple[GlucoseSegment, ...]
    suggested_split_boundaries_utc: tuple[datetime, ...]
    suggested_split_note: str
    split_disclosure: str = SPLIT_DISCLOSURE

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "snapshot_start_utc": to_iso8601(self.snapshot_start_utc),
            "snapshot_end_utc": to_iso8601(self.snapshot_end_utc),
            "earliest_observed_by_category": {
                key: _iso(value) for key, value in self.earliest_observed_by_category.items()
            },
            "latest_observed_by_category": {
                key: _iso(value) for key, value in self.latest_observed_by_category.items()
            },
            "local_days_with_glucose": self.local_days_with_glucose,
            "local_days_with_major_glucose_gaps": self.local_days_with_major_glucose_gaps,
            "contiguous_glucose_segments": [row.to_json_dict() for row in self.contiguous_glucose_segments],
            "suggested_split_boundaries_utc": [to_iso8601(value) for value in self.suggested_split_boundaries_utc],
            "suggested_split_note": self.suggested_split_note,
            "split_disclosure": self.split_disclosure,
        }


class InputSnapshotRef(ContractModel):
    snapshot_id: str
    manifest_checksum: str
    source_start_utc: datetime
    source_end_utc: datetime
    source_timezone: str
    source_file_checksums: dict[str, str] = Field(default_factory=dict)
    range_semantics: str = "[start, end)"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "manifest_checksum": self.manifest_checksum,
            "source_interval": {
                "start": to_iso8601(self.source_start_utc),
                "end": to_iso8601(self.source_end_utc),
                "range_semantics": self.range_semantics,
            },
            "source_timezone": self.source_timezone,
            "source_file_checksums": dict(self.source_file_checksums),
        }


class ArtifactFileRef(ContractModel):
    path: str
    rows: int
    sha256: str

    def to_json_dict(self) -> dict[str, Any]:
        return {"path": self.path, "rows": self.rows, "sha256": self.sha256}


class DiagnosticsManifest(ContractModel):
    diagnostics_schema_version: str
    diagnostics_id: str
    created_at: datetime
    input_snapshot_id: str
    input_snapshot_manifest_sha256: str
    diagnostics_code_version: str
    git_sha: str | None = None
    configuration: DiagnosticsConfig
    files: dict[str, ArtifactFileRef]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "diagnostics_schema_version": self.diagnostics_schema_version,
            "diagnostics_id": self.diagnostics_id,
            "created_at": to_iso8601(self.created_at),
            "input_snapshot_id": self.input_snapshot_id,
            "input_snapshot_manifest_sha256": self.input_snapshot_manifest_sha256,
            "diagnostics_code_version": self.diagnostics_code_version,
            "git_sha": self.git_sha,
            "configuration": self.configuration.to_json_dict(),
            "files": {name: ref.to_json_dict() for name, ref in self.files.items()},
        }


class DiagnosticsReport(ContractModel):
    diagnostics_schema_version: str
    diagnostics_id: str
    created_at: datetime
    input_snapshot: InputSnapshotRef
    configuration: DiagnosticsConfig
    structural_summary: dict[str, CategoryStructuralSummary]
    glucose_sampling_summary: GlucoseSamplingSummary
    glucose_gap_summary: GlucoseGapSummary
    daily_coverage_summary: DailyCoverageSummary
    forecast_readiness_summary: ForecastReadinessSummary
    chronological_coverage_summary: ChronologicalCoverageSummary
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "diagnostics_schema_version": self.diagnostics_schema_version,
            "diagnostics_id": self.diagnostics_id,
            "created_at": to_iso8601(self.created_at),
            "input_snapshot": self.input_snapshot.to_json_dict(),
            "configuration": self.configuration.to_json_dict(),
            "structural_summary": {
                name: summary.to_json_dict() for name, summary in self.structural_summary.items()
            },
            "glucose_sampling_summary": self.glucose_sampling_summary.to_json_dict(),
            "glucose_gap_summary": self.glucose_gap_summary.to_json_dict(),
            "daily_coverage_summary": self.daily_coverage_summary.to_json_dict(),
            "forecast_readiness_summary": self.forecast_readiness_summary.to_json_dict(),
            "chronological_coverage_summary": self.chronological_coverage_summary.to_json_dict(),
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return to_iso8601(value)
