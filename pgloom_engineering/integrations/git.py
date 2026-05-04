from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pgloom.harness.subprocess import SubprocessResult, run_bounded


class GitCommandError(RuntimeError):
    def __init__(self, result: SubprocessResult) -> None:
        self.result = result
        super().__init__(
            f"git command failed ({result.exit_code}): {' '.join(result.argv)}\n{result.stderr}"
        )


class WorktreeHandle:
    def __init__(self, *, repo: Path, worktree: Path, branch: str) -> None:
        self.repo = repo
        self.worktree = worktree
        self.branch = branch


@dataclass(frozen=True)
class _ExistingWorktree:
    path: Path
    prunable: bool = False


def git_status(repo: Path, *, timeout_seconds: float = 30) -> SubprocessResult:
    return run_bounded(["git", "status", "--short"], cwd=repo, timeout_seconds=timeout_seconds)


def current_head(repo: Path, *, timeout_seconds: float = 30) -> str:
    return _git(
        repo,
        ["rev-parse", "HEAD"],
        timeout_seconds=timeout_seconds,
    ).stdout.strip()


def create_task_worktree(
    *,
    repo: Path,
    worktree_root: Path,
    feature_id: str,
    task_id: str,
    slice_id: str,
    base_ref: str = "HEAD",
    branch_prefix: str = "pgloom",
    timeout_seconds: float = 60,
) -> WorktreeHandle:
    branch = task_branch_name(
        feature_id=feature_id,
        task_id=task_id,
        slice_id=slice_id,
        prefix=branch_prefix,
    )
    worktree = worktree_root / branch.replace("/", "__")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    existing = _worktree_for_branch(repo, branch, timeout_seconds=timeout_seconds)
    if existing is not None:
        if existing.path.resolve() != worktree.resolve():
            raise RuntimeError(
                f"branch {branch!r} is already attached to worktree {existing.path}, "
                f"not expected path {worktree}"
            )
        if existing.prunable or not existing.path.exists():
            _git(repo, ["worktree", "prune"], timeout_seconds=timeout_seconds)
        else:
            return WorktreeHandle(repo=repo, worktree=worktree, branch=branch)
    _git(
        repo,
        ["worktree", "add", "-B", branch, str(worktree), base_ref],
        timeout_seconds=timeout_seconds,
    )
    return WorktreeHandle(repo=repo, worktree=worktree, branch=branch)


def task_branch_name(
    *,
    feature_id: str,
    task_id: str,
    slice_id: str,
    prefix: str = "pgloom",
) -> str:
    return "/".join(
        [
            _slug(prefix),
            _slug(feature_id),
            _slug(slice_id),
            _branch_task_component(task_id),
        ]
    )


def changed_files(repo: Path, *, timeout_seconds: float = 30) -> list[str]:
    result = _git(
        repo,
        ["status", "--porcelain=v1", "-z", "-uall"],
        timeout_seconds=timeout_seconds,
    )
    return _parse_porcelain_z(result.stdout)


def commit_all(
    repo: Path,
    *,
    message: str,
    timeout_seconds: float = 60,
) -> str | None:
    if not changed_files(repo, timeout_seconds=timeout_seconds):
        return None
    _git(repo, ["add", "-A"], timeout_seconds=timeout_seconds)
    _git(repo, ["commit", "-m", message], timeout_seconds=timeout_seconds)
    return current_head(repo, timeout_seconds=timeout_seconds)


def push_branch(
    repo: Path,
    *,
    branch: str,
    remote: str = "origin",
    set_upstream: bool = True,
    timeout_seconds: float = 120,
) -> SubprocessResult:
    argv = ["push"]
    if set_upstream:
        argv.append("-u")
    argv.extend([remote, branch])
    return _git(repo, argv, timeout_seconds=timeout_seconds)


def remove_worktree(
    *,
    repo: Path,
    worktree: Path,
    force: bool = False,
    timeout_seconds: float = 60,
) -> None:
    argv = ["worktree", "remove"]
    if force:
        argv.append("--force")
    argv.append(str(worktree))
    _git(repo, argv, timeout_seconds=timeout_seconds)


def _git(repo: Path, args: list[str], *, timeout_seconds: float) -> SubprocessResult:
    result = run_bounded(["git", *args], cwd=repo, timeout_seconds=timeout_seconds)
    if result.exit_code != 0 or result.timed_out or result.killed:
        raise GitCommandError(result)
    return result


def _worktree_for_branch(
    repo: Path,
    branch: str,
    *,
    timeout_seconds: float,
) -> _ExistingWorktree | None:
    result = _git(repo, ["worktree", "list", "--porcelain"], timeout_seconds=timeout_seconds)
    expected_branch = f"refs/heads/{branch}"
    for block in result.stdout.strip().split("\n\n"):
        path: Path | None = None
        branch_matches = False
        prunable = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line == f"branch {expected_branch}":
                branch_matches = True
            elif line.startswith("prunable"):
                prunable = True
        if branch_matches and path is not None:
            return _ExistingWorktree(path=path, prunable=prunable)
    return None


def _parse_porcelain_z(output: str) -> list[str]:
    paths: list[str] = []
    entries = [entry for entry in output.split("\0") if entry]
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            paths.append(path)
            index += 1
            if index < len(entries):
                paths.append(entries[index])
        else:
            paths.append(path)
        index += 1
    return sorted(dict.fromkeys(paths))


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug.lower() or "item"


def _branch_task_component(task_id: str) -> str:
    slug = _slug(task_id)
    digest = hashlib.sha1(task_id.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:24]}-{digest}"
