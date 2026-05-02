from __future__ import annotations

from pathlib import Path

from pgloom.harness.subprocess import SubprocessResult, run_bounded


def git_status(repo: Path, *, timeout_seconds: float = 30) -> SubprocessResult:
    return run_bounded(["git", "status", "--short"], cwd=repo, timeout_seconds=timeout_seconds)
