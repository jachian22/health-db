"""Write immutable episode artifacts. Does not modify source snapshot or diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from health_ml.diagnostics.config import DiagnosticsConfig
from health_ml.diagnostics.models import ForecastReadinessRow, NumericDistribution
from health_ml.episodes.config import EPISODE_DIAGNOSTICS_SCHEMA_VERSION, EpisodeConfig
from health_ml.episodes.contracts import (
    NO_CLINICAL_DISCLOSURE,
    NO_INTERPOLATION_DISCLOSURE,
    DiagnosticsComparison,
    EpisodeDiagnosticsReport,
    EpisodeManifest,
    EpisodeRow,
    EventRow,
    GlucoseHistoryRow,
    HistoryQuality,
    HorizonTargetQuality,
    InputDiagnosticsIdentity,
    InputSnapshotIdentity,
    RejectedAnchorRow,
    TargetRow,
)
from health_ml.episodes.eligibility import AnchorEvaluation
from health_ml.errors import EpisodeError
from health_ml.schemas.canonical import GlucoseRecord
from health_ml.times import to_iso8601

OUTPUT_FILES = (
    "episodes.parquet",
    "episode_glucose_history.parquet",
    "episode_targets.parquet",
    "episode_events.parquet",
    "rejected_anchors.parquet",
    "diagnostics.json",
    "manifest.json",
    "README.md",
)


def write_parquet(path: Path, schema: pa.Schema, rows: list[dict[str, Any]]) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def config_compatibility_warnings(
    episode_config: EpisodeConfig,
    diagnostics_config: DiagnosticsConfig,
) -> list[str]:
    warnings: list[str] = []
    if diagnostics_config.minimum_history_minutes != float(episode_config.history_minutes):
        warnings.append(
            "Phase 1.5 minimum_history_minutes "
            f"({diagnostics_config.minimum_history_minutes:g}) differs from Phase 2 "
            f"history_minutes ({episode_config.history_minutes}); Phase 2 policy is used"
        )
    if diagnostics_config.target_tolerance_minutes != episode_config.target_tolerance_minutes:
        warnings.append(
            "Phase 1.5 target_tolerance_minutes "
            f"({diagnostics_config.target_tolerance_minutes:g}) differs from Phase 2 "
            f"({episode_config.target_tolerance_minutes:g}); Phase 2 policy is used"
        )
    if tuple(diagnostics_config.horizons_minutes) != tuple(episode_config.horizons_minutes):
        warnings.append(
            "Phase 1.5 horizons_minutes "
            f"{list(diagnostics_config.horizons_minutes)} differ from Phase 2 "
            f"{list(episode_config.horizons_minutes)}; Phase 2 policy is used"
        )
    return warnings


def compare_with_readiness(
    evaluations: Sequence[AnchorEvaluation],
    readiness_rows: Sequence[ForecastReadinessRow],
    *,
    episode_horizons: Sequence[int],
    diagnostics_horizons: Sequence[int],
) -> DiagnosticsComparison:
    phase15_status_counts: Counter[str] = Counter(row.status for row in readiness_rows)
    accepted = sum(1 for item in evaluations if item.accepted)
    rejected = len(evaluations) - accepted
    outcome_counts = {
        "candidate_anchor_count": len(evaluations),
        "accepted_episode_count": accepted,
        "rejected_anchor_count": rejected,
    }
    status_counts = {
        "readiness_row_count": len(readiness_rows),
        "eligible_unique": phase15_status_counts["eligible_unique"],
        "eligible_ambiguous": phase15_status_counts["eligible_ambiguous"],
        "missing_target": phase15_status_counts["missing_target"],
        "insufficient_history": phase15_status_counts["insufficient_history"],
    }
    expected = len(evaluations) * len(diagnostics_horizons)
    if not diagnostics_horizons or len(readiness_rows) != expected:
        return DiagnosticsComparison(
            comparison_status="skipped_cardinality_mismatch",
            phase_1_5_readiness_counts=status_counts,
            phase_2_final_outcome_counts=outcome_counts,
            mismatch_summary={},
        )

    unique_all = 0
    unique_all_but_rejected = 0
    accepted_but_not_unique_all = 0
    width = len(diagnostics_horizons)
    for index, evaluation in enumerate(evaluations):
        subset = readiness_rows[index * width : (index + 1) * width]
        by_horizon = {row.horizon_minutes: row.status for row in subset}
        is_unique_all = all(
            by_horizon.get(horizon) == "eligible_unique" for horizon in episode_horizons
        )
        if is_unique_all:
            unique_all += 1
            if not evaluation.accepted:
                unique_all_but_rejected += 1
        elif evaluation.accepted:
            accepted_but_not_unique_all += 1
    status_counts["unique_all_requested_horizons"] = unique_all
    return DiagnosticsComparison(
        comparison_status="compared",
        phase_1_5_readiness_counts=status_counts,
        phase_2_final_outcome_counts=outcome_counts,
        mismatch_summary={
            "phase_1_5_unique_all_horizons_but_phase_2_rejected": unique_all_but_rejected,
            "phase_2_accepted_but_phase_1_5_not_unique_all_horizons": accepted_but_not_unique_all,
        },
    )


def build_diagnostics_report(
    *,
    dataset_id: str,
    snapshot_id: str,
    snapshot_manifest_sha256: str,
    snapshot_schema_version: str,
    source_timezone: str,
    diagnostics_id: str,
    diagnostics_manifest_sha256: str,
    diagnostics_schema_version: str,
    config: EpisodeConfig,
    evaluations: Sequence[AnchorEvaluation],
    accepted_rows: Sequence[EpisodeRow],
    target_rows: Sequence[TargetRow],
    comparison: DiagnosticsComparison,
    extra_warnings: Sequence[str],
) -> EpisodeDiagnosticsReport:
    accepted = [item for item in evaluations if item.accepted]
    rejected = [item for item in evaluations if not item.accepted]
    code_counts: Counter[str] = Counter()
    for item in rejected:
        code_counts.update(item.rejection_codes)
    horizon_rejections: dict[str, dict[str, int]] = {
        str(horizon): {} for horizon in config.horizons_minutes
    }
    for item in rejected:
        for horizon in item.horizons:
            if horizon.rejection_code is None:
                continue
            bucket = horizon_rejections[str(horizon.horizon_minutes)]
            bucket[horizon.rejection_code] = bucket.get(horizon.rejection_code, 0) + 1
    target_quality: list[HorizonTargetQuality] = []
    for horizon in config.horizons_minutes:
        subset = [row for row in target_rows if row.horizon_minutes == horizon]
        sources: Counter[str] = Counter(row.source or "<null>" for row in subset)
        target_quality.append(
            HorizonTargetQuality(
                horizon_minutes=horizon,
                accepted_count=len(subset),
                target_offset_seconds=NumericDistribution.from_values(
                    [row.target_offset_seconds for row in subset]
                ),
                source_counts=dict(sources),
            )
        )
    candidate_count = len(evaluations)
    accepted_count = len(accepted)
    warnings = list(extra_warnings)
    if candidate_count == 0:
        warnings.append("snapshot contains no glucose observations; no candidate anchors were evaluated")
    elif accepted_count == 0:
        warnings.append("no candidate anchors satisfied the Phase 2 multi-horizon episode policy")
    if comparison.comparison_status == "skipped_cardinality_mismatch":
        warnings.append(
            "Phase 1.5 forecast_readiness comparison was skipped because the readiness "
            "row count does not match candidate anchors × diagnostics horizons"
        )
    mismatch = comparison.mismatch_summary
    if mismatch.get("phase_1_5_unique_all_horizons_but_phase_2_rejected"):
        warnings.append(
            "some Phase 1.5 unique-all-horizon anchors were rejected by the stricter Phase 2 policy"
        )
    return EpisodeDiagnosticsReport(
        episode_diagnostics_schema_version=EPISODE_DIAGNOSTICS_SCHEMA_VERSION,
        episode_dataset_id=dataset_id,
        input_snapshot_identity=InputSnapshotIdentity(
            snapshot_id=snapshot_id,
            manifest_sha256=snapshot_manifest_sha256,
            schema_version=snapshot_schema_version,
            source_timezone=source_timezone,
        ),
        input_diagnostics_identity=InputDiagnosticsIdentity(
            diagnostics_id=diagnostics_id,
            manifest_sha256=diagnostics_manifest_sha256,
            diagnostics_schema_version=diagnostics_schema_version,
        ),
        configuration=config,
        candidate_anchor_count=candidate_count,
        accepted_episode_count=accepted_count,
        rejected_anchor_count=len(rejected),
        acceptance_rate=None if candidate_count == 0 else accepted_count / candidate_count,
        rejections_by_code=dict(sorted(code_counts.items())),
        rejections_by_horizon=horizon_rejections,
        history_quality=HistoryQuality(
            accepted_history_observed_count=NumericDistribution.from_values(
                [row.history_observed_count for row in accepted_rows]
            ),
            accepted_history_missing_grid_count=NumericDistribution.from_values(
                [row.history_missing_grid_count for row in accepted_rows]
            ),
            accepted_history_max_gap_minutes=NumericDistribution.from_values(
                [row.history_max_observed_gap_minutes for row in accepted_rows]
            ),
        ),
        target_quality_per_horizon=tuple(target_quality),
        diagnostics_comparison=comparison,
        warnings=tuple(warnings),
        limitations=build_limitations(),
    )


def build_limitations() -> tuple[str, ...]:
    return (
        NO_INTERPOLATION_DISCLOSURE,
        NO_CLINICAL_DISCLOSURE,
        "Missing fixed-grid slots are retained as null glucose values with observed_mask=false. "
        "They were not interpolated, forward-filled, or resampled.",
        "Phase 1.5 forecast_readiness is a post-hoc comparison only. Every glucose "
        "timestamp is still evaluated independently under the Phase 2 policy; "
        "eligible_unique is never treated as sufficient eligibility.",
        "This artifact contains observed targets only. It does not include train/validation/test "
        "splits, feature normalization, engineered nutrition, or a trained model.",
    )


def render_readme(
    *,
    manifest: EpisodeManifest,
    report: EpisodeDiagnosticsReport,
) -> str:
    config = manifest.configuration
    lines = [
        "# Forecast episode dataset",
        "",
        "This directory is an immutable Phase 2 episode artifact. It contains supervised-learning",
        "episode indexes, observed historical glucose grids, observed future targets, and",
        "historical event context derived from one snapshot and one diagnostics artifact.",
        "",
        "## Identity",
        "",
        f"- Episode dataset ID: `{manifest.episode_dataset_id}`",
        f"- Snapshot ID: `{manifest.input_snapshot_id}`",
        f"- Snapshot manifest SHA-256: `{manifest.input_snapshot_manifest_sha256}`",
        f"- Diagnostics ID: `{manifest.input_diagnostics_id}`",
        f"- Diagnostics manifest SHA-256: `{manifest.input_diagnostics_manifest_sha256}`",
        f"- Generator version: `{manifest.episode_generator_code_version}`",
        f"- Git SHA: `{manifest.git_sha or 'unavailable'}`",
        f"- Created at (provenance only): `{to_iso8601(manifest.created_at)}`",
        f"- Snapshot display timezone (metadata only): `{report.input_snapshot_identity.source_timezone}`",
        "",
        "All episode timestamps and duration calculations use timezone-aware UTC elapsed time.",
        "Local time-of-day, weekday, and calendar features are not derived in this phase.",
        "",
        "## Files",
        "",
        "- `episodes.parquet`: one row per accepted anchor (primary episode index).",
        "- `episode_glucose_history.parquet`: accepted episode × fixed-grid slot, including missing slots.",
        "- `episode_targets.parquet`: accepted episode × configured horizon, unique observed labels only.",
        "- `episode_events.parquet`: meals, workouts, sleep intervals, and weights in the history window.",
        "- `rejected_anchors.parquet`: every evaluated candidate that was not accepted, with rejection codes.",
        "  Per-horizon candidate counts live in `horizon_diagnostics` (list of structs) so the",
        "  schema is not hardcoded to a fixed set of horizons.",
        "- `diagnostics.json`: aggregate counts, quality summaries, and a post-hoc Phase 1.5 comparison.",
        "- `manifest.json`: checksums, configuration, and input artifact identity.",
        "",
        "## Time windows",
        "",
        "- History window: `[history_start, anchor_time]` where "
        f"`history_start = anchor_time - {config.history_minutes} minutes`.",
        f"- Future window: `(anchor_time, anchor_time + {config.max_horizon_minutes} minutes]`.",
        "- History inclusion for point observations: `history_start <= timestamp <= anchor_time`.",
        "- Target candidates: `ideal_target ± target_tolerance`, and the source timestamp must be "
        "strictly later than the anchor.",
        "- Workout/sleep intervals are included on overlap: `event.start < anchor` AND "
        "`event.end > history_start`. Source interval bounds are not clipped; "
        "`overlap_duration_seconds` is a derived field only.",
        "",
        "## Eligibility rules",
        "",
        "A candidate glucose timestamp becomes an accepted episode only when every rule below passes",
        "and a unique observed target exists at every configured horizon.",
        "",
        f"- Rule A: exactly one glucose observation in the history-start band "
        f"(±{config.history_start_tolerance_minutes:g} minutes around history start).",
        f"- Rule B: at least two observations in the history window, strictly increasing timestamps, "
        f"and every adjacent UTC gap ≤ {config.max_history_gap_minutes:g} minutes. Gaps are not filled.",
        f"- Rule C: exactly one observed future glucose in each target band "
        f"(±{config.target_tolerance_minutes:g} minutes). Ambiguous bands are unlabeled.",
        f"- Rule D: at most one source observation maps to each of {config.grid_position_count} "
        f"fixed grid positions (cadence {config.grid_cadence_minutes} minutes). "
        "Missing slots stay null with `observed_mask=false`. Non-final slots use half-open "
        "bands so a midpoint observation maps to exactly one index.",
        "- Rule E: Phase 1.5 `eligible_unique` is never treated as sufficient final eligibility. "
        "forecast_readiness is compared after evaluation; it does not select candidate anchors.",
        "",
        f"Configured horizons: {', '.join(f'+{h}m' for h in config.horizons_minutes)}.",
        f"Target policy: `{config.target_policy}`.",
        "",
        "## Fixed grid and missing mask",
        "",
        "The history grid is a shape-stable view over raw observations, not a resampled series.",
        "Exact source glucose values are copied when a unique observation falls within the slot",
        "tolerance. Empty slots keep `glucose_mg_dl=null`, `observed_mask=false`, and",
        "`source_timestamp_utc=null`. No value is interpolated, imputed, averaged, or selected",
        "from an ambiguous set.",
        "",
        "## Causal invariant",
        "",
        "Historical inputs and event context use only records with timestamps at or before the",
        "anchor. Target records are strictly later observations. Future meals, workouts, sleep,",
        "weight, and glucose do not enter history tables.",
        "",
        "## What this artifact does not contain",
        "",
        "- Train/validation/test splits",
        "- Feature scaling or normalization",
        "- Engineered nutrition, sleep-session labels, or health scores",
        "- Interpolated or synthetic CGM",
        "- Model weights, forecasts, or performance metrics",
        "",
        "## Limitations and Phase 3",
        "",
        NO_INTERPOLATION_DISCLOSURE,
        "",
        NO_CLINICAL_DISCLOSURE,
        "",
        "Phase 3 may introduce chronological splits, features, and baseline models. Those steps",
        "must not rewrite this artifact or leak future information into earlier training windows.",
        "",
    ]
    return "\n".join(lines)


def format_cli_summary(
    *,
    snapshot_id: str,
    diagnostics_id: str,
    output_dir: str,
    candidate_count: int,
    accepted_count: int,
    rejected_count: int,
    rejections_by_code: dict[str, int],
) -> str:
    lines = [
        "Episode dataset complete",
        "",
        f"Snapshot: {snapshot_id}",
        f"Diagnostics: {diagnostics_id}",
        f"Output: {output_dir}",
        "",
        f"Candidates evaluated: {candidate_count}",
        f"Accepted episodes: {accepted_count}",
        f"Rejected anchors: {rejected_count}",
        "",
        "Top rejection reasons:",
    ]
    if rejections_by_code:
        ranked = sorted(rejections_by_code.items(), key=lambda item: (-item[1], item[0]))
        for code, count in ranked[:5]:
            lines.append(f"  {code}: {count}")
    else:
        lines.append("  none")
    lines.extend(
        [
            "",
            "No interpolation, imputation, feature engineering, splitting, or model training occurred.",
        ]
    )
    return "\n".join(lines)


def rejected_row(
    evaluation: AnchorEvaluation,
    *,
    snapshot_id: str,
) -> RejectedAnchorRow:
    return RejectedAnchorRow(
        anchor_timestamp_utc=evaluation.anchor_timestamp_utc,
        snapshot_id=snapshot_id,
        history_start_timestamp_utc=evaluation.history_start_timestamp_utc,
        rejection_codes=evaluation.rejection_codes,
        history_observed_count=evaluation.history_observed_count,
        history_max_observed_gap_minutes=evaluation.history_max_observed_gap_minutes,
        horizon_diagnostics=tuple(horizon.diagnostic() for horizon in evaluation.horizons),
    )


def validate_history_invariants(rows: Sequence[GlucoseHistoryRow], config: EpisodeConfig) -> None:
    by_episode: dict[str, list[GlucoseHistoryRow]] = {}
    for row in rows:
        by_episode.setdefault(row.episode_id, []).append(row)
    cadence = config.grid_cadence_minutes * 60
    for episode_id, slots in by_episode.items():
        ordered = sorted(slots, key=lambda item: item.grid_index)
        if [item.grid_index for item in ordered] != list(range(config.grid_position_count)):
            raise ValueError(f"{episode_id} grid indexes are not 0..N-1")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            delta = (current.grid_timestamp_utc - previous.grid_timestamp_utc).total_seconds()
            if delta != cadence:
                raise ValueError(f"{episode_id} grid cadence is not exactly {config.grid_cadence_minutes} minutes")
        last = ordered[-1]
        if last.grid_timestamp_utc != ordered[0].grid_timestamp_utc + timedelta(
            minutes=config.history_minutes
        ):
            raise ValueError(f"{episode_id} last grid timestamp does not equal the anchor")
        for slot in ordered:
            if slot.observed_mask:
                if slot.glucose_mg_dl is None or slot.source_timestamp_utc is None:
                    raise ValueError(f"{episode_id} observed grid slot is missing glucose or source timestamp")
            elif (
                slot.glucose_mg_dl is not None
                or slot.source_timestamp_utc is not None
                or slot.source is not None
            ):
                raise ValueError(f"{episode_id} missing grid slot is not fully null")


def validate_target_invariants(
    targets: Sequence[TargetRow],
    episodes: Sequence[EpisodeRow],
    config: EpisodeConfig,
) -> None:
    by_episode = {row.episode_id: row for row in episodes}
    grouped: dict[str, list[TargetRow]] = {}
    tolerance_seconds = config.target_tolerance_minutes * 60
    for row in targets:
        grouped.setdefault(row.episode_id, []).append(row)
        episode = by_episode[row.episode_id]
        if row.target_source_timestamp_utc <= episode.anchor_timestamp_utc:
            raise ValueError("target timestamp is not strictly after the anchor")
        if abs(row.target_offset_seconds) > tolerance_seconds:
            raise ValueError("target offset exceeds configured tolerance")
    for episode_id, episode in by_episode.items():
        rows = grouped.get(episode_id, [])
        horizons = tuple(sorted(item.horizon_minutes for item in rows))
        if horizons != tuple(config.horizons_minutes):
            raise ValueError(f"{episode.episode_id} is missing a required target horizon")


def validate_causal_history(
    history_rows: Sequence[GlucoseHistoryRow],
    event_rows: Sequence[EventRow],
    episodes: Sequence[EpisodeRow],
) -> None:
    anchors = {row.episode_id: row.anchor_timestamp_utc for row in episodes}
    starts = {row.episode_id: row.history_start_timestamp_utc for row in episodes}
    for row in history_rows:
        anchor = anchors[row.episode_id]
        if row.source_timestamp_utc is not None and row.source_timestamp_utc > anchor:
            raise ValueError("future glucose leaked into episode history")
        if row.grid_timestamp_utc > anchor:
            raise ValueError("grid timestamp is after the anchor")
    for row in event_rows:
        anchor = anchors[row.episode_id]
        start = starts[row.episode_id]
        if row.event_timestamp_utc is not None and row.event_timestamp_utc > anchor:
            raise ValueError("future point event leaked into episode context")
        if row.event_start_timestamp_utc is not None and row.event_end_timestamp_utc is not None:
            if not (
                row.event_start_timestamp_utc < anchor and row.event_end_timestamp_utc > start
            ):
                raise ValueError("non-overlapping interval leaked into episode context")


def validate_source_values_unchanged(
    history_rows: Sequence[GlucoseHistoryRow],
    target_rows: Sequence[TargetRow],
    glucose: Sequence[GlucoseRecord],
) -> None:
    by_timestamp: dict[datetime, list[float]] = {}
    for row in glucose:
        by_timestamp.setdefault(row.timestamp, []).append(row.glucose_mg_dl)
    for row in history_rows:
        if not row.observed_mask or row.source_timestamp_utc is None or row.glucose_mg_dl is None:
            continue
        values = by_timestamp.get(row.source_timestamp_utc)
        if values is None or row.glucose_mg_dl not in values:
            raise EpisodeError("episode history glucose does not match a source observation")
    for row in target_rows:
        values = by_timestamp.get(row.target_source_timestamp_utc)
        if values is None or row.target_glucose_mg_dl not in values:
            raise EpisodeError("episode target glucose does not match a source observation")
