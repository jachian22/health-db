"""Git identity must come from the source checkout, not the process cwd."""

from __future__ import annotations

from pathlib import Path

from health_ml.git import try_git_sha


def test_try_git_sha_is_independent_of_cwd(tmp_path: Path, monkeypatch):
    first = try_git_sha()
    monkeypatch.chdir(tmp_path)
    second = try_git_sha()
    assert first == second
    if first is not None:
        assert len(first) == 40
