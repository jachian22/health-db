"""Errors for the ML extraction layer. Messages never include secrets or raw bodies."""

from __future__ import annotations

from typing import Any


class HealthMLError(Exception):
    """Base error for the ML extraction layer."""


class HealthAPIError(HealthMLError):
    """Mapped Query API / transport failure."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class SnapshotError(HealthMLError):
    """Snapshot build or validation failure."""


class InvalidRangeError(SnapshotError):
    """start/end are missing a timezone or do not form a forward interval."""


class SnapshotExistsError(SnapshotError):
    """Refusing to overwrite an existing snapshot directory."""


class SnapshotValidationError(SnapshotError):
    """Canonical records failed validation. Source data is not repaired."""

    def __init__(self, message: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(message)


class DiagnosticsError(HealthMLError):
    """Snapshot diagnostics failed. Source snapshot files are not modified."""


class DiagnosticsExistsError(DiagnosticsError):
    """Refusing to overwrite an existing diagnostics artifact directory."""


class DiagnosticsValidationError(DiagnosticsError):
    """Diagnostics configuration or snapshot integrity failed."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = problems or []
        super().__init__(message)


class EpisodeError(HealthMLError):
    """Forecast-episode generation failed. Source snapshot and diagnostics are not modified."""


class EpisodeExistsError(EpisodeError):
    """Refusing to overwrite an existing episode-dataset directory."""


class EpisodeValidationError(EpisodeError):
    """Episode configuration or input-artifact integrity failed."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = problems or []
        super().__init__(message)
