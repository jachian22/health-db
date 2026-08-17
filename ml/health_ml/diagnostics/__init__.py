"""Phase 1.5 snapshot diagnostics and forecasting-readiness counting.

Reads an immutable Phase 0/1 snapshot and writes a separate diagnostics artifact.
Does not call the Query API, repair source data, or generate episodes/targets/models.
"""

from __future__ import annotations

from health_ml.diagnostics.runner import DiagnosticsResult, run_diagnostics

__all__ = ["DiagnosticsResult", "run_diagnostics"]
