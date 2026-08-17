"""ML extraction layer for health-db.

Reads the existing Query API and writes immutable Parquet snapshots.
Does not train models or mutate source health data.
"""

from __future__ import annotations

__version__ = "0.1.0"
SCHEMA_VERSION = "0.1"
