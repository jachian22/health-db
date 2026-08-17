"""Resolve git identity from the source checkout, not the process working directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def try_git_sha() -> str | None:
    root = _git_root()
    if root is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    sha = completed.stdout.strip()
    return sha or None


def _git_root() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None
