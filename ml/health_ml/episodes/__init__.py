"""Phase 2 deterministic forecast-episode generation.

Reads one immutable snapshot and its matching diagnostics artifact and writes
an immutable episode dataset. Does not call the Query API, train models, or
modify source artifacts.
"""

from __future__ import annotations

from health_ml.episodes.runner import EpisodeResult, run_episodes

__all__ = ["EpisodeResult", "run_episodes"]
