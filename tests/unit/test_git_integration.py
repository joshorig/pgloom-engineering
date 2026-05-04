from __future__ import annotations

from pathlib import Path

from pgloom.harness.subprocess import run_bounded

from pgloom_engineering.integrations.git import (
    changed_files,
    commit_all,
    create_task_worktree,
    push_branch,
    task_branch_name,
)


def test_task_branch_name_is_stable_and_safe() -> None:
    assert task_branch_name(feature_id="Feature 1", task_id="task/abc", slice_id="qa author") == (
        "pgloom/feature-1/qa-author/task-abc"
    )


def test_worktree_change_commit_and_push_round_trip(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    _run(["git", "init", "--bare", str(origin)])
    _run(["git", "init", "-b", "main", str(repo)])
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    repo.joinpath("README.md").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)
    _run(["git", "remote", "add", "origin", str(origin)], cwd=repo)
    _run(["git", "push", "-u", "origin", "main"], cwd=repo)

    handle = create_task_worktree(
        repo=repo,
        worktree_root=worktrees,
        feature_id="feature-1",
        task_id="task-1",
        slice_id="qa-author",
        base_ref="main",
    )
    _run(["git", "config", "user.email", "test@example.com"], cwd=handle.worktree)
    _run(["git", "config", "user.name", "Test User"], cwd=handle.worktree)
    handle.worktree.joinpath("tests").mkdir()
    handle.worktree.joinpath("tests/test_new.py").write_text("def test_new():\n    assert True\n")

    assert changed_files(handle.worktree) == ["tests/test_new.py"]
    commit = commit_all(handle.worktree, message="add qa test")
    assert commit is not None
    pushed = push_branch(handle.worktree, branch=handle.branch)
    assert pushed.exit_code == 0


def test_create_task_worktree_reuses_existing_task_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    _run(["git", "init", "-b", "main", str(repo)])
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    repo.joinpath("README.md").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)

    first = create_task_worktree(
        repo=repo,
        worktree_root=worktrees,
        feature_id="feature-1",
        task_id="task-1",
        slice_id="qa-author",
        base_ref="main",
    )
    second = create_task_worktree(
        repo=repo,
        worktree_root=worktrees,
        feature_id="feature-1",
        task_id="task-1",
        slice_id="qa-author",
        base_ref="main",
    )

    assert second.branch == first.branch
    assert second.worktree == first.worktree


def _run(argv: list[str], *, cwd: Path | None = None) -> None:
    result = run_bounded(argv, cwd=cwd, timeout_seconds=30)
    assert result.exit_code == 0, result.stderr
