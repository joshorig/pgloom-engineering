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
    QAResultContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.qa_author_runtime import (
    build_qa_author_prompt,
    build_qa_quality_repair_prompt,
    qa_model_route,
    qa_quality_repairable,
    route_model_command,
)
from pgloom_engineering.roles.qa import QAHandler, normalize_qa_result_payload


class FakeProvider:
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del prompt, kwargs
        worktree = Path(profile.command[-1])
        worktree.joinpath("tests").mkdir(exist_ok=True)
        worktree.joinpath("tests/test_acceptance.py").write_text(
            "def test_acceptance():\n    assert False\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "task-1",
                    "tests_added": ["tests/test_acceptance.py::test_acceptance"],
                    "matrix_coverage": {
                        "acceptance criterion": [
                            "tests/test_acceptance.py::test_acceptance",
                        ]
                    },
                    "red_proof": [
                        {
                            "test": "tests/test_acceptance.py::test_acceptance",
                            "command": ["pytest", "tests/test_acceptance.py"],
                            "exit_code": 1,
                            "output_excerpt": "assert False",
                        }
                    ],
                    "paths_touched": ["tests/test_acceptance.py"],
                }
            ),
            model_usage_id=42,
        )


class ScriptStringCheckProvider(FakeProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del prompt, kwargs
        worktree = Path(profile.command[-1])
        worktree.joinpath("tests").mkdir(exist_ok=True)
        worktree.joinpath("tests/BenchmarkWiringTest.java").write_text(
            "\n".join(
                [
                    "class BenchmarkWiringTest {",
                    "  @Test",
                    "  void benchmarkIsWired() throws Exception {",
                    '    String smoke = Files.readString(Path.of("qa/smoke.sh"));',
                    '    assertTrue(smoke.contains("jmhSmokeCheck"));',
                    "  }",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "task-1",
                    "tests_added": ["tests/BenchmarkWiringTest.java"],
                    "matrix_coverage": {"acceptance criterion": ["tests/BenchmarkWiringTest.java"]},
                    "red_proof": [
                        {
                            "test": "tests/BenchmarkWiringTest.java",
                            "command": ["pytest", "tests/test_acceptance.py"],
                            "exit_code": 1,
                            "output_excerpt": "red",
                        }
                    ],
                    "paths_touched": ["tests/BenchmarkWiringTest.java"],
                }
            ),
            model_usage_id=42,
        )


class QualityRepairProvider(ScriptStringCheckProvider):
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return super().invoke(profile=profile, prompt=prompt, **kwargs)
        worktree = Path(profile.command[-1])
        worktree.joinpath("tests/BenchmarkWiringTest.java").unlink(missing_ok=True)
        return FakeProvider().invoke(profile=profile, prompt=prompt, **kwargs)


class GeneratedArtifactProvider(FakeProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        worktree = Path(profile.command[-1])
        worktree.joinpath("playwright-report").mkdir()
        worktree.joinpath("playwright-report/index.html").write_text(
            "<html></html>\n",
            encoding="utf-8",
        )
        worktree.joinpath("ui/test-results/domain-switch").mkdir(parents=True)
        worktree.joinpath("ui/test-results/domain-switch/error-context.md").write_text(
            "debug artifact\n",
            encoding="utf-8",
        )
        return super().invoke(profile=profile, prompt=prompt, **kwargs)


class DependencyAwareProvider(FakeProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        worktree = Path(profile.command[-1])
        assert worktree.joinpath("ui/node_modules").is_symlink()
        return super().invoke(profile=profile, prompt=prompt, **kwargs)


class NoChangeProvider(FakeProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, prompt, kwargs
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "task-1",
                    "tests_added": ["tests/test_acceptance.py::test_acceptance"],
                    "matrix_coverage": {
                        "acceptance criterion": [
                            "tests/test_acceptance.py::test_acceptance",
                        ]
                    },
                    "red_proof": [],
                    "paths_touched": [],
                }
            ),
            model_usage_id=42,
        )


class NoChangeThenRepairProvider(FakeProvider):
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return NoChangeProvider().invoke(profile=profile, prompt=prompt, **kwargs)
        payload = json.loads(prompt)
        assert payload["role"] == "qa.author.no_changes_repair"
        return super().invoke(profile=profile, prompt=prompt, **kwargs)


class UserTestProvider:
    def __init__(
        self,
        *,
        shell_string_commands: bool = False,
        verdict: str = "pass",
        command: list[str] | str | None = None,
        procedures_attestation: dict[str, object] | None = None,
    ) -> None:
        self.shell_string_commands = shell_string_commands
        self.verdict = verdict
        self.command = command
        self.procedures_attestation = procedures_attestation

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, kwargs
        payload = json.loads(prompt)
        assert payload["role"] == "qa.usertest"
        assert payload["worktree"]
        assert payload["role_context"]["contract"] == "engineering.role_context.v1"
        assert payload["role_gate_contract"]["contract_version"] == (
            "engineering.role_gate_contract.v1"
        )
        assert payload["role_gate_contract"]["role"] == "qa.verify.usertest"
        command: list[str] | str
        command = self.command or (
            "python -c 'consumer flow'"
            if self.shell_string_commands
            else [
                "python",
                "-c",
                "consumer flow",
            ]
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "QAResultContract": {
                        "feature_id": payload["task_contract"]["feature_id"],
                        "task_id": payload["task_contract"]["inputs"]["task_id"],
                        "verdict": self.verdict,
                        "validator_type": "usertest",
                        "commands": [command],
                        "commands_run": [
                            {
                                "cmd": command,
                                "exit_code": 0,
                                "duration_s": 0.1,
                            }
                        ],
                        "validation_evidence": [
                            {
                                "evidence_id": "user-flow",
                                "kind": "integration_check",
                                "summary": "Ran a consumer-style public API flow.",
                                "verdict": "pass",
                                "metadata": {"surface": "library"},
                            }
                        ],
                        "evidence": ["consumer flow passed"],
                        "procedures_attestation": self.procedures_attestation or {},
                        "findings": [
                            {
                                "severity": "blocking",
                                "summary": "consumer flow failed",
                                "details": "user-test caught a feature defect",
                            }
                        ]
                        if self.verdict != "pass"
                        else [],
                    }
                }
            ),
            model_usage_id=77,
        )


class CompileErrorProvider(FakeProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del prompt, kwargs
        worktree = Path(profile.command[-1])
        worktree.joinpath("tests").mkdir(exist_ok=True)
        worktree.joinpath("tests/test_acceptance.py").write_text(
            "def test_acceptance(:\n    assert False\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "task-1",
                    "tests_added": ["tests/test_acceptance.py::test_acceptance"],
                    "matrix_coverage": {
                        "acceptance criterion": [
                            "tests/test_acceptance.py::test_acceptance",
                        ]
                    },
                    "red_proof": [],
                    "paths_touched": ["tests/test_acceptance.py"],
                }
            ),
            model_usage_id=42,
        )


class SelfHealingCompileErrorProvider(FakeProvider):
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del prompt, kwargs
        self.calls += 1
        worktree = Path(profile.command[-1])
        worktree.joinpath("tests").mkdir(exist_ok=True)
        if self.calls == 1:
            worktree.joinpath("tests/test_acceptance.py").write_text(
                "def test_acceptance(:\n    assert False\n",
                encoding="utf-8",
            )
            usage_id = 101
        else:
            worktree.joinpath("tests/test_acceptance.py").write_text(
                "def test_acceptance():\n    assert False\n",
                encoding="utf-8",
            )
            usage_id = 102
        return SimpleNamespace(
            text=json.dumps(
                {
                    "feature_id": "feature-1",
                    "task_id": "task-1",
                    "tests_added": ["tests/test_acceptance.py::test_acceptance"],
                    "matrix_coverage": {
                        "acceptance criterion": [
                            "tests/test_acceptance.py::test_acceptance",
                        ]
                    },
                    "red_proof": [],
                    "paths_touched": ["tests/test_acceptance.py"],
                }
            ),
            model_usage_id=usage_id,
        )


def test_qa_author_creates_worktree_and_returns_contract(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=FakeProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    contract = result.result["qa_author_contract"]
    assert contract["tests_added"] == ["tests/test_acceptance.py::test_acceptance"]
    assert contract["red_proof"][0]["source"] == "orchestrator"
    assert contract["red_proof"][0]["command"] == [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_acceptance.py",
        "-q",
    ]
    assert contract["paths_touched"] == ["tests/test_acceptance.py"]
    assert contract["branch"].startswith("pgloom/feature-1/qa-author/")
    assert contract["model_usage_ids"] == [42]


def test_qa_author_runs_all_verification_commands(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "verification_commands": [
                [sys.executable, "-c", "raise SystemExit(0)"],
                [sys.executable, "-m", "pytest", "tests/test_acceptance.py", "-q"],
            ]
        }
    )

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=FakeProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    red_proof = result.result["qa_author_contract"]["red_proof"]
    assert len(red_proof) == 1
    assert red_proof[0]["command"] == [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_acceptance.py",
        "-q",
    ]
    assert red_proof[0]["exit_code"] == 1


def test_qa_author_blocks_non_qa_paths(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()

    class BadProvider(FakeProvider):
        def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
            worktree = Path(profile.command[-1])
            worktree.joinpath("src").mkdir()
            worktree.joinpath("src/app.py").write_text("print('bad')\n", encoding="utf-8")
            return super().invoke(profile=profile, prompt=prompt, **kwargs)

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=BadProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_path_violation"


def test_qa_author_blocks_authored_tests_that_do_not_compile(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=CompileErrorProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_tests_do_not_compile"
    assert result.result["changed_files"] == ["tests/test_acceptance.py"]


def test_qa_author_repairs_authored_tests_that_do_not_compile(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()
    provider = SelfHealingCompileErrorProvider()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=provider).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert provider.calls == 2
    assert result.result["repair_attempts"] == 1
    assert result.result["qa_author_contract"]["model_usage_ids"] == [101, 102]


def test_qa_author_rejects_red_proof_without_relevant_changes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    repo.joinpath("tests").mkdir()
    repo.joinpath("tests/test_acceptance.py").write_text(
        "def test_acceptance():\n    assert False\n",
        encoding="utf-8",
    )
    _run(["git", "add", "tests/test_acceptance.py"], cwd=repo)
    _run(["git", "commit", "-m", "add existing failing test"], cwd=repo)
    plan = _plan()
    task_contract = _task_contract()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=NoChangeProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_no_changes"


def test_qa_author_repairs_red_proof_without_relevant_changes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    provider = NoChangeThenRepairProvider()
    result = QAHandler(provider=provider).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert provider.calls == 2
    assert result.result["repair_attempts"] == 1
    assert result.result["qa_author_contract"]["paths_touched"] == ["tests/test_acceptance.py"]


def test_qa_author_rechecks_path_policy_after_verification(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('src').mkdir(); "
                    "Path('src/app.py').write_text('print(1)\\n')",
                ],
                [sys.executable, "-m", "pytest", "tests/test_acceptance.py", "-q"],
            ]
        }
    )

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=FakeProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_path_violation"
    assert result.result["violations"] == [
        {"path": "src/app.py", "reason": "outside_allowed_paths"}
    ]


def test_qa_author_filters_generated_artifacts_before_path_policy(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )

    result = QAHandler(provider=GeneratedArtifactProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert result.result["qa_author_contract"]["paths_touched"] == ["tests/test_acceptance.py"]


def test_qa_author_hydrates_dependencies_before_invoking_provider(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    repo.joinpath("ui/node_modules").mkdir(parents=True)
    repo.joinpath("ui/node_modules/.keep").write_text("dependency\n", encoding="utf-8")
    plan = _plan()
    task_contract = _task_contract()
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={
                "worktree_root": str(tmp_path / "worktrees"),
                "qa": {"dependency_hydration": ["ui/node_modules"]},
            },
        ),
    )

    result = QAHandler(provider=DependencyAwareProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"


def test_qa_author_resets_task_worktree_on_retry_attempt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract()
    resets: list[tuple[Path, str]] = []

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={"worktree_root": str(tmp_path / "worktrees")},
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.reset_worktree_to_ref",
        lambda *, worktree, base_ref: resets.append((worktree, base_ref)),
    )

    result = QAHandler(provider=FakeProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "attempt": 2,
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert resets and resets[0][1] == "main"


def test_qa_verify_blocks_without_task_contract(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: None,
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.task_contract_missing"


def test_qa_verify_runs_configured_commands(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    authored_worktree.joinpath("marker.txt").write_text("authored\n", encoding="utf-8")
    plan = _plan()
    qa_author_output = {
        "qa_author_contract": {
            "feature_id": "feature-1",
            "task_id": "qa-author-task-1",
            "worktree_path": str(authored_worktree),
        }
    }
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "dependencies": ["qa-author-task-1"],
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('marker.txt').exists(); print('verify ok')",
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )

    def get_contract(task_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        del args, kwargs
        if task_id == "verify-task-1":
            return {"input_contract": task_contract.model_dump(mode="json")}
        if task_id == "qa-author-task-1":
            return {"output_contract": qa_author_output}
        return None

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        get_contract,
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "done"
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.feature_id == "feature-1"
    assert contract.task_id == "verify-task-1"
    assert contract.verdict == "pass"
    assert "verify ok" in contract.evidence[0]


def test_qa_verify_failed_command_records_finding(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    qa_author_output = {
        "qa_author_contract": {
            "feature_id": "feature-1",
            "task_id": "qa-author-task-1",
            "worktree_path": str(authored_worktree),
        }
    }
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "dependencies": ["qa-author-task-1"],
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "import sys; print('allocation gate failed', file=sys.stderr); sys.exit(7)",
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )

    def get_contract(task_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        del args, kwargs
        if task_id == "verify-task-1":
            return {"input_contract": task_contract.model_dump(mode="json")}
        if task_id == "qa-author-task-1":
            return {"output_contract": qa_author_output}
        return None

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        get_contract,
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_verify_failed"
    assert "allocation gate failed" in result.blocker_reason
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.verdict == "fail"
    assert "allocation gate failed" in contract.findings[0]


def test_qa_verify_failure_reason_preserves_stdout_diagnostics(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "handoff-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    qa_author_output = {
        "qa_author_contract": {
            "feature_id": "feature-1",
            "task_id": "qa-author-task-1",
            "worktree_path": str(authored_worktree),
        }
    }
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "dependencies": ["qa-author-task-1"],
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "print('JMH smoke GC gate failed:'); "
                        "print('- rangeScanSmoke allocated 0.008 B/op, above "
                        "threshold 0.005 B/op'); "
                        "print('BUILD FAILED', file=sys.stderr); "
                        "sys.exit(1)"
                    ),
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )

    def get_contract(task_id: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        del args, kwargs
        if task_id == "verify-task-1":
            return {"input_contract": task_contract.model_dump(mode="json")}
        if task_id == "qa-author-task-1":
            return {"output_contract": qa_author_output}
        return None

    monkeypatch.setattr("pgloom_engineering.roles.qa.get_task_contract", get_contract)
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_verify_failed"
    assert result.blocker_reason is not None
    assert "rangeScanSmoke allocated 0.008 B/op" in result.blocker_reason
    assert "BUILD FAILED" in result.blocker_reason


def test_qa_verify_uses_handoff_worktree(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    stale_worktree = tmp_path / "stale-worktree"
    stale_worktree.mkdir()
    authored_worktree = tmp_path / "handoff-worktree"
    authored_worktree.mkdir()
    authored_worktree.joinpath("marker.txt").write_text("authored\n", encoding="utf-8")
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('marker.txt').read_text() == 'authored\\n'",
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [
            {
                "contract": {
                    "qa_author_contract": {
                        "feature_id": "feature-1",
                        "task_id": "qa-author-task-0",
                        "worktree_path": str(stale_worktree),
                    }
                }
            },
            {
                "contract": {
                    "qa_author_contract": {
                        "feature_id": "feature-1",
                        "task_id": "qa-author-task-1",
                        "worktree_path": str(authored_worktree),
                    }
                }
            }
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "done"


def test_qa_usertest_uses_model_driven_user_flow(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.usertest",
            "expected_outputs": ["QAResultContract"],
            "inputs": {"task_id": "verify-task-1", "task_slice_id": "qa-usertest"},
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
            qa_validation_profile="qa-validation",
            qa_validation_command=["fake-qa-validation", "{worktree}"],
            qa_validation_codex_model="gpt-5.4",
            qa_validation_codex_reasoning="medium",
            qa_validation_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [
            {"contract": {"qa_author_contract": {"worktree_path": str(authored_worktree)}}}
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            name="demo",
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.build_role_context",
        lambda **kwargs: SimpleNamespace(
            prompt_payload=lambda: {
                "contract": "engineering.role_context.v1",
                "role": "qa.usertest",
            }
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.record_role_context_usage",
        lambda *args, **kwargs: 88,
    )
    registered: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.register_artifact",
        lambda **kwargs: registered.append(kwargs) or {"id": "artifact-usertest"},
    )

    result = QAHandler(provider=UserTestProvider()).handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.usertest",
            "payload": {},
        }
    )

    assert result.status == "done"
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.validator_type == "usertest"
    assert contract.validation_evidence[0]["metadata"]["surface"] == "library"
    assert contract.validation_evidence[0]["artifact_ids"] == ["artifact-usertest"]
    assert contract.commands_run[0]["artifact_ids"] == ["artifact-usertest"]
    assert registered[0]["artifact_type"] == "qa-usertest-transcript"
    assert json.loads(registered[0]["content"])["model_usage_id"] == 77
    assert result.result["token_savior_usage_ids"] == [88]


def test_qa_usertest_accepts_shell_string_commands(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.usertest",
            "expected_outputs": ["QAResultContract"],
            "inputs": {"task_id": "verify-task-1", "task_slice_id": "qa-usertest"},
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
            qa_validation_profile="qa-validation",
            qa_validation_command=["fake-qa-validation", "{worktree}"],
            qa_validation_codex_model="gpt-5.4",
            qa_validation_codex_reasoning="medium",
            qa_validation_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [
            {"contract": {"qa_author_contract": {"worktree_path": str(authored_worktree)}}}
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            name="demo",
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.build_role_context",
        lambda **kwargs: SimpleNamespace(
            prompt_payload=lambda: {
                "contract": "engineering.role_context.v1",
                "role": "qa.usertest",
            }
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.record_role_context_usage",
        lambda *args, **kwargs: 88,
    )

    result = QAHandler(provider=UserTestProvider(shell_string_commands=True)).handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.usertest",
            "payload": {},
        }
    )

    assert result.status == "done"
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.commands == [["python", "-c", "consumer flow"]]
    assert contract.commands_run[0]["cmd"] == ["python", "-c", "consumer flow"]


def test_qa_usertest_normalizes_structured_procedure_attestation() -> None:
    normalized = normalize_qa_result_payload(
        {
            "QAResultContract": {
                "feature_id": "feature-1",
                "task_id": "verify-task-1",
                "verdict": "pass",
                "validator_type": "usertest",
                "procedures_attestation": {
                    "record-replay-evidence": {
                        "status": "completed",
                        "notes": "captured command artifacts",
                    }
                },
            }
        }
    )

    contract = QAResultContract.model_validate(normalized)
    assert (
        contract.procedures_attestation["record-replay-evidence"]
        == "completed - captured command artifacts"
    )


def test_qa_usertest_blocks_broad_project_test_command(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.usertest",
            "expected_outputs": ["QAResultContract"],
            "inputs": {"task_id": "verify-task-1", "task_slice_id": "qa-usertest"},
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
            qa_validation_profile="qa-validation",
            qa_validation_command=["fake-qa-validation", "{worktree}"],
            qa_validation_codex_model="gpt-5.4",
            qa_validation_codex_reasoning="medium",
            qa_validation_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [
            {"contract": {"qa_author_contract": {"worktree_path": str(authored_worktree)}}}
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            name="demo",
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.build_role_context",
        lambda **kwargs: SimpleNamespace(
            prompt_payload=lambda: {
                "contract": "engineering.role_context.v1",
                "role": "qa.usertest",
            }
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.record_role_context_usage",
        lambda *args, **kwargs: 88,
    )

    result = QAHandler(
        provider=UserTestProvider(command=["./gradlew", "--no-daemon", "test"])
    ).handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.usertest",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_usertest_failed"
    assert "must not substitute broad project test/check commands" in str(
        result.blocker_reason
    )


def test_qa_usertest_fail_verdict_blocks_as_validation_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.usertest",
            "expected_outputs": ["QAResultContract"],
            "inputs": {"task_id": "verify-task-1", "task_slice_id": "qa-usertest"},
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
            qa_validation_profile="qa-validation",
            qa_validation_command=["fake-qa-validation", "{worktree}"],
            qa_validation_codex_model="gpt-5.4",
            qa_validation_codex_reasoning="medium",
            qa_validation_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [
            {"contract": {"qa_author_contract": {"worktree_path": str(authored_worktree)}}}
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            name="demo",
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.build_role_context",
        lambda **kwargs: SimpleNamespace(
            prompt_payload=lambda: {
                "contract": "engineering.role_context.v1",
                "role": "qa.usertest",
            }
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.record_role_context_usage",
        lambda *args, **kwargs: 88,
    )

    result = QAHandler(provider=UserTestProvider(verdict="fail")).handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.usertest",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_usertest_failed"
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.verdict == "fail"
    assert "consumer flow failed" in contract.findings[0]


def test_qa_verify_finds_feature_qa_author_worktree_after_reviewer_dependency(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    authored_worktree = tmp_path / "authored-worktree"
    authored_worktree.mkdir()
    authored_worktree.joinpath("marker.txt").write_text("authored\n", encoding="utf-8")
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "dependencies": ["review-task-1"],
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('marker.txt').read_text() == 'authored\\n'",
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr("pgloom_engineering.roles.qa.list_task_handoffs", lambda *a, **k: [])
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_contracts",
        lambda *args, **kwargs: [
            {
                "output_contract": {
                    "qa_author_contract": {
                        "feature_id": "feature-1",
                        "task_id": "qa-author-task-1",
                        "worktree_path": str(authored_worktree),
                    }
                }
            }
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(root=repo, base_branch="main", metadata={}),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "done"


def test_qa_verify_prefers_latest_feature_worktree_after_corrective_recovery(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _git_repo(tmp_path)
    stale_worktree = tmp_path / "stale-worktree"
    stale_worktree.mkdir()
    stale_worktree.joinpath("marker.txt").write_text("stale\n", encoding="utf-8")
    repaired_worktree = tmp_path / "repaired-worktree"
    repaired_worktree.mkdir()
    repaired_worktree.joinpath("marker.txt").write_text("repaired\n", encoding="utf-8")
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "dependencies": ["review-task-1"],
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('marker.txt').read_text() == 'repaired\\n'",
                ]
            ],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr("pgloom_engineering.roles.qa.list_task_handoffs", lambda *a, **k: [])
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_contracts",
        lambda *args, **kwargs: [
            {
                "output_contract": {
                    "qa_author_contract": {
                        "feature_id": "feature-1",
                        "task_id": "qa-author-task-1",
                        "worktree_path": str(stale_worktree),
                    }
                }
            },
            {
                "output_contract": {
                    "worktree_path": str(repaired_worktree),
                    "task_id": "implementer-task-2",
                    "changed_files": ["core/src/main/java/Example.java"],
                }
            },
        ],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(root=repo, base_branch="main", metadata={}),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "done"


def test_legacy_qa_task_type_is_blocked() -> None:
    result = QAHandler().handle(
        {
            "id": "qa-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_deprecated_task_type"


def test_qa_verify_blocks_without_authored_worktree(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _git_repo(tmp_path)
    plan = _plan()
    task_contract = _task_contract().model_copy(
        update={
            "task_type": "engineering.qa.verify.scrutiny",
            "expected_outputs": ["QAResultContract"],
            "verification_commands": [[sys.executable, "-c", "print('verify ok')"]],
        }
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={},
        ),
    )

    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify.scrutiny",
            "payload": {},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_verify_worktree_missing"


def test_qa_author_blocks_script_string_assertions_when_gate_validation_required(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    repo.joinpath("qa").mkdir()
    repo.joinpath("qa/smoke.sh").write_text(
        "./gradlew :benchmarks:jmhSmokeCheck\n"
        "grep gc.alloc.rate.norm build/reports/jmh/results.txt\n",
        encoding="utf-8",
    )
    repo.joinpath("qa/regression.sh").write_text(
        "./gradlew test :benchmarks:jmh\n",
        encoding="utf-8",
    )
    _run(["git", "add", "qa/smoke.sh", "qa/regression.sh"], cwd=repo)
    _run(["git", "commit", "-m", "add qa gates"], cwd=repo)
    plan = _plan()
    task_contract = _task_contract()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={
                "worktree_root": str(tmp_path / "worktrees"),
                "qa": {
                    "required_gates": [
                        {
                            "id": "smoke",
                            "command": ["./qa/smoke.sh"],
                            "must_cover": ["allocation", "benchmark_smoke"],
                        },
                        {
                            "id": "regression",
                            "command": ["./qa/regression.sh"],
                            "must_cover": ["benchmark_full", "unit_regression"],
                        },
                    ],
                    "semantic_conventions": {
                        "build_hook_tests": {
                            "allow_build_file_string_assertions": False,
                            "deterministic_gate_validation_required": True,
                        }
                    },
                },
            },
        ),
    )

    result = QAHandler(provider=ScriptStringCheckProvider()).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.qa_semantic_quality_failed"
    assert result.result["findings"][0]["code"] == "qa_semantic_build_file_string_assertion"
    assert result.result["gate_validation"][0]["status"] == "configured"


def test_qa_author_repairs_semantic_quality_findings(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _git_repo(tmp_path)
    repo.joinpath("qa").mkdir()
    repo.joinpath("qa/smoke.sh").write_text(
        "./gradlew :benchmarks:jmhSmokeCheck\n"
        "grep gc.alloc.rate.norm build/reports/jmh/results.txt\n",
        encoding="utf-8",
    )
    repo.joinpath("qa/regression.sh").write_text(
        "./gradlew test :benchmarks:jmh\n",
        encoding="utf-8",
    )
    _run(["git", "add", "qa/smoke.sh", "qa/regression.sh"], cwd=repo)
    _run(["git", "commit", "-m", "add qa gates"], cwd=repo)
    plan = _plan()
    task_contract = _task_contract()
    provider = QualityRepairProvider()

    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_settings",
        lambda: SimpleNamespace(
            qa_worktree_root=tmp_path / "worktrees",
            qa_author_profile="qa-author",
            qa_author_command=["fake-qa", "{worktree}"],
            qa_author_invocation_timeout_seconds=30.0,
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="low",
            qa_author_claude_model="haiku",
        ),
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.list_task_handoffs",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.qa.get_project",
        lambda *args, **kwargs: SimpleNamespace(
            root=repo,
            base_branch="main",
            metadata={
                "worktree_root": str(tmp_path / "worktrees"),
                "qa": {
                    "required_gates": [
                        {
                            "id": "smoke",
                            "command": ["./qa/smoke.sh"],
                            "must_cover": ["allocation", "benchmark_smoke"],
                        },
                        {
                            "id": "regression",
                            "command": ["./qa/regression.sh"],
                            "must_cover": ["benchmark_full", "unit_regression"],
                        },
                    ],
                    "semantic_conventions": {
                        "build_hook_tests": {
                            "allow_build_file_string_assertions": False,
                            "deterministic_gate_validation_required": True,
                        }
                    },
                },
            },
        ),
    )

    result = QAHandler(provider=provider).handle(
        {
            "id": "task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.author",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert provider.calls == 2
    assert result.result["quality_repair_attempts"] == 1


def test_qa_quality_repairable_accepts_reflective_jmh_finding() -> None:
    assert qa_quality_repairable(
        {
            "blocking_findings": [
                {
                    "code": "qa_semantic_jmh_reflective_invocation",
                    "file": "benchmarks/src/jmh/java/com/example/RangeBenchmark.java",
                }
            ]
        }
    )


def test_qa_quality_repairable_accepts_range_prefix_coverage_finding() -> None:
    assert qa_quality_repairable(
        {
            "blocking_findings": [
                {
                    "code": "qa_semantic_range_prefix_behavior_missing",
                    "file": "conformance-tests/src/test/java/RangeScanConformanceTest.java",
                }
            ]
        }
    )


def test_qa_quality_repairable_accepts_java_line_length_finding() -> None:
    assert qa_quality_repairable(
        {
            "blocking_findings": [
                {
                    "code": "qa_semantic_java_line_too_long",
                    "file": "core/src/test/java/com/example/RangeScanApiTest.java",
                    "line": 23,
                },
                {
                    "code": "qa_semantic_range_benchmark_parameterized_gate_mismatch",
                    "file": "benchmarks/build.gradle",
                    "line": 134,
                },
                {
                    "code": "qa_semantic_nonportable_generated_worktree_path",
                    "file": "qa/fixtures/run-range-scan-user-journey.sh",
                    "line": 4,
                },
                {
                    "code": "qa_semantic_usertest_fixture_observes_without_asserting",
                    "file": (
                        "conformance-tests/src/test/java/com/example/"
                        "RangeScanUsertestMain.java"
                    ),
                    "line": 12,
                },
                {
                    "code": "qa_semantic_range_prefix_no_seeded_match",
                    "file": "core/src/test/java/com/example/RangeScanApiTest.java",
                    "line": 8,
                },
            ]
        }
    )


def test_qa_quality_repair_prompt_explains_prefix_seed_repair(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    test_file = worktree / "core/src/test/java/com/example/RangeScanApiTest.java"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class RangeScanApiTest {\n"
        "  static final int PREFIX_VALUE = 1;\n"
        "  static final int PREFIX_BITS = 4;\n"
        "}\n",
        encoding="utf-8",
    )

    prompt = build_qa_quality_repair_prompt(
        plan=_plan(),
        task_contract=_task_contract(),
        worktree=worktree,
        changed_files=[str(test_file.relative_to(worktree))],
        quality_review={
            "blocking_findings": [
                {
                    "code": "qa_semantic_range_prefix_no_seeded_match",
                    "file": str(test_file.relative_to(worktree)),
                    "line": 3,
                    "from_key": 0,
                    "to_key": 15,
                    "prefix_value": 1,
                    "prefix_bits": 4,
                    "written_keys": [1, 2, 3, 12],
                }
            ]
        },
        current_contract={},
        project_metadata={},
    )

    payload = json.loads(prompt)
    instructions = "\n".join(payload["instructions"])
    assert "qa_semantic_range_prefix_no_seeded_match" in instructions
    assert "(writtenKey >> PREFIX_BITS) == PREFIX_VALUE" in instructions
    assert payload["repair_files"] == [str(test_file.relative_to(worktree))]


def test_qa_author_prompt_includes_project_qa_metadata() -> None:
    prompt = build_qa_author_prompt(
        _plan(),
        _task_contract(),
        project_root=Path("."),
        project_metadata={
            "qa": {
                "test_roots": ["app-api/src/test/java", "ui/tests/e2e"],
                "source_roots": ["app-api/src/main/java", "ui/src"],
                "example_tests": ["app-api/src/test/java/ExistingEndpointTest.java"],
                "quality_gates": ["Use endpoint-layer tests for API routes."],
            },
            "qa_author": {
                "avoid_patterns": ["service-only controller coverage"],
            },
        },
    )
    payload = json.loads(prompt)

    assert payload["project_qa_metadata"] == {
        "avoid_patterns": ["service-only controller coverage"],
        "example_tests": ["app-api/src/test/java/ExistingEndpointTest.java"],
        "quality_gates": ["Use endpoint-layer tests for API routes."],
        "source_roots": ["app-api/src/main/java", "ui/src"],
        "test_roots": ["app-api/src/test/java", "ui/tests/e2e"],
    }
    assert "qa_context_capsule" in payload


def test_qa_author_model_routing_updates_codex_command() -> None:
    command = route_model_command(
        ["codex", "exec", "-m", "gpt-5.5", "-c", 'model_reasoning_effort="medium"'],
        claude_model="haiku",
        codex_model="gpt-5.4",
        codex_reasoning="low",
    )

    assert command == [
        "codex",
        "exec",
        "-m",
        "gpt-5.4",
        "-c",
        'model_reasoning_effort="low"',
    ]


def test_qa_author_model_route_uses_project_metadata_default() -> None:
    route = qa_model_route(
        {
            "qa": {
                "model_routing": {
                    "default": {
                        "model": "gpt-5.4",
                        "reasoning": "medium",
                    }
                }
            }
        },
        SimpleNamespace(
            qa_author_claude_model="haiku",
            qa_author_codex_model="gpt-5.5",
            qa_author_codex_reasoning="low",
        ),
    )

    assert route == {
        "claude_model": "haiku",
        "codex_model": "gpt-5.4",
        "codex_reasoning": "medium",
    }


def test_qa_author_model_route_uses_escalation_tier() -> None:
    route = qa_model_route(
        {
            "qa": {
                "model_routing": {
                    "default": {
                        "model": "gpt-5.4",
                        "reasoning": "medium",
                    },
                    "escalate": {
                        "model": "gpt-5.5",
                        "reasoning": "high",
                        "claude_model": "sonnet",
                    },
                }
            }
        },
        SimpleNamespace(
            qa_author_claude_model="haiku",
            qa_author_codex_model="gpt-5.4",
            qa_author_codex_reasoning="medium",
        ),
        tier="escalate",
    )

    assert route == {
        "claude_model": "sonnet",
        "codex_model": "gpt-5.5",
        "codex_reasoning": "high",
    }


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _run(["git", "init", "-b", "main", str(repo)])
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    repo.joinpath("README.md").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


def _plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Add acceptance coverage.",
        design_contract=DesignContract(acceptance_tests=["acceptance criterion"]),
        affected_surfaces=["src/", "tests/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write failing acceptance tests.",
                allowed_paths=["tests/"],
                forbidden_paths=["src/"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[
                    [sys.executable, "-m", "pytest", "tests/test_acceptance.py", "-q"]
                ],
            )
        ],
        acceptance_test_matrix=["acceptance criterion"],
    )


def _task_contract() -> TaskContract:
    return TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="qa",
        task_type="engineering.qa.author",
        objective="Write failing acceptance tests.",
        inputs={"task_slice_id": "qa-author"},
        allowed_paths=["tests/"],
        forbidden_paths=["src/"],
        expected_outputs=["QAAuthorContract"],
        verification_commands=[[sys.executable, "-m", "pytest", "tests/test_acceptance.py", "-q"]],
    )


def _run(argv: list[str], *, cwd: Path | None = None) -> None:
    result = run_bounded(argv, cwd=cwd, timeout_seconds=30)
    assert result.exit_code == 0, result.stderr
