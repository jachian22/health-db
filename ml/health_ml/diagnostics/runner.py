"""Orchestrate snapshot diagnostics I/O. Does not modify the source snapshot."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from health_ml import __version__
from health_ml.datasets.manifest import sha256_file
from health_ml.diagnostics.config import (
    DIAGNOSTICS_SCHEMA_VERSION,
    DiagnosticsConfig,
    resolve_display_timezone,
)
from health_ml.diagnostics.coverage import (
    daily_coverage,
    local_dates_touched,
    local_day_bounds,
    structural_summaries,
)
from health_ml.diagnostics.glucose import (
    contiguous_segments,
    detect_glucose_gaps,
    gap_summary,
    sampling_summary,
    writable_gaps,
)
from health_ml.diagnostics.loader import LoadedSnapshot, load_snapshot
from health_ml.diagnostics.models import (
    DAILY_COVERAGE_ARROW_SCHEMA,
    FORECAST_READINESS_ARROW_SCHEMA,
    GLUCOSE_GAPS_ARROW_SCHEMA,
    SPLIT_DISCLOSURE,
    ArtifactFileRef,
    ChronologicalCoverageSummary,
    DiagnosticsManifest,
    DiagnosticsReport,
    GlucoseGap,
    InputSnapshotRef,
)
from health_ml.diagnostics.readiness import forecast_readiness_rows, forecast_readiness_summary
from health_ml.diagnostics.report import (
    build_limitations,
    build_warnings,
    format_cli_summary,
    render_markdown,
)
from health_ml.errors import DiagnosticsError, DiagnosticsExistsError
from health_ml.git import try_git_sha
from health_ml.times import interval_overlaps

OUTPUT_FILES = (
    "diagnostics.json",
    "diagnostics.md",
    "glucose_gaps.parquet",
    "daily_coverage.parquet",
    "forecast_readiness.parquet",
    "manifest.json",
)

_MIN_SPLIT_OBSERVATIONS = 100
_MIN_SPLIT_SEGMENTS = 2


@dataclass(frozen=True)
class DiagnosticsResult:
    diagnostics_id: str
    output_dir: Path
    report: DiagnosticsReport
    manifest: DiagnosticsManifest
    cli_summary: str


def diagnostics_id_for(
    snapshot_id: str,
    config: DiagnosticsConfig,
    *,
    manifest_sha256: str,
    source_file_checksums: dict[str, str],
    code_version: str = __version__,
    schema_version: str = DIAGNOSTICS_SCHEMA_VERSION,
) -> str:
    payload = {
        "snapshot_id": snapshot_id,
        "input_snapshot_manifest_sha256": manifest_sha256,
        "source_file_checksums": source_file_checksums,
        "diagnostics_schema_version": schema_version,
        "diagnostics_code_version": code_version,
        "configuration": config.identity_payload(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"d{schema_version}_{digest}"


def run_diagnostics(
    snapshot_dir: Path,
    output_dir: Path,
    *,
    config: DiagnosticsConfig | None = None,
    display_timezone: str | None = None,
    overwrite: bool = False,
    created_at: datetime | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> DiagnosticsResult:
    snapshot_path = Path(snapshot_dir)
    output_root = Path(output_dir)

    log = progress or (lambda _phase, _name: None)
    loaded = load_snapshot(snapshot_path, progress=log)

    timezone_name, timezone_source = resolve_display_timezone(
        cli_timezone=display_timezone,
        snapshot_timezone=loaded.manifest.timezone,
    )
    base = config or DiagnosticsConfig()
    resolved = base.model_copy(
        update={
            "display_timezone": timezone_name,
            "display_timezone_source": timezone_source,
        }
    )

    diagnostics_id = diagnostics_id_for(
        loaded.snapshot_id,
        resolved,
        manifest_sha256=loaded.manifest_sha256,
        source_file_checksums=loaded.source_file_checksums,
    )
    final_dir = output_root / loaded.snapshot_id / diagnostics_id
    _reject_output_under_snapshot(snapshot_path, final_dir)
    if final_dir.exists() and not overwrite:
        raise DiagnosticsExistsError(
            f"Diagnostics artifact already exists: {final_dir}. Pass overwrite=True to replace it."
        )

    created = created_at or datetime.now(tz=UTC)
    if created.tzinfo is None:
        raise DiagnosticsError("created_at must be timezone-aware")
    created_utc = created.astimezone(UTC)

    log("analyze", "structural")
    structural = structural_summaries(loaded)
    timestamps = [row.timestamp for row in loaded.glucose]
    gaps = detect_glucose_gaps(timestamps, resolved)
    written_gaps = writable_gaps(gaps)
    sampling = sampling_summary(loaded.glucose, loaded.glucose_source_order, gaps)
    gap_stats = gap_summary(gaps)
    log("ok", "structural")
    log("analyze", "daily_coverage")
    coverage = daily_coverage(loaded, resolved)
    log("ok", "daily_coverage")
    log("analyze", "forecast_readiness")
    readiness_rows = forecast_readiness_rows(loaded.glucose, resolved)
    readiness = forecast_readiness_summary(readiness_rows, resolved)
    log("ok", "forecast_readiness")
    chrono = _chronological_summary(loaded, resolved, coverage.local_days_with_glucose, gaps)
    warnings = build_warnings(
        structural=structural,
        sampling=sampling,
        coverage=coverage,
        config=resolved,
    )
    report = DiagnosticsReport(
        diagnostics_schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        diagnostics_id=diagnostics_id,
        created_at=created_utc,
        input_snapshot=InputSnapshotRef(
            snapshot_id=loaded.snapshot_id,
            manifest_checksum=loaded.manifest_sha256,
            source_start_utc=loaded.manifest.source_start,
            source_end_utc=loaded.manifest.source_end,
            source_timezone=loaded.manifest.timezone,
            source_file_checksums=loaded.source_file_checksums,
            range_semantics=loaded.manifest.request.range_semantics,
        ),
        configuration=resolved,
        structural_summary=structural,
        glucose_sampling_summary=sampling,
        glucose_gap_summary=gap_stats,
        daily_coverage_summary=coverage,
        forecast_readiness_summary=readiness,
        chronological_coverage_summary=chrono,
        warnings=warnings,
        limitations=build_limitations(),
    )

    output_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_root / loaded.snapshot_id / f".{diagnostics_id}.partial"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    try:
        log("write", "glucose_gaps.parquet")
        _write_parquet(
            tmp_dir / "glucose_gaps.parquet",
            GLUCOSE_GAPS_ARROW_SCHEMA,
            [gap.to_row() for gap in written_gaps],
        )
        log("ok", "glucose_gaps.parquet")
        log("write", "daily_coverage.parquet")
        _write_parquet(
            tmp_dir / "daily_coverage.parquet",
            DAILY_COVERAGE_ARROW_SCHEMA,
            [row.to_row() for row in coverage.rows],
        )
        log("ok", "daily_coverage.parquet")
        log("write", "forecast_readiness.parquet")
        _write_parquet(
            tmp_dir / "forecast_readiness.parquet",
            FORECAST_READINESS_ARROW_SCHEMA,
            [row.to_row() for row in readiness_rows],
        )
        log("ok", "forecast_readiness.parquet")
        log("write", "diagnostics.json")
        (tmp_dir / "diagnostics.json").write_text(
            json.dumps(report.to_json_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        log("ok", "diagnostics.json")
        log("write", "diagnostics.md")
        (tmp_dir / "diagnostics.md").write_text(render_markdown(report), encoding="utf-8")
        log("ok", "diagnostics.md")

        files = {
            "diagnostics.json": _file_ref(tmp_dir / "diagnostics.json", rows=1),
            "diagnostics.md": _file_ref(tmp_dir / "diagnostics.md", rows=1),
            "glucose_gaps.parquet": _file_ref(
                tmp_dir / "glucose_gaps.parquet",
                rows=len(written_gaps),
            ),
            "daily_coverage.parquet": _file_ref(
                tmp_dir / "daily_coverage.parquet",
                rows=len(coverage.rows),
            ),
            "forecast_readiness.parquet": _file_ref(
                tmp_dir / "forecast_readiness.parquet",
                rows=len(readiness_rows),
            ),
            # manifest.json is omitted: a self-checksum cannot be stable.
        }
        manifest = DiagnosticsManifest(
            diagnostics_schema_version=DIAGNOSTICS_SCHEMA_VERSION,
            diagnostics_id=diagnostics_id,
            created_at=created_utc,
            input_snapshot_id=loaded.snapshot_id,
            input_snapshot_manifest_sha256=loaded.manifest_sha256,
            diagnostics_code_version=__version__,
            git_sha=try_git_sha(),
            configuration=resolved,
            files=files,
        )
        log("write", "manifest.json")
        (tmp_dir / "manifest.json").write_text(
            json.dumps(manifest.to_json_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        log("ok", "manifest.json")

        if final_dir.exists():
            shutil.rmtree(final_dir)
        tmp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    summary = format_cli_summary(
        snapshot_id=loaded.snapshot_id,
        output_dir=str(final_dir),
        structural=structural,
        gaps=gap_stats,
        readiness=readiness,
        warning_count=len(warnings),
        config=resolved,
    )
    return DiagnosticsResult(
        diagnostics_id=diagnostics_id,
        output_dir=final_dir,
        report=report,
        manifest=manifest,
        cli_summary=summary,
    )


def _chronological_summary(
    loaded: LoadedSnapshot,
    config: DiagnosticsConfig,
    local_days_with_glucose: int,
    gaps: Sequence[GlucoseGap],
) -> ChronologicalCoverageSummary:
    tz = ZoneInfo(config.display_timezone)
    timestamps = [row.timestamp for row in loaded.glucose]
    segments = contiguous_segments(timestamps, config)
    major_gap_days = _local_days_with_major_gaps(
        gaps,
        loaded.manifest.source_start,
        loaded.manifest.source_end,
        tz,
    )
    observation_count = len(timestamps)
    if (
        observation_count >= _MIN_SPLIT_OBSERVATIONS
        and len(segments) >= _MIN_SPLIT_SEGMENTS
    ):
        boundaries = tuple(segment.start_timestamp_utc for segment in segments[1:])
        note = (
            "Advisory cut points at the first observation of each contiguous glucose segment "
            "after the first. These are not train/validation/test assignments."
        )
    else:
        boundaries = ()
        note = (
            "No candidate chronological split boundaries are suggested; observation volume "
            "or segment count is too small to make a split meaningful."
        )
    earliest = {
        "glucose": min((row.timestamp for row in loaded.glucose), default=None),
        "meals": min((row.timestamp for row in loaded.meals), default=None),
        "workouts": min((row.start for row in loaded.workouts), default=None),
        "sleep": min((row.start for row in loaded.sleep), default=None),
        "weight": min((row.timestamp for row in loaded.weight), default=None),
    }
    latest = {
        "glucose": max((row.timestamp for row in loaded.glucose), default=None),
        "meals": max((row.timestamp for row in loaded.meals), default=None),
        "workouts": max((row.end for row in loaded.workouts), default=None),
        "sleep": max((row.end for row in loaded.sleep), default=None),
        "weight": max((row.timestamp for row in loaded.weight), default=None),
    }
    return ChronologicalCoverageSummary(
        snapshot_start_utc=loaded.manifest.source_start,
        snapshot_end_utc=loaded.manifest.source_end,
        earliest_observed_by_category=earliest,
        latest_observed_by_category=latest,
        local_days_with_glucose=local_days_with_glucose,
        local_days_with_major_glucose_gaps=len(major_gap_days),
        contiguous_glucose_segments=segments,
        suggested_split_boundaries_utc=boundaries,
        suggested_split_note=note,
        split_disclosure=SPLIT_DISCLOSURE,
    )


def _local_days_with_major_gaps(
    gaps: Sequence[GlucoseGap],
    snapshot_start: datetime,
    snapshot_end: datetime,
    tz: ZoneInfo,
) -> set:
    major = [gap for gap in gaps if gap.classification == "major"]
    if not major:
        return set()
    days = set()
    for day in local_dates_touched(snapshot_start, snapshot_end, tz):
        day_start, day_end = local_day_bounds(day, tz)
        for gap in major:
            if interval_overlaps(gap.previous_timestamp_utc, gap.next_timestamp_utc, day_start, day_end):
                days.add(day)
                break
    return days


def _write_parquet(path: Path, schema: pa.Schema, rows: list[dict]) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")


def _file_ref(path: Path, *, rows: int) -> ArtifactFileRef:
    return ArtifactFileRef(path=path.name, rows=rows, sha256=sha256_file(path))


def _reject_output_under_snapshot(snapshot_dir: Path, output_path: Path) -> None:
    snap = snapshot_dir.resolve()
    out = output_path.resolve()
    if out == snap or snap in out.parents:
        raise DiagnosticsError("Diagnostics output must not be written under the snapshot directory")


__all__ = ["DiagnosticsResult", "diagnostics_id_for", "run_diagnostics"]
