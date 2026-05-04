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
    qa_model_route,
    route_model_command,
)
from pgloom_engineering.roles.qa import QAHandler


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


def test_qa_verify_returns_valid_inconclusive_result_contract() -> None:
    result = QAHandler().handle(
        {
            "id": "verify-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.qa.verify",
            "payload": {},
        }
    )

    assert result.status == "done"
    contract = QAResultContract.model_validate(result.result["qa_result_contract"])
    assert contract.feature_id == "feature-1"
    assert contract.task_id == "verify-task-1"
    assert contract.verdict == "inconclusive"


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
