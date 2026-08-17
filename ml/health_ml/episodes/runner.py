"""Orchestrate deterministic episode generation. Does not modify source artifacts."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from health_ml import __version__
from health_ml.episodes.artifacts import (
    build_diagnostics_report,
    compare_with_readiness,
    config_compatibility_warnings,
    format_cli_summary,
    rejected_row,
    render_readme,
    write_json,
    write_parquet,
)
from health_ml.episodes.config import EPISODE_DATASET_SCHEMA_VERSION, EpisodeConfig
from health_ml.episodes.contracts import (
    EPISODE_EVENTS_ARROW_SCHEMA,
    EPISODE_GLUCOSE_HISTORY_ARROW_SCHEMA,
    EPISODE_TARGETS_ARROW_SCHEMA,
    EPISODES_ARROW_SCHEMA,
    REJECTED_ANCHORS_ARROW_SCHEMA,
    EpisodeManifest,
    EpisodeRow,
    EventRow,
    GlucoseHistoryRow,
    RejectedAnchorRow,
    TargetRow,
)
from health_ml.episodes.eligibility import AnchorEvaluation, evaluate_all_anchors
from health_ml.episodes.events import context_counts, select_event_context
from health_ml.episodes.inputs import EpisodeInputs, load_episode_inputs
from health_ml.episodes.manifest import episode_dataset_id_for, episode_id_for, file_ref
from health_ml.errors import EpisodeError, EpisodeExistsError
from health_ml.git import try_git_sha


@dataclass(frozen=True)
class EpisodeResult:
    episode_dataset_id: str
    output_dir: Path
    manifest: EpisodeManifest
    cli_summary: str
    accepted_episode_count: int
    rejected_anchor_count: int


def run_episodes(
    snapshot_dir: Path,
    diagnostics_dir: Path,
    output_dir: Path,
    *,
    config: EpisodeConfig | None = None,
    overwrite: bool = False,
    created_at: datetime | None = None,
    git_sha: str | None = None,
    detect_git_sha: bool = True,
    code_version: str = __version__,
    progress: Callable[[str, str], None] | None = None,
) -> EpisodeResult:
    """Build an immutable episode dataset from one snapshot and matching diagnostics."""
    log = progress or (lambda _phase, _name: None)
    resolved = config or EpisodeConfig()
    inputs = load_episode_inputs(snapshot_dir, diagnostics_dir, progress=log)
    if git_sha is not None:
        resolved_git_sha = git_sha
    elif detect_git_sha:
        resolved_git_sha = try_git_sha()
    else:
        resolved_git_sha = None

    dataset_id = episode_dataset_id_for(
        snapshot_id=inputs.snapshot.snapshot_id,
        snapshot_manifest_sha256=inputs.snapshot.manifest_sha256,
        diagnostics_id=inputs.diagnostics.diagnostics_id,
        diagnostics_manifest_sha256=inputs.diagnostics.manifest_sha256,
        config=resolved,
        code_version=code_version,
        git_sha=resolved_git_sha,
    )
    output_root = Path(output_dir)
    final_dir = output_root / inputs.snapshot.snapshot_id / dataset_id
    _reject_output_under_inputs(
        Path(snapshot_dir),
        Path(diagnostics_dir),
        final_dir,
    )
    if final_dir.exists() and not overwrite:
        raise EpisodeExistsError(
            f"Episode dataset already exists: {final_dir}. Pass overwrite=True to replace it."
        )

    created = created_at or datetime.now(tz=UTC)
    if created.tzinfo is None:
        raise EpisodeError("created_at must be timezone-aware")
    created_utc = created.astimezone(UTC)

    log("analyze", "eligibility")
    evaluations = evaluate_all_anchors(inputs.snapshot.glucose, resolved)
    log("ok", "eligibility")

    episode_rows, history_rows, target_rows, event_rows, rejected_rows = _materialize(
        evaluations=evaluations,
        inputs=inputs,
        config=resolved,
        dataset_id=dataset_id,
        log=log,
    )

    extra_warnings = config_compatibility_warnings(
        resolved, inputs.diagnostics.manifest.configuration
    )
    comparison = compare_with_readiness(
        evaluations,
        inputs.diagnostics.readiness_rows,
        episode_horizons=resolved.horizons_minutes,
        diagnostics_horizons=inputs.diagnostics.manifest.configuration.horizons_minutes,
    )
    report = build_diagnostics_report(
        dataset_id=dataset_id,
        snapshot_id=inputs.snapshot.snapshot_id,
        snapshot_manifest_sha256=inputs.snapshot.manifest_sha256,
        snapshot_schema_version=inputs.snapshot.manifest.schema_version,
        source_timezone=inputs.snapshot.manifest.timezone,
        diagnostics_id=inputs.diagnostics.diagnostics_id,
        diagnostics_manifest_sha256=inputs.diagnostics.manifest_sha256,
        diagnostics_schema_version=inputs.diagnostics.manifest.diagnostics_schema_version,
        config=resolved,
        evaluations=evaluations,
        accepted_rows=episode_rows,
        target_rows=target_rows,
        comparison=comparison,
        extra_warnings=extra_warnings,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_root / inputs.snapshot.snapshot_id / f".{dataset_id}.partial"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    try:
        log("write", "episodes.parquet")
        write_parquet(tmp_dir / "episodes.parquet", EPISODES_ARROW_SCHEMA, [row.to_row() for row in episode_rows])
        log("ok", "episodes.parquet")
        log("write", "episode_glucose_history.parquet")
        write_parquet(
            tmp_dir / "episode_glucose_history.parquet",
            EPISODE_GLUCOSE_HISTORY_ARROW_SCHEMA,
            [row.to_row() for row in history_rows],
        )
        log("ok", "episode_glucose_history.parquet")
        log("write", "episode_targets.parquet")
        write_parquet(
            tmp_dir / "episode_targets.parquet",
            EPISODE_TARGETS_ARROW_SCHEMA,
            [row.to_row() for row in target_rows],
        )
        log("ok", "episode_targets.parquet")
        log("write", "episode_events.parquet")
        write_parquet(
            tmp_dir / "episode_events.parquet",
            EPISODE_EVENTS_ARROW_SCHEMA,
            [row.to_row() for row in event_rows],
        )
        log("ok", "episode_events.parquet")
        log("write", "rejected_anchors.parquet")
        write_parquet(
            tmp_dir / "rejected_anchors.parquet",
            REJECTED_ANCHORS_ARROW_SCHEMA,
            [row.to_row() for row in rejected_rows],
        )
        log("ok", "rejected_anchors.parquet")
        log("write", "diagnostics.json")
        write_json(tmp_dir / "diagnostics.json", report.to_json_dict())
        log("ok", "diagnostics.json")

        manifest = EpisodeManifest(
            episode_dataset_schema_version=EPISODE_DATASET_SCHEMA_VERSION,
            episode_dataset_id=dataset_id,
            created_at=created_utc,
            input_snapshot_id=inputs.snapshot.snapshot_id,
            input_snapshot_manifest_sha256=inputs.snapshot.manifest_sha256,
            input_diagnostics_id=inputs.diagnostics.diagnostics_id,
            input_diagnostics_manifest_sha256=inputs.diagnostics.manifest_sha256,
            episode_generator_code_version=code_version,
            git_sha=resolved_git_sha,
            configuration=resolved,
            files={},
            accepted_episode_count=len(episode_rows),
            rejected_anchor_count=len(rejected_rows),
        )
        log("write", "README.md")
        (tmp_dir / "README.md").write_text(render_readme(manifest=manifest, report=report), encoding="utf-8")
        log("ok", "README.md")

        files = {
            "episodes.parquet": file_ref(tmp_dir / "episodes.parquet", row_count=len(episode_rows)),
            "episode_glucose_history.parquet": file_ref(
                tmp_dir / "episode_glucose_history.parquet", row_count=len(history_rows)
            ),
            "episode_targets.parquet": file_ref(
                tmp_dir / "episode_targets.parquet", row_count=len(target_rows)
            ),
            "episode_events.parquet": file_ref(
                tmp_dir / "episode_events.parquet", row_count=len(event_rows)
            ),
            "rejected_anchors.parquet": file_ref(
                tmp_dir / "rejected_anchors.parquet", row_count=len(rejected_rows)
            ),
            "diagnostics.json": file_ref(tmp_dir / "diagnostics.json", row_count=1),
            "README.md": file_ref(tmp_dir / "README.md", row_count=1),
        }
        manifest = manifest.model_copy(update={"files": files})
        log("write", "manifest.json")
        write_json(tmp_dir / "manifest.json", manifest.to_json_dict())
        log("ok", "manifest.json")

        if final_dir.exists():
            shutil.rmtree(final_dir)
        tmp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    summary = format_cli_summary(
        snapshot_id=inputs.snapshot.snapshot_id,
        diagnostics_id=inputs.diagnostics.diagnostics_id,
        output_dir=str(final_dir),
        candidate_count=len(evaluations),
        accepted_count=len(episode_rows),
        rejected_count=len(rejected_rows),
        rejections_by_code=report.rejections_by_code,
    )
    return EpisodeResult(
        episode_dataset_id=dataset_id,
        output_dir=final_dir,
        manifest=manifest,
        cli_summary=summary,
        accepted_episode_count=len(episode_rows),
        rejected_anchor_count=len(rejected_rows),
    )


def _materialize(
    *,
    evaluations: Sequence[AnchorEvaluation],
    inputs: EpisodeInputs,
    config: EpisodeConfig,
    dataset_id: str,
    log: Callable[[str, str], None],
) -> tuple[
    tuple[EpisodeRow, ...],
    tuple[GlucoseHistoryRow, ...],
    tuple[TargetRow, ...],
    tuple[EventRow, ...],
    tuple[RejectedAnchorRow, ...],
]:
    snapshot = inputs.snapshot
    episode_rows: list[EpisodeRow] = []
    history_rows: list[GlucoseHistoryRow] = []
    target_rows: list[TargetRow] = []
    event_rows: list[EventRow] = []
    rejected_rows: list[RejectedAnchorRow] = []
    log("analyze", "event_context")
    for evaluation in evaluations:
        if not evaluation.accepted:
            rejected_rows.append(rejected_row(evaluation, snapshot_id=snapshot.snapshot_id))
            continue
        episode_id = episode_id_for(dataset_id, evaluation.anchor_timestamp_utc)
        events: tuple[EventRow, ...] = ()
        if config.include_event_context:
            events = select_event_context(
                episode_id=episode_id,
                history_start=evaluation.history_start_timestamp_utc,
                anchor=evaluation.anchor_timestamp_utc,
                meals=snapshot.meals,
                workouts=snapshot.workouts,
                sleep=snapshot.sleep,
                weight=snapshot.weight,
            )
        meals_n, workouts_n, sleep_n, weight_n = context_counts(events)
        history_start_record = evaluation.history_start_record
        if history_start_record is None:
            raise EpisodeError("accepted episode is missing a unique history-start observation")
        if evaluation.history_max_observed_gap_minutes is None:
            raise EpisodeError("accepted episode is missing a history gap measurement")
        episode_rows.append(
            EpisodeRow(
                episode_id=episode_id,
                anchor_timestamp_utc=evaluation.anchor_timestamp_utc,
                history_start_timestamp_utc=evaluation.history_start_timestamp_utc,
                history_end_timestamp_utc=evaluation.history_end_timestamp_utc,
                maximum_target_timestamp_utc=evaluation.maximum_target_timestamp_utc,
                snapshot_id=snapshot.snapshot_id,
                snapshot_manifest_sha256=snapshot.manifest_sha256,
                diagnostics_id=inputs.diagnostics.diagnostics_id,
                diagnostics_manifest_sha256=inputs.diagnostics.manifest_sha256,
                history_minutes=config.history_minutes,
                grid_cadence_minutes=config.grid_cadence_minutes,
                grid_position_count=config.grid_position_count,
                max_history_gap_minutes=config.max_history_gap_minutes,
                history_start_tolerance_minutes=config.history_start_tolerance_minutes,
                target_tolerance_minutes=config.target_tolerance_minutes,
                horizons_minutes=config.horizons_minutes,
                history_observed_count=evaluation.history_observed_count,
                history_missing_grid_count=evaluation.grid.missing_count,
                history_max_observed_gap_minutes=evaluation.history_max_observed_gap_minutes,
                history_start_source_timestamp_utc=history_start_record.timestamp,
                anchor_source_timestamp_utc=evaluation.anchor_timestamp_utc,
                meal_context_count=meals_n,
                workout_context_count=workouts_n,
                sleep_context_count=sleep_n,
                weight_context_count=weight_n,
            )
        )
        history_rows.extend(slot.to_history_row(episode_id) for slot in evaluation.grid.slots)
        for horizon in evaluation.horizons:
            record = horizon.target_record
            if record is None:
                raise EpisodeError("accepted episode is missing a unique observed target")
            if record.timestamp <= evaluation.anchor_timestamp_utc:
                raise EpisodeError("target timestamp is not strictly after the anchor")
            offset = (record.timestamp - horizon.ideal_target_timestamp_utc).total_seconds()
            target_rows.append(
                TargetRow(
                    episode_id=episode_id,
                    horizon_minutes=horizon.horizon_minutes,
                    ideal_target_timestamp_utc=horizon.ideal_target_timestamp_utc,
                    target_source_timestamp_utc=record.timestamp,
                    target_glucose_mg_dl=record.glucose_mg_dl,
                    target_offset_seconds=offset,
                    source=record.source,
                )
            )
        event_rows.extend(events)
    log("ok", "event_context")
    return (
        tuple(episode_rows),
        tuple(history_rows),
        tuple(target_rows),
        tuple(event_rows),
        tuple(rejected_rows),
    )


def _reject_output_under_inputs(
    snapshot_dir: Path,
    diagnostics_dir: Path,
    output_path: Path,
) -> None:
    out = output_path.resolve()
    for source in (snapshot_dir.resolve(), diagnostics_dir.resolve()):
        if out == source or source in out.parents:
            raise EpisodeError(
                "Episode output must not be written under the snapshot or diagnostics directory"
            )
