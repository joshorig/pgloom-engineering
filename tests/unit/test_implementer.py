from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pgloom.harness.subprocess import run_bounded

from pgloom_engineering.contracts import (
    DesignContract,
    PlanContract,
    QAAuthorContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.projects import ProjectConfig
from pgloom_engineering.roles import implementer
from pgloom_engineering.roles.implementer import (
    ImplementerHandler,
    implementation_path_violations,
    normalize_task_result_payload,
)


class ImplementerProvider:
    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.calls = 0

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, kwargs
        self.calls += 1
        payload = json.loads(prompt)
        assert payload["worktree"] == str(self.worktree)
        self.worktree.joinpath("src").mkdir(exist_ok=True)
        self.worktree.joinpath("src/App.java").write_text("class App {}\n", encoding="utf-8")
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "impl-1",
                    "changed_files": ["src/App.java"],
                    "checks": [],
                    "blockers": [],
                }
            ),
            model_usage_id=100 + self.calls,
        )


class RepairingImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            self.worktree.joinpath("tests/test_red.py").write_text(
                "def test_red():\n    assert True\n",
                encoding="utf-8",
            )
            return SimpleNamespace(text='{"bad": true}', model_usage_id=201)
        self.worktree.joinpath("tests/test_red.py").write_text(
            "def test_red():\n    assert False\n",
            encoding="utf-8",
        )
        self.worktree.joinpath("src").mkdir(exist_ok=True)
        self.worktree.joinpath("src/App.java").write_text("class App {}\n", encoding="utf-8")
        return SimpleNamespace(
            text=json.dumps(
                {
                    "TaskResultContract": {
                        "feature_id": "feature-1",
                        "task_id": "impl-1",
                        "changed_files": ["src/App.java"],
                    }
                }
            ),
            model_usage_id=202,
        )


def test_implementer_uses_qa_worktree_and_reports_only_implementation_delta(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    result = ImplementerHandler(provider=ImplementerProvider(worktree)).handle(_task())

    assert result.status == "done"
    contract = result.result["task_result_contract"]
    assert contract["changed_files"] == ["src/App.java"]
    assert contract["model_usage_ids"] == [101]
    assert contract["checks"][0]["status"] == "passed"


def test_implementer_repairs_contract_path_and_verification_failures(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    provider = RepairingImplementerProvider(worktree)
    result = ImplementerHandler(provider=provider).handle(_task())

    assert result.status == "done"
    assert provider.calls == 2
    assert result.result["repair_attempts"] == 1
    assert result.result["task_result_contract"]["changed_files"] == ["src/App.java"]


def test_implementation_path_violations_enforce_allowed_and_forbidden_paths() -> None:
    contract = _implementer_contract()

    assert implementation_path_violations(
        ["src/App.java", "tests/test_red.py", "docs/note.md"],
        contract,
    ) == [
        {"path": "tests/test_red.py", "reason": "forbidden_path"},
        {"path": "docs/note.md", "reason": "outside_allowed_paths"},
    ]


def test_normalize_task_result_payload_accepts_wrappers() -> None:
    payload = {"TaskResultContract": {"feature_id": "feature-1"}}
    assert normalize_task_result_payload(payload) == {"feature_id": "feature-1"}
    payload = {"task_result_contract": {"feature_id": "feature-2"}}
    assert normalize_task_result_payload(payload) == {"feature_id": "feature-2"}


def _patch_live_contracts(monkeypatch: Any, worktree: Path) -> None:
    plan = _plan()
    task_contract = _implementer_contract()
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        tests_added=["tests/test_red.py"],
        matrix_coverage={"criterion": ["tests/test_red.py"]},
        paths_touched=["tests/test_red.py"],
        worktree_path=str(worktree),
        branch="pgloom/feature-1/qa-author/qa-1",
    )

    def fake_get_task_contract(task_id: str, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        if task_id == "impl-1":
            return {"input_contract": task_contract.model_dump(mode="json")}
        if task_id == "qa-1":
            return {
                "input_contract": {},
                "output_contract": {
                    "qa_author_contract": qa_contract.model_dump(mode="json")
                },
            }
        return None

    monkeypatch.setattr(implementer, "get_task_contract", fake_get_task_contract)
    monkeypatch.setattr(
        implementer,
        "get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        implementer,
        "get_project",
        lambda *args, **kwargs: ProjectConfig(name="demo", root=worktree, metadata={}),
    )


def _task() -> dict[str, Any]:
    return {
        "id": "impl-1",
        "workflow_id": "feature-1",
        "task_type": "engineering.implement",
        "payload": {"database_url": None},
    }


def _plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Implement the acceptance criterion.",
        design_contract=DesignContract(acceptance_tests=["criterion"]),
        affected_surfaces=["src/", "tests/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement source.",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
                expected_outputs=["TaskResultContract"],
            )
        ],
        acceptance_test_matrix=["criterion"],
    )


def _implementer_contract() -> TaskContract:
    return TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="implementer",
        task_type="engineering.implement",
        objective="Implement source.",
        inputs={"task_slice_id": "impl"},
        allowed_paths=["src/"],
        forbidden_paths=["tests/"],
        dependencies=["qa-1"],
        expected_outputs=["TaskResultContract"],
        verification_commands=[
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('src/App.java').exists()",
            ]
        ],
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _run(["git", "init", "-b", "main", str(repo)])
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    repo.joinpath("README.md").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


def _run(argv: list[str], *, cwd: Path | None = None) -> None:
    result = run_bounded(argv, cwd=cwd, timeout_seconds=30)
    assert result.exit_code == 0, result.stderr
