"""Immutable Phase 2 episode-generation configuration.

This policy is independent of Phase 1.5 diagnostics settings. Differing
diagnostics values are reported, never silently adopted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from health_ml.errors import EpisodeValidationError
from health_ml.parsing import parse_horizon_list

EPISODE_DATASET_SCHEMA_VERSION = "2.0"
EPISODE_DIAGNOSTICS_SCHEMA_VERSION = "2.0"
TARGET_POLICY_UNIQUE_ONLY: Literal["unique-only"] = "unique-only"

DEFAULT_HISTORY_MINUTES = 120
DEFAULT_GRID_CADENCE_MINUTES = 5
DEFAULT_MAX_HISTORY_GAP_MINUTES = 15.0
DEFAULT_HISTORY_START_TOLERANCE_MINUTES = 2.5
DEFAULT_TARGET_TOLERANCE_MINUTES = 2.5
DEFAULT_HORIZONS_MINUTES = (30, 60, 120)
DEFAULT_INCLUDE_EVENT_CONTEXT = True


def parse_horizons_minutes(value: str | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    try:
        return parse_horizon_list(value)
    except ValueError as exc:
        raise EpisodeValidationError(str(exc)) from exc


def parse_bool_flag(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    text = value.strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise EpisodeValidationError("include-event-context must be true or false")


class EpisodeConfig(BaseModel):
    """Typed episode policy. Never used to interpolate, impute, or alter source data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    history_minutes: int = DEFAULT_HISTORY_MINUTES
    grid_cadence_minutes: int = DEFAULT_GRID_CADENCE_MINUTES
    max_history_gap_minutes: float = DEFAULT_MAX_HISTORY_GAP_MINUTES
    history_start_tolerance_minutes: float = DEFAULT_HISTORY_START_TOLERANCE_MINUTES
    target_tolerance_minutes: float = DEFAULT_TARGET_TOLERANCE_MINUTES
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES
    target_policy: Literal["unique-only"] = TARGET_POLICY_UNIQUE_ONLY
    include_event_context: bool = DEFAULT_INCLUDE_EVENT_CONTEXT

    @field_validator("horizons_minutes", mode="before")
    @classmethod
    def coerce_horizons(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_horizons_minutes(value)
        return value

    @field_validator("include_event_context", mode="before")
    @classmethod
    def coerce_include_events(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_bool_flag(value)
        return value

    @model_validator(mode="after")
    def check_policy(self) -> EpisodeConfig:
        problems: list[str] = []
        if self.history_minutes <= 0:
            problems.append("history-minutes must be greater than 0")
        if self.grid_cadence_minutes <= 0:
            problems.append("grid-cadence-minutes must be greater than 0")
        if self.history_minutes > 0 and self.grid_cadence_minutes > 0:
            if self.history_minutes % self.grid_cadence_minutes != 0:
                problems.append("history-minutes must be divisible by grid-cadence-minutes")
        if self.max_history_gap_minutes <= 0:
            problems.append("max-history-gap-minutes must be greater than 0")
        if self.history_start_tolerance_minutes < 0:
            problems.append("history-start-tolerance-minutes must be greater than or equal to 0")
        if self.target_tolerance_minutes <= 0:
            problems.append("target-tolerance-minutes must be greater than 0")
        if self.target_policy != TARGET_POLICY_UNIQUE_ONLY:
            problems.append('target-policy must be exactly "unique-only" for Phase 2')
        horizons = self.horizons_minutes
        if not horizons:
            problems.append("horizons-minutes must contain at least one horizon")
        else:
            if any(horizon <= 0 for horizon in horizons):
                problems.append("horizons-minutes must be positive integers")
            if len(set(horizons)) != len(horizons):
                problems.append("horizons-minutes must be unique")
            if list(horizons) != sorted(horizons):
                problems.append("horizons-minutes must be in ascending order")
        if problems:
            raise EpisodeValidationError(
                "Invalid episode configuration:\n" + "\n".join(f"  - {item}" for item in problems),
                problems,
            )
        return self

    @property
    def grid_position_count(self) -> int:
        return self.history_minutes // self.grid_cadence_minutes + 1

    @property
    def max_horizon_minutes(self) -> int:
        return max(self.horizons_minutes)

    def identity_payload(self) -> dict[str, Any]:
        """Fields that participate in episode-dataset identity. `created_at` is excluded."""
        return {
            "history_minutes": self.history_minutes,
            "grid_cadence_minutes": self.grid_cadence_minutes,
            "max_history_gap_minutes": self.max_history_gap_minutes,
            "history_start_tolerance_minutes": self.history_start_tolerance_minutes,
            "target_tolerance_minutes": self.target_tolerance_minutes,
            "horizons_minutes": list(self.horizons_minutes),
            "target_policy": self.target_policy,
            "include_event_context": self.include_event_context,
        }

    def to_json_dict(self) -> dict[str, Any]:
        payload = self.identity_payload()
        payload["grid_position_count"] = self.grid_position_count
        return payload


def target_rejection_code(kind: str, horizon_minutes: int) -> str:
    """kind is MISSING or AMBIGUOUS → MISSING_TARGET_30M / AMBIGUOUS_TARGET_30M."""
    return f"{kind}_TARGET_{horizon_minutes}M"
