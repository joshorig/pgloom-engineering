from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pgloom.harness.subprocess import run_bounded

from pgloom_engineering.config import EngineeringSettings
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
    _dirty_forbidden_path_violations,
    build_implementer_context_capsule,
    build_implementer_prompt,
    build_implementer_repair_prompt,
    build_implementer_source_starter_pack,
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


class CommandCapturingImplementerProvider(ImplementerProvider):
    def __init__(self, worktree: Path) -> None:
        super().__init__(worktree)
        self.command: list[str] = []

    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.command = list(profile.command)
        return super().invoke(profile=profile, prompt=prompt, **kwargs)


class RepairingImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(text='{"bad": true}', model_usage_id=201)
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


class StaleBlockerImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        payload = json.loads(prompt)
        if self.calls == 1:
            self.worktree.joinpath("src").mkdir(exist_ok=True)
            self.worktree.joinpath("src/App.java").write_text(
                "class App {}\n",
                encoding="utf-8",
            )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "TaskResultContract": {
                            "feature_id": "feature-1",
                            "task_id": "impl-1",
                            "changed_files": ["src/App.java"],
                            "blockers": ["Gradle daemon could not bind before retry"],
                        }
                    }
                ),
                model_usage_id=301,
            )
        assert "TaskResultContract.blockers must be empty" in payload["contract_error"]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "TaskResultContract": {
                        "feature_id": "feature-1",
                        "task_id": "impl-1",
                        "changed_files": ["src/App.java"],
                        "blockers": [],
                    }
                }
            ),
            model_usage_id=302,
        )


class PersistentStaleBlockerImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, prompt, kwargs
        self.calls += 1
        self.worktree.joinpath("src").mkdir(exist_ok=True)
        self.worktree.joinpath("src/App.java").write_text("class App {}\n", encoding="utf-8")
        return SimpleNamespace(
            text=json.dumps(
                {
                    "TaskResultContract": {
                        "feature_id": "feature-1",
                        "task_id": "impl-1",
                        "changed_files": ["src/App.java"],
                        "blockers": ["stale command failure still reported"],
                    }
                }
            ),
            model_usage_id=400 + self.calls,
        )


class StructuredEvidenceImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, prompt, kwargs
        self.calls += 1
        self.worktree.joinpath("src").mkdir(exist_ok=True)
        self.worktree.joinpath("src/App.java").write_text("class App {}\n", encoding="utf-8")
        return SimpleNamespace(
            text=json.dumps(
                {
                    "TaskResultContract": {
                        "feature_id": "feature-1",
                        "task_id": "impl-1",
                        "changed_files": ["src/App.java"],
                        "deviations": [
                            {
                                "type": "verification_note",
                                "message": "kept generated QA test unchanged",
                            }
                        ],
                        "blockers": [
                            {
                                "type": "stale_tool_failure",
                                "message": "initial command output was stale",
                            }
                        ],
                    }
                }
            ),
            model_usage_id=500 + self.calls,
        )


class QaPathMutatingImplementerProvider(ImplementerProvider):
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, prompt, kwargs
        self.calls += 1
        self.worktree.joinpath("tests/test_red.py").write_text(
            "def test_red():\n    assert True\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "TaskResultContract": {
                        "feature_id": "feature-1",
                        "task_id": "impl-1",
                        "changed_files": ["tests/test_red.py"],
                    }
                }
            ),
            model_usage_id=600 + self.calls,
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
    assert contract["commands_run"][0]["cmd"][0] == sys.executable
    assert contract["commands_run"][0]["exit_code"] == 0


def test_implementer_blocks_and_restores_qa_owned_path_before_verification(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    result = ImplementerHandler(provider=QaPathMutatingImplementerProvider(worktree)).handle(
        _task()
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.implementation_path_violation"
    assert result.result["violations"] == [
        {"path": "tests/test_red.py", "reason": "forbidden_path"}
    ]
    assert "assert True" in result.result["violation_diffs"]["tests/test_red.py"][
        "diff_excerpt"
    ]
    assert worktree.joinpath("tests/test_red.py").read_text(encoding="utf-8") == (
        "def test_red():\n    assert False\n"
    )


def test_implementer_context_isolation_skips_eager_add_dir_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )
    context_root = tmp_path / "context-root"
    provider = CommandCapturingImplementerProvider(worktree)

    _patch_live_contracts(monkeypatch, worktree)
    monkeypatch.setattr(
        implementer,
        "get_settings",
        lambda: EngineeringSettings(
            implementer_command=[
                "codex",
                "exec",
                "-C",
                str(worktree),
                "--json",
                "-",
            ],
            implementer_model_context_isolation_enabled=True,
            role_model_context_root=context_root,
        ),
    )

    result = ImplementerHandler(provider=provider).handle(_task())

    assert result.status == "done"
    assert provider.command[provider.command.index("-C") + 1] == str(
        context_root.resolve()
    )
    assert "--add-dir" not in provider.command


def test_implementer_blocks_preexisting_forbidden_dirty_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )
    worktree.joinpath("tests/leak.py").write_text(
        "def test_leak():\n    assert True\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    result = ImplementerHandler(provider=ImplementerProvider(worktree)).handle(_task())

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.implementation_path_violation"
    assert result.result["violations"] == [
        {
            "path": "tests/leak.py",
            "reason": "preexisting_forbidden_dirty_path",
        }
    ]


def test_dirty_scope_path_violations_allow_unchanged_prior_variant_files() -> None:
    task_contract = _implementer_contract().model_copy(
        update={
            "objective": "Implement zero-allocation SINGLE-store ascending scans.",
            "expected_outputs": ["SINGLE-store range scan implementation"],
            "allowed_paths": ["store/src/main/java/"],
            "forbidden_paths": ["tests/"],
        }
    )
    qa_contract = QAAuthorContract(feature_id="feature-1", task_id="qa-1")

    violations = implementer._dirty_scope_path_violations(  # noqa: SLF001
        [
            "store/src/main/java/com/example/SingleStore.java",
            "store/src/main/java/com/example/DoubleStore.java",
        ],
        touched=["store/src/main/java/com/example/SingleStore.java"],
        task_contract=task_contract,
        qa_contract=qa_contract,
    )

    assert violations == []


def test_preexisting_forbidden_check_exempts_qa_authored_paths() -> None:
    task_contract = _implementer_contract()
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        paths_touched=[
            "benchmarks/build.gradle",
            "conformance-tests/src/test/java/com/example/RangeScanTest.java",
        ],
    )
    task_contract = task_contract.model_copy(
        update={
            "forbidden_paths": [
                "benchmarks/build.gradle",
                "conformance-tests/src/test/java/",
                "core/src/test/java/",
            ]
        }
    )

    assert (
        _dirty_forbidden_path_violations(
            [
                "benchmarks/build.gradle",
                "conformance-tests/src/test/java/com/example/RangeScanTest.java",
                "core/src/test/java/com/example/LeakTest.java",
            ],
            touched=[],
            task_contract=task_contract,
            qa_contract=qa_contract,
        )
        == [
            {
                "path": "core/src/test/java/com/example/LeakTest.java",
                "reason": "preexisting_forbidden_dirty_path",
            }
        ]
    )


def test_implementer_repairs_contract_path_and_verification_failures(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("PGLOOM_ENGINEERING_IMPLEMENTER_INLINE_REPAIR_ATTEMPTS", "1")
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


def test_implementer_repairs_stale_reported_blockers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    provider = StaleBlockerImplementerProvider(worktree)
    result = ImplementerHandler(provider=provider).handle(_task())

    assert result.status == "done"
    assert provider.calls == 1
    assert result.result["repair_attempts"] == 0
    contract = result.result["task_result_contract"]
    assert contract["blockers"] == []
    assert contract["commands_run"][0]["exit_code"] == 0


def test_implementer_clears_persistent_reported_blockers_when_verification_passes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    provider = PersistentStaleBlockerImplementerProvider(worktree)
    result = ImplementerHandler(provider=provider).handle(_task())

    assert result.status == "done"
    assert provider.calls == 1
    contract = result.result["task_result_contract"]
    assert contract["blockers"] == []
    assert contract["commands_run"][0]["exit_code"] == 0
    assert contract["deviations"] == [
        (
            "reported_blocker_cleared_by_orchestrator_verification: "
            "stale command failure still reported"
        )
    ]


def test_implementer_normalizes_structured_blockers_and_deviations(
    tmp_path: Path, monkeypatch: Any
) -> None:
    worktree = _git_repo(tmp_path)
    worktree.joinpath("tests").mkdir()
    worktree.joinpath("tests/test_red.py").write_text(
        "def test_red():\n    assert False\n",
        encoding="utf-8",
    )

    _patch_live_contracts(monkeypatch, worktree)
    provider = StructuredEvidenceImplementerProvider(worktree)
    result = ImplementerHandler(provider=provider).handle(_task())

    assert result.status == "done"
    assert provider.calls == 1
    contract = result.result["task_result_contract"]
    assert contract["blockers"] == []
    assert contract["deviations"] == [
        '{"message":"kept generated QA test unchanged","type":"verification_note"}',
        (
            "reported_blocker_cleared_by_orchestrator_verification: "
            '{"message":"initial command output was stale","type":"stale_tool_failure"}'
        ),
    ]


def test_implementer_verification_reason_includes_jmh_smoke_diagnostics(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "repo"
    worktree.joinpath("benchmarks/build").mkdir(parents=True)
    worktree.joinpath("benchmarks/build/jmh.txt").write_text(
        "\n".join(
            [
                "StoreRangeScanBenchmark.ascendingRangeSmoke:·gc.alloc.rate.norm "
                "mmap single avgt 0.030 B/op",
                "StoreRangeScanBenchmark.ascendingRangeSmoke:·gc.alloc.rate.norm "
                "mmap double avgt 0.046 B/op",
            ]
        ),
        encoding="utf-8",
    )
    item = SimpleNamespace(
        original=SimpleNamespace(
            argv=["./gradlew", ":benchmarks:jmhSmokeCheck"],
            stdout="BUILD FAILED",
            stderr="",
        ),
        stdout_excerpt="BUILD FAILED",
        stderr_excerpt="",
    )

    reason = implementer._verification_blocker_reason(item, worktree=worktree)  # noqa: SLF001

    assert "benchmark_smoke_diagnostic" in reason
    assert "0.030 B/op" in reason
    assert "0.046 B/op" in reason


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
    assert normalize_task_result_payload(payload) == {
        "feature_id": "feature-1",
        "checks": [],
        "commands_run": [],
    }
    payload = {"task_result_contract": {"feature_id": "feature-2"}}
    assert normalize_task_result_payload(payload) == {
        "feature_id": "feature-2",
        "checks": [],
        "commands_run": [],
    }


def test_normalize_task_result_payload_accepts_string_checks() -> None:
    payload = {
        "feature_id": "feature-1",
        "checks": ["compile_passed", {"name": "smoke", "status": "failed"}],
        "commands_run": ["./gradlew :store:test"],
    }

    assert normalize_task_result_payload(payload) == {
        "feature_id": "feature-1",
        "checks": [
            {"name": "compile_passed", "status": "reported"},
            {"name": "smoke", "status": "failed"},
        ],
        "commands_run": [
            {"name": "./gradlew :store:test", "status": "reported"},
        ],
    }


def test_normalize_task_result_payload_accepts_structured_evidence_lists() -> None:
    payload = {
        "feature_id": "feature-1",
        "changed_files": [{"path": "src/App.java"}],
        "blockers": [{"type": "compile", "message": "compile failed"}],
        "deviations": [{"type": "note", "message": "kept QA tests unchanged"}],
    }

    assert normalize_task_result_payload(payload) == {
        "feature_id": "feature-1",
        "checks": [],
        "commands_run": [],
        "changed_files": ['{"path":"src/App.java"}'],
        "blockers": ['{"message":"compile failed","type":"compile"}'],
        "deviations": ['{"message":"kept QA tests unchanged","type":"note"}'],
    }


def test_implementer_repair_prompt_compacts_previous_response(tmp_path: Path) -> None:
    raw_response = json.dumps(
        {
            "TaskResultContract": {
                "feature_id": "feature-1",
                "task_id": "impl-1",
                "changed_files": ["src/App.java"],
                "checks": [{"command": ["pytest", "-q"], "exit_code": 1, "huge": "x" * 5000}],
                "blockers": ["compile failed"],
                "deviations": ["none"],
            }
        }
    )

    prompt = json.loads(
        build_implementer_repair_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            changed_files=["src/App.java"],
            path_violations=[],
            failed_verifications=[],
            contract_error="compile failed",
            raw_response=raw_response,
            role_context={},
        )
    )

    assert "previous_response" not in prompt
    assert prompt["previous_response_summary"]["changed_files"] == ["src/App.java"]
    assert prompt["previous_response_summary"]["checks"] == [
        {"command": ["pytest", "-q"], "exit_code": 1}
    ]
    assert "x" * 100 not in json.dumps(prompt)


def test_implementer_repair_prompt_includes_jmh_artifact_hints(tmp_path: Path) -> None:
    tmp_path.joinpath("benchmarks/build").mkdir(parents=True)
    tmp_path.joinpath("benchmarks/build/jmh.txt").write_text(
        "Benchmark  Mode  Cnt  Score\n"
        "StoreRangeScanBenchmark.ascendingRangeSmoke avgt 1 42.0 ns/op\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("benchmarks/build/jmh.json").write_text(
        json.dumps(
            [
                {
                    "benchmark": (
                        "com.joshorig.ull.lvc.bench."
                        "StoreRangeScanBenchmark.ascendingRangeSmoke"
                    )
                }
            ]
        ),
        encoding="utf-8",
    )
    failed = SimpleNamespace(
        original=SimpleNamespace(
            argv=["./gradlew", ":benchmarks:jmhSmokeCheck"],
            exit_code=1,
            stdout="BUILD FAILED",
            stderr="",
        ),
        stdout_excerpt="BUILD FAILED",
        stderr_excerpt="",
    )

    prompt = json.loads(
        build_implementer_repair_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            changed_files=["src/App.java"],
            path_violations=[],
            failed_verifications=[failed],
            contract_error=None,
            raw_response="{}",
            role_context={},
        )
    )

    hints = prompt["failed_verifications"][0]["artifact_hints"]
    assert "StoreRangeScanBenchmark.ascendingRangeSmoke" in hints["jmh_text_tail"]
    assert hints["jmh_result_benchmarks"] == [
        "com.joshorig.ull.lvc.bench.StoreRangeScanBenchmark.ascendingRangeSmoke"
    ]


def test_implementer_repair_prompt_includes_gradle_test_artifact_hints(
    tmp_path: Path,
) -> None:
    results_dir = tmp_path / "core/build/test-results/test"
    results_dir.mkdir(parents=True)
    results_dir.joinpath("TEST-com.example.RangeScanApiTest.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.RangeScanApiTest" tests="1" failures="1" errors="0">
  <testcase classname="com.example.RangeScanApiTest" name="visitsInclusiveRange">
    <failure message="expected: &lt;3&gt; but was: &lt;2&gt;" type="AssertionError">
java.lang.AssertionError: expected: &lt;3&gt; but was: &lt;2&gt;
    </failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    failed = SimpleNamespace(
        original=SimpleNamespace(
            argv=["./gradlew", ":core:test", "--tests", "com.example.RangeScanApiTest"],
            exit_code=1,
            stdout=(
                "> Task :core:test FAILED\n"
                "com.example.RangeScanApiTest > visitsInclusiveRange FAILED\n"
                "    java.lang.AssertionError: expected:<3> but was:<2>\n"
                "BUILD FAILED\n"
            ),
            stderr="",
        ),
        stdout_excerpt="BUILD FAILED",
        stderr_excerpt="",
    )

    prompt = json.loads(
        build_implementer_repair_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            changed_files=["src/App.java"],
            path_violations=[],
            failed_verifications=[failed],
            contract_error=None,
            raw_response="{}",
            role_context={},
        )
    )

    hints = prompt["failed_verifications"][0]["artifact_hints"]
    assert hints["gradle_test_failures"][0]["test"] == (
        "com.example.RangeScanApiTest.visitsInclusiveRange"
    )
    assert "expected:" in hints["gradle_test_failures"][0]["message"]
    assert any("visitsInclusiveRange FAILED" in line for line in hints["failure_output_lines"])
    reason = implementer._verification_blocker_reason(  # noqa: SLF001
        failed,
        worktree=tmp_path,
    )
    assert "gradle_test_failures" in reason
    assert "RangeScanApiTest.visitsInclusiveRange" in reason


def test_implementer_prompt_includes_context_capsule(tmp_path: Path) -> None:
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        tests_added=["tests/test_red.py#test_red"],
        matrix_coverage={"criterion": ["tests/test_red.py#test_red"]},
        paths_touched=["tests/test_red.py"],
        red_proof=[{"command": ["pytest", "tests/test_red.py"], "exit_code": 1}],
        worktree_path=str(tmp_path),
    )
    role_context = {
        "contract": "engineering.role_context.v1",
        "packed_context": "packed source summary",
        "memory_digest": "prior decision: preserve zero allocation",
        "relevant_paths": ["src/App.java"],
        "qa_write_paths": ["tests/"],
        "token_savior": {"tokens_saved": 100},
    }

    prompt = json.loads(
        build_implementer_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=qa_contract,
            worktree=tmp_path,
            project_metadata={},
            task_id="impl-1",
            role_context=role_context,
        )
    )

    capsule = prompt["implementer_context_capsule"]
    assert capsule["contract"] == "engineering.implementer_context_capsule.v1"
    assert capsule["qa_handoff"]["tests_added"] == ["tests/test_red.py#test_red"]
    assert capsule["recall"]["memory_digest"] == "prior decision: preserve zero allocation"
    assert "src/App.java" in capsule["recall"]["source_queries"]
    assert "scope_boundary" in capsule["slice"]
    assert prompt["source_starter_pack"]["contract"] == (
        "engineering.implementer_source_starter_pack.v1"
    )

    instructions = " ".join(prompt["instructions"])
    assert "Treat the TaskContract objective as the scope boundary" in instructions
    assert "later plan slices" in instructions


def test_implementer_source_starter_pack_includes_bounded_source_and_qa_tests(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("src/main/java/com/example").mkdir(parents=True)
    tmp_path.joinpath("src/test/java/com/example").mkdir(parents=True)
    tmp_path.joinpath("src/main/java/com/example/RangeStore.java").write_text(
        "class RangeStore {\n  void ascendingRange() {}\n}\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("src/main/java/com/example/Irrelevant.java").write_text(
        "class Irrelevant {}\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("src/test/java/com/example/RangeStoreTest.java").write_text(
        "class RangeStoreTest {\n  void prefixMiss() {}\n}\n",
        encoding="utf-8",
    )
    task_contract = _implementer_contract().model_copy(
        update={
            "objective": "Implement ascendingRange prefix behavior.",
            "allowed_paths": ["src/main/java/"],
            "forbidden_paths": ["src/test/java/"],
            "expected_outputs": ["RangeStore ascendingRange"],
        }
    )
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        tests_added=["src/test/java/com/example/RangeStoreTest.java"],
        paths_touched=["src/test/java/com/example/RangeStoreTest.java"],
        worktree_path=str(tmp_path),
    )

    starter = build_implementer_source_starter_pack(
        worktree=tmp_path,
        task_contract=task_contract,
        qa_contract=qa_contract,
        role_context={"relevant_paths": ["src/main/java/com/example/RangeStore.java"]},
        max_file_chars=120,
    )

    assert starter["contract"] == "engineering.implementer_source_starter_pack.v1"
    assert starter["source_files"][0]["path"] == "src/main/java/com/example/RangeStore.java"
    assert "ascendingRange" in starter["source_files"][0]["excerpt"]
    assert starter["read_only_qa_test_files"][0]["path"] == (
        "src/test/java/com/example/RangeStoreTest.java"
    )
    assert "prefixMiss" in starter["read_only_qa_test_files"][0]["excerpt"]


def test_implementer_prompt_forbids_extra_broad_project_gates(tmp_path: Path) -> None:
    prompt = json.loads(
        build_implementer_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            project_metadata={},
            task_id="impl-1",
            role_context={},
        )
    )

    instructions = " ".join(prompt["instructions"])
    assert "Run exactly the TaskContract verification_commands" in instructions
    assert "./gradlew test" in instructions
    assert "./qa/regression.sh" in instructions


def test_implementer_prompt_preserves_store_invariants(tmp_path: Path) -> None:
    prompt = json.loads(
        build_implementer_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            project_metadata={},
            task_id="impl-1",
            role_context={},
        )
    )

    instructions = " ".join(prompt["instructions"])
    assert "do not wrap raw slot ids modulo slotCount" in instructions
    assert "do not widen point APIs" in instructions
    assert "do not accept partial payload lengths" in instructions
    assert "trimming trailing zero bytes" in instructions


def test_implementer_repair_prompt_forbids_extra_broad_project_gates(
    tmp_path: Path,
) -> None:
    prompt = json.loads(
        build_implementer_repair_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            changed_files=[],
            path_violations=[],
            failed_verifications=[],
            contract_error="failed",
            raw_response="{}",
            role_context={},
        )
    )

    instructions = " ".join(prompt["instructions"])
    assert "Rerun only the TaskContract verification_commands" in instructions
    assert "./gradlew check" in instructions
    assert "full JMH sweeps" in instructions


def test_implementer_repair_prompt_preserves_store_invariants(tmp_path: Path) -> None:
    prompt = json.loads(
        build_implementer_repair_prompt(
            plan=_plan(),
            task_contract=_implementer_contract(),
            qa_contract=QAAuthorContract(
                feature_id="feature-1",
                task_id="qa-1",
                worktree_path=str(tmp_path),
            ),
            worktree=tmp_path,
            changed_files=["store/src/main/java/example/Store.java"],
            path_violations=[],
            failed_verifications=[],
            contract_error=None,
            raw_response="{}",
            role_context={},
        )
    )

    instructions = " ".join(prompt["instructions"])
    assert "no modulo wrapping of raw slot ids" in instructions
    assert "invalid/out-of-range slots" in instructions
    assert "no partial payload writes" in instructions
    assert "no trailing-zero trimming" in instructions


def test_implementer_context_capsule_compacts_large_recall_text(tmp_path: Path) -> None:
    capsule = build_implementer_context_capsule(
        plan=_plan(),
        task_contract=_implementer_contract(),
        qa_contract=QAAuthorContract(
            feature_id="feature-1",
            task_id="qa-1",
            worktree_path=str(tmp_path),
        ),
        role_context={
            "packed_context": "p" * 5000,
            "memory_digest": "m" * 3000,
        },
    )

    assert len(capsule["recall"]["packed_context"]) < 3400
    assert len(capsule["recall"]["memory_digest"]) < 2000


def test_corrective_implementer_falls_back_to_feature_qa_author_contract(
    tmp_path: Path, monkeypatch: Any
) -> None:
    task_contract = _implementer_contract().model_copy(update={"dependencies": []})
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        worktree_path=str(tmp_path),
    )
    monkeypatch.setattr(implementer, "get_task_contract", lambda *args, **kwargs: None)
    monkeypatch.setattr(implementer, "list_task_handoffs", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        implementer,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {"output_contract": {"qa_author_contract": qa_contract.model_dump(mode="json")}}
        ],
    )

    found = implementer._dependency_qa_contract(  # noqa: SLF001
        task_contract, database_url=None
    )

    assert found is not None
    assert found.task_id == "qa-1"
    assert found.worktree_path == str(tmp_path)


def test_corrective_implementer_skips_task_result_when_finding_qa_contract(
    tmp_path: Path, monkeypatch: Any
) -> None:
    task_contract = _implementer_contract().model_copy(
        update={"dependencies": ["impl-previous"]}
    )
    qa_contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="qa-1",
        paths_touched=["tests/test_red.py"],
        worktree_path=str(tmp_path),
    )

    def fake_get_task_contract(task_id: str, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        if task_id == "impl-previous":
            return {
                "output_contract": {
                    "feature_id": "feature-1",
                    "task_id": "impl-previous",
                    "changed_files": ["src/App.java"],
                    "worktree_path": str(tmp_path),
                }
            }
        return None

    monkeypatch.setattr(implementer, "get_task_contract", fake_get_task_contract)
    monkeypatch.setattr(implementer, "list_task_handoffs", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        implementer,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {"output_contract": {"qa_author_contract": qa_contract.model_dump(mode="json")}}
        ],
    )

    found = implementer._dependency_qa_contract(  # noqa: SLF001
        task_contract, database_url=None
    )

    assert found is not None
    assert found.task_id == "qa-1"
    assert found.paths_touched == ["tests/test_red.py"]


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
