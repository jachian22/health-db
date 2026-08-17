"""CLI for snapshots, diagnostics, and forecast-episode datasets.

    python -m health_ml.cli build-dataset \
      --start 2026-08-01T00:00:00-04:00 \
      --end 2026-08-16T23:59:59-04:00 \
      --output data/snapshots

    python -m health_ml.cli diagnose-snapshot \
      --snapshot data/snapshots/<snapshot-id> \
      --output data/diagnostics

    python -m health_ml.cli build-episodes \
      --snapshot data/snapshots/<snapshot-id> \
      --diagnostics data/diagnostics/<snapshot-id>/<diagnostics-id> \
      --output data/episodes
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from health_ml.clients.health_api import HealthDataClient
from health_ml.config import (
    DEFAULT_QUERY_TIMEZONE,
    Settings,
    get_settings,
)
from health_ml.datasets.snapshot import build_snapshot
from health_ml.diagnostics.config import (
    DEFAULT_EXPECTED_CGM_CADENCE_MINUTES,
    DEFAULT_GAP_MAJOR_MINUTES,
    DEFAULT_GAP_WARNING_MINUTES,
    DEFAULT_HORIZONS_MINUTES,
    DEFAULT_MINIMUM_HISTORY_MINUTES,
    DEFAULT_TARGET_TOLERANCE_MINUTES,
    DiagnosticsConfig,
    parse_horizons_minutes,
)
from health_ml.diagnostics.runner import run_diagnostics
from health_ml.episodes import config as episode_config
from health_ml.episodes.runner import run_episodes
from health_ml.errors import DiagnosticsValidationError, EpisodeValidationError, HealthMLError


def parse_cli_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid timestamp {value!r}; use ISO-8601 with timezone"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone information")
    return parsed


def _format_local(value: datetime, timezone: str) -> str:
    local = value.astimezone(ZoneInfo(timezone))
    return local.strftime("%Y-%m-%d %H:%M %Z")


class _Printer:
    def __init__(self) -> None:
        self._started: set[str] = set()

    def __call__(self, phase: str, name: str) -> None:
        labels = {
            "fetch": "Fetching:",
            "load": "Loading:",
            "analyze": "Computing:",
            "write": "Writing:",
        }
        if phase in labels and phase not in self._started:
            print(f"\n{labels[phase]}")
            self._started.add(phase)
        if phase == "ok":
            print(f"  ✓ {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="health_ml.cli",
        description="Extract canonical health data into a versioned Parquet snapshot, "
        "diagnose an existing snapshot, or build a forecast-episode dataset.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-dataset", help="Build an immutable snapshot from the Query API")
    build.add_argument(
        "--start",
        type=parse_cli_datetime,
        required=True,
        help="Inclusive range start (ISO-8601 with timezone)",
    )
    build.add_argument(
        "--end",
        type=parse_cli_datetime,
        required=True,
        help="Exclusive range end (ISO-8601 with timezone)",
    )
    build.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Parent directory for snapshots (e.g. data/snapshots)",
    )
    build.add_argument(
        "--timezone",
        default=DEFAULT_QUERY_TIMEZONE,
        help=f"IANA timezone recorded in the manifest (default {DEFAULT_QUERY_TIMEZONE})",
    )
    build.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing snapshot with the same id",
    )

    diagnose = sub.add_parser(
        "diagnose-snapshot",
        help="Diagnose an existing immutable snapshot without calling the Query API",
    )
    diagnose.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="Snapshot directory (e.g. data/snapshots/<snapshot-id>)",
    )
    diagnose.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Parent directory for diagnostics artifacts (e.g. data/diagnostics)",
    )
    diagnose.add_argument(
        "--gap-warning-minutes",
        type=float,
        default=DEFAULT_GAP_WARNING_MINUTES,
        help=f"Warning gap threshold in minutes (default {DEFAULT_GAP_WARNING_MINUTES:g})",
    )
    diagnose.add_argument(
        "--gap-major-minutes",
        type=float,
        default=DEFAULT_GAP_MAJOR_MINUTES,
        help=f"Major gap threshold in minutes (default {DEFAULT_GAP_MAJOR_MINUTES:g})",
    )
    diagnose.add_argument(
        "--expected-cgm-cadence-minutes",
        type=float,
        default=DEFAULT_EXPECTED_CGM_CADENCE_MINUTES,
        help=f"Expected CGM cadence in minutes (default {DEFAULT_EXPECTED_CGM_CADENCE_MINUTES:g})",
    )
    diagnose.add_argument(
        "--target-tolerance-minutes",
        type=float,
        default=DEFAULT_TARGET_TOLERANCE_MINUTES,
        help=f"Closed target-band half-width in minutes (default {DEFAULT_TARGET_TOLERANCE_MINUTES:g})",
    )
    diagnose.add_argument(
        "--minimum-history-minutes",
        type=float,
        default=DEFAULT_MINIMUM_HISTORY_MINUTES,
        help=f"Minimum observed history span in minutes (default {DEFAULT_MINIMUM_HISTORY_MINUTES:g})",
    )
    diagnose.add_argument(
        "--horizons-minutes",
        default=",".join(str(item) for item in DEFAULT_HORIZONS_MINUTES),
        help="Comma-separated forecast horizons in minutes (default 30,60,120)",
    )
    diagnose.add_argument(
        "--display-timezone",
        default=None,
        help=(
            "IANA timezone for local-day reporting. Default: snapshot manifest timezone, "
            f"else {DEFAULT_QUERY_TIMEZONE}"
        ),
    )
    diagnose.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing diagnostics artifact with the same id",
    )

    episodes = sub.add_parser(
        "build-episodes",
        help="Build an immutable forecast-episode dataset from a snapshot and diagnostics artifact",
    )
    episodes.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="Snapshot directory (e.g. data/snapshots/<snapshot-id>)",
    )
    episodes.add_argument(
        "--diagnostics",
        type=Path,
        required=True,
        help="Diagnostics directory (e.g. data/diagnostics/<snapshot-id>/<diagnostics-id>)",
    )
    episodes.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Parent directory for episode datasets (e.g. data/episodes)",
    )
    episodes.add_argument(
        "--history-minutes",
        type=int,
        default=episode_config.DEFAULT_HISTORY_MINUTES,
        help=f"Inclusive history length in minutes (default {episode_config.DEFAULT_HISTORY_MINUTES})",
    )
    episodes.add_argument(
        "--grid-cadence-minutes",
        type=int,
        default=episode_config.DEFAULT_GRID_CADENCE_MINUTES,
        help=f"Fixed-grid cadence in minutes (default {episode_config.DEFAULT_GRID_CADENCE_MINUTES})",
    )
    episodes.add_argument(
        "--max-history-gap-minutes",
        type=float,
        default=episode_config.DEFAULT_MAX_HISTORY_GAP_MINUTES,
        help=f"Maximum adjacent observed history gap in minutes (default {episode_config.DEFAULT_MAX_HISTORY_GAP_MINUTES:g})",
    )
    episodes.add_argument(
        "--history-start-tolerance-minutes",
        type=float,
        default=episode_config.DEFAULT_HISTORY_START_TOLERANCE_MINUTES,
        help=(
            "Closed history-start band half-width in minutes "
            f"(default {episode_config.DEFAULT_HISTORY_START_TOLERANCE_MINUTES:g})"
        ),
    )
    episodes.add_argument(
        "--target-tolerance-minutes",
        type=float,
        default=episode_config.DEFAULT_TARGET_TOLERANCE_MINUTES,
        help=f"Closed target-band half-width in minutes (default {episode_config.DEFAULT_TARGET_TOLERANCE_MINUTES:g})",
    )
    episodes.add_argument(
        "--horizons-minutes",
        default=",".join(str(item) for item in episode_config.DEFAULT_HORIZONS_MINUTES),
        help="Comma-separated forecast horizons in minutes (default 30,60,120)",
    )
    episodes.add_argument(
        "--target-policy",
        default=episode_config.TARGET_POLICY_UNIQUE_ONLY,
        help='Target selection policy (Phase 2 supports only "unique-only")',
    )
    episodes.add_argument(
        "--include-event-context",
        default=str(episode_config.DEFAULT_INCLUDE_EVENT_CONTEXT).lower(),
        help="Include historical meal/workout/sleep/weight context (default true)",
    )
    episodes.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing episode dataset with the same id",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client: HealthDataClient | None = None,
    settings: Settings | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build-dataset":
        return _cmd_build_dataset(args, client=client, settings=settings)
    if args.command == "diagnose-snapshot":
        return _cmd_diagnose_snapshot(args)
    if args.command == "build-episodes":
        return _cmd_build_episodes(args)
    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_build_dataset(
    args: argparse.Namespace,
    *,
    client: HealthDataClient | None,
    settings: Settings | None,
) -> int:
    if client is None and settings is None:
        try:
            settings = get_settings()
        except ValidationError:
            print(
                "Missing configuration. Set HEALTH_API_URL and HEALTH_API_READ_KEY "
                "(QUERY_API_BASE_URL / READ_API_KEY are accepted as aliases).",
                file=sys.stderr,
            )
            return 1

    timezone = args.timezone
    print("Building health dataset")
    print(f"  Start: {_format_local(args.start, timezone)}")
    print(f"  End:   {_format_local(args.end, timezone)}")

    try:
        result = build_snapshot(
            args.start,
            args.end,
            args.output,
            client=client,
            settings=settings,
            overwrite=args.overwrite,
            timezone=timezone,
            progress=_Printer(),
        )
    except HealthMLError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"\nError: invalid configuration ({exc.error_count()} issues)", file=sys.stderr)
        return 1

    print("\nSnapshot complete.")
    print(f"  Path: {result.output_dir}")
    print()
    print(result.diagnostics.format())
    return 0


def _cmd_diagnose_snapshot(args: argparse.Namespace) -> int:
    try:
        horizons = parse_horizons_minutes(args.horizons_minutes)
        config = DiagnosticsConfig(
            gap_warning_minutes=args.gap_warning_minutes,
            gap_major_minutes=args.gap_major_minutes,
            expected_cgm_cadence_minutes=args.expected_cgm_cadence_minutes,
            target_tolerance_minutes=args.target_tolerance_minutes,
            minimum_history_minutes=args.minimum_history_minutes,
            horizons_minutes=horizons,
        )
    except (DiagnosticsValidationError, ValidationError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print("Diagnosing snapshot")
    print(f"  Snapshot: {args.snapshot}")
    print(f"  Output:   {args.output}")

    try:
        result = run_diagnostics(
            args.snapshot,
            args.output,
            config=config,
            display_timezone=args.display_timezone,
            overwrite=args.overwrite,
            progress=_Printer(),
        )
    except HealthMLError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"\nError: invalid configuration ({exc.error_count()} issues)", file=sys.stderr)
        return 1

    print()
    print(result.cli_summary)
    return 0


def _cmd_build_episodes(args: argparse.Namespace) -> int:
    try:
        horizons = episode_config.parse_horizons_minutes(args.horizons_minutes)
        include_events = episode_config.parse_bool_flag(args.include_event_context)
        config = episode_config.EpisodeConfig(
            history_minutes=args.history_minutes,
            grid_cadence_minutes=args.grid_cadence_minutes,
            max_history_gap_minutes=args.max_history_gap_minutes,
            history_start_tolerance_minutes=args.history_start_tolerance_minutes,
            target_tolerance_minutes=args.target_tolerance_minutes,
            horizons_minutes=horizons,
            target_policy=args.target_policy,
            include_event_context=include_events,
        )
    except (EpisodeValidationError, ValidationError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print("Building episode dataset")
    print(f"  Snapshot:    {args.snapshot}")
    print(f"  Diagnostics: {args.diagnostics}")
    print(f"  Output:      {args.output}")

    try:
        result = run_episodes(
            args.snapshot,
            args.diagnostics,
            args.output,
            config=config,
            overwrite=args.overwrite,
            progress=_Printer(),
        )
    except HealthMLError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"\nError: invalid configuration ({exc.error_count()} issues)", file=sys.stderr)
        return 1

    print()
    print(result.cli_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
