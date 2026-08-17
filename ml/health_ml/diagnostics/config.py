"""Immutable diagnostics configuration. Controls measurement, not data repair."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from health_ml.config import DEFAULT_QUERY_TIMEZONE
from health_ml.errors import DiagnosticsValidationError
from health_ml.parsing import parse_horizon_list

DIAGNOSTICS_SCHEMA_VERSION = "1.5"
DEFAULT_GAP_WARNING_MINUTES = 15.0
DEFAULT_GAP_MAJOR_MINUTES = 60.0
DEFAULT_EXPECTED_CGM_CADENCE_MINUTES = 5.0
DEFAULT_TARGET_TOLERANCE_MINUTES = 2.5
DEFAULT_MINIMUM_HISTORY_MINUTES = 120.0
DEFAULT_HORIZONS_MINUTES = (30, 60, 120)
DISPLAY_TIMEZONE_CLI = "cli"
DISPLAY_TIMEZONE_SNAPSHOT = "snapshot_manifest"
DISPLAY_TIMEZONE_FALLBACK = "default_fallback"


def parse_horizons_minutes(value: str | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    try:
        return parse_horizon_list(value)
    except ValueError as exc:
        raise DiagnosticsValidationError(str(exc)) from exc


class DiagnosticsConfig(BaseModel):
    """Typed measurement settings. Never used to interpolate, impute, or clip source data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_warning_minutes: float = DEFAULT_GAP_WARNING_MINUTES
    gap_major_minutes: float = DEFAULT_GAP_MAJOR_MINUTES
    expected_cgm_cadence_minutes: float = DEFAULT_EXPECTED_CGM_CADENCE_MINUTES
    target_tolerance_minutes: float = DEFAULT_TARGET_TOLERANCE_MINUTES
    minimum_history_minutes: float = DEFAULT_MINIMUM_HISTORY_MINUTES
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES
    display_timezone: str = DEFAULT_QUERY_TIMEZONE
    display_timezone_source: str = DISPLAY_TIMEZONE_SNAPSHOT

    @field_validator("horizons_minutes", mode="before")
    @classmethod
    def coerce_horizons(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_horizons_minutes(value)
        return value

    @field_validator("display_timezone")
    @classmethod
    def timezone_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"display timezone must be a valid IANA timezone name: {value}") from exc
        return value

    @field_validator("display_timezone_source")
    @classmethod
    def timezone_source_known(cls, value: str) -> str:
        allowed = {DISPLAY_TIMEZONE_CLI, DISPLAY_TIMEZONE_SNAPSHOT, DISPLAY_TIMEZONE_FALLBACK}
        if value not in allowed:
            raise ValueError(f"display_timezone_source must be one of {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def check_thresholds(self) -> DiagnosticsConfig:
        problems: list[str] = []
        if self.expected_cgm_cadence_minutes <= 0:
            problems.append("expected-cgm-cadence-minutes must be greater than 0")
        if self.gap_warning_minutes <= 0:
            problems.append("gap-warning-minutes must be greater than 0")
        if self.gap_major_minutes < self.gap_warning_minutes:
            problems.append("gap-major-minutes must be greater than or equal to gap-warning-minutes")
        if self.target_tolerance_minutes <= 0:
            problems.append("target-tolerance-minutes must be greater than 0")
        if self.minimum_history_minutes < 0:
            problems.append("minimum-history-minutes must be greater than or equal to 0")
        horizons = self.horizons_minutes
        if not horizons:
            problems.append("horizons-minutes must contain at least one horizon")
        else:
            if any(h <= 0 for h in horizons):
                problems.append("horizons-minutes must be positive integers")
            if len(set(horizons)) != len(horizons):
                problems.append("horizons-minutes must be unique")
            if list(horizons) != sorted(horizons):
                problems.append("horizons-minutes must be in ascending order")
            if self.target_tolerance_minutes >= min(horizons):
                problems.append("target-tolerance-minutes must be less than the minimum requested horizon")
        if problems:
            raise DiagnosticsValidationError(
                "Invalid diagnostics configuration:\n" + "\n".join(f"  - {item}" for item in problems),
                problems,
            )
        return self

    def identity_payload(self) -> dict[str, Any]:
        """Fields that participate in diagnostics identity. `created_at` is excluded."""
        return {
            "gap_warning_minutes": self.gap_warning_minutes,
            "gap_major_minutes": self.gap_major_minutes,
            "expected_cgm_cadence_minutes": self.expected_cgm_cadence_minutes,
            "target_tolerance_minutes": self.target_tolerance_minutes,
            "minimum_history_minutes": self.minimum_history_minutes,
            "horizons_minutes": list(self.horizons_minutes),
            "display_timezone": self.display_timezone,
        }

    def to_json_dict(self) -> dict[str, Any]:
        payload = self.identity_payload()
        payload["display_timezone_source"] = self.display_timezone_source
        return payload


def resolve_display_timezone(
    *,
    cli_timezone: str | None,
    snapshot_timezone: str | None,
) -> tuple[str, str]:
    """Return (timezone, source). CLI wins; then snapshot manifest; then America/New_York."""
    if cli_timezone is not None and cli_timezone.strip():
        return cli_timezone, DISPLAY_TIMEZONE_CLI
    if snapshot_timezone is not None and snapshot_timezone.strip():
        return snapshot_timezone, DISPLAY_TIMEZONE_SNAPSHOT
    return DEFAULT_QUERY_TIMEZONE, DISPLAY_TIMEZONE_FALLBACK
