"""Human-readable diagnostics report and CLI summary. No raw personal records."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from health_ml.diagnostics.config import DISPLAY_TIMEZONE_FALLBACK, DiagnosticsConfig
from health_ml.diagnostics.models import (
    ELIGIBILITY_DISCLOSURE,
    SPLIT_DISCLOSURE,
    CategoryStructuralSummary,
    DailyCoverageSummary,
    DiagnosticsReport,
    ForecastReadinessSummary,
    GlucoseGapSummary,
    GlucoseSamplingSummary,
    NumericDistribution,
)
from health_ml.times import to_iso8601

PHASE2_QUESTIONS = (
    "How should later episode generation treat multiple observations inside a target-tolerance band "
    "(leave unlabeled, keep earliest, keep nearest)? This phase counts ambiguity and does not choose.",
    "Should Phase 2 require a continuous cadence-compliant context window, rather than the span-only "
    "minimum-history check used here?",
    "What elapsed context-window length should be frozen before features or labels are generated?",
    "Should a major observed gap terminate a context window, and should observations after the gap "
    "start a new segment? Sensor-replacement gaps are currently ordinary major gaps.",
    "Should duplicate glucose timestamps be distinct anchors, dropped, or flagged as a labeling policy?",
    "Given the contiguous glucose segments reported here, where should a future chronological evaluation "
    "cut be placed so later times cannot enter fitting, normalization, target construction, or selection?",
    "Are the configured horizons and target tolerance the frozen labeling policy, or will they change "
    "before any model is trained?",
)


def build_warnings(
    *,
    structural: dict[str, CategoryStructuralSummary],
    sampling: GlucoseSamplingSummary,
    coverage: DailyCoverageSummary,
    config: DiagnosticsConfig,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for name, summary in structural.items():
        if summary.row_count == 0:
            warnings.append(f"{name} category has zero rows")
        if summary.duplicate_identifier_count:
            warnings.append(
                f"{name} has {summary.duplicate_identifier_count} duplicate identifier(s)"
            )
        if summary.invalid_interval_count:
            warnings.append(
                f"{name} has {summary.invalid_interval_count} invalid interval(s) with start >= end"
            )
        if summary.extends_beyond_bounds_count:
            warnings.append(
                f"{name} has {summary.extends_beyond_bounds_count} interval(s) that extend beyond "
                "the declared snapshot bounds (source intervals are unclipped)"
            )
        if summary.non_overlapping_count:
            warnings.append(
                f"{name} has {summary.non_overlapping_count} interval(s) that do not overlap "
                "the declared snapshot interval"
            )
        elif summary.outside_declared_interval_count and summary.overlapping_snapshot_count is None:
            warnings.append(
                f"{name} has {summary.outside_declared_interval_count} point record(s) outside "
                "the declared snapshot interval [start, end)"
            )
    if sampling.duplicate_timestamp_count:
        warnings.append(
            f"glucose has {sampling.duplicate_timestamp_count} duplicate timestamp(s); "
            "duplicates were preserved"
        )
    if sampling.non_increasing_timestamp_pair_count:
        warnings.append(
            f"glucose source order has {sampling.non_increasing_timestamp_pair_count} "
            "non-increasing adjacent timestamp pair(s); analysis used chronological sort"
        )
    if sampling.warning_gap_count:
        warnings.append(
            f"{sampling.warning_gap_count} glucose gap(s) greater than "
            f"{config.gap_warning_minutes:g} minutes and at most {config.gap_major_minutes:g} minutes"
        )
    if sampling.major_gap_count:
        warnings.append(
            f"{sampling.major_gap_count} glucose gap(s) greater than {config.gap_major_minutes:g} minutes"
        )
    if not sampling.interval_distribution_available:
        warnings.append("fewer than two glucose observations; no sampling-interval distribution is available")
    if config.display_timezone_source == DISPLAY_TIMEZONE_FALLBACK:
        warnings.append(
            f"display timezone {config.display_timezone} was used as a fallback; "
            "it was not present on the snapshot manifest"
        )
    if coverage.local_day_count == 0:
        warnings.append("snapshot interval does not touch any local calendar day")
    return tuple(warnings)


def build_limitations() -> tuple[str, ...]:
    return (
        ELIGIBILITY_DISCLOSURE,
        SPLIT_DISCLOSURE,
        "This diagnostics phase does not interpolate CGM, impute missing values, deduplicate "
        "timestamps, clip source intervals, or repair raw observations.",
        "The minimum-history rule confirms only that some earlier observation exists at or before "
        "T minus the configured span. It does not claim continuous cadence coverage.",
        "glucose_expected_count_estimate is floor(local-day elapsed minutes / configured cadence). "
        "It is not a sensor schedule and does not imply complete expected coverage.",
        "No episodes, prediction targets, features, labels, models, or health conclusions were generated.",
    )


def render_markdown(report: DiagnosticsReport) -> str:
    tz = ZoneInfo(report.configuration.display_timezone)
    snap = report.input_snapshot
    sampling = report.glucose_sampling_summary
    gaps = report.glucose_gap_summary
    coverage = report.daily_coverage_summary
    ready = report.forecast_readiness_summary
    chrono = report.chronological_coverage_summary
    lines = [
        "# Snapshot diagnostics",
        "",
        "## Snapshot identity",
        "",
        f"- Diagnostics ID: `{report.diagnostics_id}`",
        f"- Snapshot ID: `{snap.snapshot_id}`",
        f"- Snapshot manifest SHA-256: `{snap.manifest_checksum}`",
        f"- Declared interval: `{_iso(snap.source_start_utc)}` to `{_iso(snap.source_end_utc)}` "
        f"(`{snap.range_semantics}`)",
        f"- Snapshot timezone: `{snap.source_timezone}`",
        f"- Created at (provenance only): `{_iso(report.created_at)}`",
        "",
        "## Configuration",
        "",
        *_config_bullets(report.configuration),
        "",
        "## Record coverage",
        "",
        "| Category | Rows | In range | Outside range | Overlapping | Extends beyond | Duplicate IDs | Sort |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name in ("glucose", "meals", "workouts", "sleep", "weight"):
        item = report.structural_summary[name]
        dup = "—" if item.duplicate_identifier_count is None else str(item.duplicate_identifier_count)
        overlapping = (
            "—" if item.overlapping_snapshot_count is None else str(item.overlapping_snapshot_count)
        )
        extends = (
            "—" if item.extends_beyond_bounds_count is None else str(item.extends_beyond_bounds_count)
        )
        lines.append(
            f"| {name} | {item.row_count} | {item.in_declared_interval_count} | "
            f"{item.outside_declared_interval_count} | {overlapping} | {extends} | {dup} | "
            f"{item.source_sort_status} |"
        )
    lines.extend(
        [
            "",
            "Point `In range` / `Outside range` use half-open `[start, end)`. "
            "Workout and sleep `Overlapping` uses overlap inclusion "
            "(`start < end` AND `end > start`) and is not clipped. "
            "`Extends beyond` is the Phase 0/1 unclipped-bounds count "
            "(interval start before snapshot start or end after snapshot end).",
            "",
            "## Glucose sampling and gaps",
            "",
            f"- Observed records: {sampling.observed_record_count}",
            f"- Duplicate timestamps: {sampling.duplicate_timestamp_count}",
            f"- Non-increasing source pairs: {sampling.non_increasing_timestamp_pair_count}",
            f"- Interval distribution available: {str(sampling.interval_distribution_available).lower()}",
            *_distribution_lines("Sampling interval (minutes)", sampling.sampling_interval_minutes),
            f"- Warning gaps (>{report.configuration.gap_warning_minutes:g}m, "
            f"≤{report.configuration.gap_major_minutes:g}m): {gaps.warning_count} "
            f"(total {gaps.warning_duration_minutes_total:.1f} minutes)",
            f"- Major gaps (>{report.configuration.gap_major_minutes:g}m): {gaps.major_count} "
            f"(total {gaps.major_duration_minutes_total:.1f} minutes)",
            "",
            "Gaps are elapsed UTC time between adjacent observed timestamps. Major gaps remain "
            "ordinary observed gaps; there is no sensor-replacement special case.",
            "",
            "## Daily coverage",
            "",
            f"Local calendar days use `{report.configuration.display_timezone}`. "
            "Expected glucose counts are cadence estimates from each local day's actual elapsed duration.",
            "",
            "| Local date | Glucose observed | Expected (est.) | Coverage ratio (est.) | Meals | Workouts | Sleep intervals | Weight |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in coverage.rows:
        ratio = "—" if row.glucose_coverage_ratio_estimate is None else f"{row.glucose_coverage_ratio_estimate:.3f}"
        lines.append(
            f"| {row.local_date.isoformat()} | {row.glucose_observed_count} | "
            f"{row.glucose_expected_count_estimate} | {ratio} | {row.meal_count} | "
            f"{row.workout_overlap_count} | {row.raw_sleep_interval_overlap_count} | {row.weight_count} |"
        )
    lines.extend(
        [
            "",
            f"- Local days with any glucose observation: {coverage.local_days_with_glucose}",
            "",
            "## Forecasting readiness",
            "",
            ELIGIBILITY_DISCLOSURE,
            "",
            "| Horizon | Anchors | Unique | Ambiguous | Missing target | Insufficient history | Unique rate | Any-eligible rate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for horizon in ready.horizons:
        unique_rate = "—" if horizon.eligible_unique_rate is None else f"{horizon.eligible_unique_rate:.3f}"
        any_rate = "—" if horizon.eligible_any_rate is None else f"{horizon.eligible_any_rate:.3f}"
        lines.append(
            f"| +{horizon.horizon_minutes}m | {horizon.anchor_count} | {horizon.eligible_unique_count} | "
            f"{horizon.eligible_ambiguous_count} | {horizon.missing_target_count} | "
            f"{horizon.insufficient_history_count} | {unique_rate} | {any_rate} |"
        )
    lines.extend(
        [
            "",
            "Ambiguous bands are counted, not resolved. Missing targets are not interpolated.",
            "",
            "## Chronological coverage",
            "",
            f"- Snapshot interval: `{_iso(chrono.snapshot_start_utc)}` to `{_iso(chrono.snapshot_end_utc)}`",
        ]
    )
    for name in ("glucose", "meals", "workouts", "sleep", "weight"):
        earliest = chrono.earliest_observed_by_category.get(name)
        latest = chrono.latest_observed_by_category.get(name)
        lines.append(
            f"- {name} observed range: {_fmt_range(earliest, latest, tz)}"
        )
    lines.extend(
        [
            f"- Local days with glucose: {chrono.local_days_with_glucose}",
            f"- Local days overlapping a major glucose gap: {chrono.local_days_with_major_glucose_gaps}",
            f"- Contiguous glucose segments (split on major gaps): {len(chrono.contiguous_glucose_segments)}",
        ]
    )
    if chrono.contiguous_glucose_segments:
        lines.extend(
            [
                "",
                "| Segment | Start (UTC) | End (UTC) | Observations | Elapsed minutes |",
                "|---|---|---|---:|---:|",
            ]
        )
        for index, segment in enumerate(chrono.contiguous_glucose_segments, start=1):
            lines.append(
                f"| {index} | `{_iso(segment.start_timestamp_utc)}` | `{_iso(segment.end_timestamp_utc)}` | "
                f"{segment.observation_count} | {segment.elapsed_minutes:.1f} |"
            )
    lines.extend(
        [
            "",
            f"- Suggested future split boundaries: {_fmt_splits(chrono.suggested_split_boundaries_utc)}",
            f"- {chrono.suggested_split_note}",
            "",
            SPLIT_DISCLOSURE,
            "",
            "## Warnings and limitations",
            "",
        ]
    )
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in report.warnings)
        lines.append("")
    else:
        lines.extend(["No warnings.", ""])
    lines.append("Limitations:")
    lines.extend(f"- {item}" for item in report.limitations)
    lines.extend(["", "## Recommended Phase 2 questions", ""])
    lines.extend(f"{index}. {item}" for index, item in enumerate(PHASE2_QUESTIONS, start=1))
    lines.append("")
    return "\n".join(lines)


def format_cli_summary(
    *,
    snapshot_id: str,
    output_dir: str,
    structural: dict,
    gaps: GlucoseGapSummary,
    readiness: ForecastReadinessSummary,
    warning_count: int,
    config: DiagnosticsConfig,
) -> str:
    glucose_rows = structural["glucose"].row_count
    lines = [
        "Snapshot diagnostics complete",
        "",
        f"Input snapshot: {snapshot_id}",
        f"Output: {output_dir}",
        "",
        "Glucose:",
        f"  observed records: {glucose_rows}",
        f"  warning gaps (>{config.gap_warning_minutes:g}m): {gaps.warning_count}",
        f"  major gaps (>{config.gap_major_minutes:g}m): {gaps.major_count}",
        "",
        "Forecast readiness:",
    ]
    for horizon in readiness.horizons:
        lines.append(
            f"  +{horizon.horizon_minutes}m: unique={horizon.eligible_unique_count}, "
            f"ambiguous={horizon.eligible_ambiguous_count}, missing={horizon.missing_target_count}, "
            f"insufficient_history={horizon.insufficient_history_count}"
        )
    lines.extend(
        [
            "",
            f"Per-modality counts: glucose={structural['glucose'].row_count}, "
            f"meals={structural['meals'].row_count}, workouts={structural['workouts'].row_count}, "
            f"sleep={structural['sleep'].row_count}, weight={structural['weight'].row_count}",
            f"Warnings: {warning_count}",
            "",
            "No episodes, targets, features, or models were generated.",
        ]
    )
    return "\n".join(lines)


def _config_bullets(config: DiagnosticsConfig) -> list[str]:
    return [
        f"- Gap warning: {config.gap_warning_minutes:g} minutes",
        f"- Gap major: {config.gap_major_minutes:g} minutes",
        f"- Expected CGM cadence: {config.expected_cgm_cadence_minutes:g} minutes",
        f"- Target tolerance: ±{config.target_tolerance_minutes:g} minutes",
        f"- Minimum history span: {config.minimum_history_minutes:g} minutes",
        f"- Horizons: {', '.join(f'+{h}m' for h in config.horizons_minutes)}",
        f"- Display timezone: {config.display_timezone} ({config.display_timezone_source})",
    ]


def _distribution_lines(title: str, distribution: NumericDistribution) -> list[str]:
    if distribution.count == 0:
        return [f"- {title}: not available"]
    return [
        f"- {title}: count={distribution.count}, min={_num(distribution.min)}, "
        f"p50={_num(distribution.p50)}, p90={_num(distribution.p90)}, "
        f"p95={_num(distribution.p95)}, max={_num(distribution.max)}"
    ]


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def _iso(value: datetime) -> str:
    return to_iso8601(value)


def _fmt_range(earliest: datetime | None, latest: datetime | None, tz: ZoneInfo) -> str:
    if earliest is None or latest is None:
        return "none"
    return f"`{_iso(earliest)}` to `{_iso(latest)}` (UTC; local {tz.key})"


def _fmt_splits(values: tuple[datetime, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(f"`{_iso(value)}`" for value in values)
