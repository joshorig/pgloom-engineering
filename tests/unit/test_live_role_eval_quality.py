import json
import os
from pathlib import Path

from pytest import MonkeyPatch

from pgloom_engineering.contracts import MilestoneContract
from pgloom_engineering.live_role_eval import (
    _diff_introduces_forbidden_query_model,
    _grade_implementation,
    _grade_plan,
    _grade_qa_author,
    _grade_workflow_state,
    _patched_env,
    _role_command,
)
from tests.unit.test_planner_council import _plan_contract


def test_live_role_plan_grade_rejects_unachievable_milestone_signoff() -> None:
    plan = _plan_contract().model_copy(
        update={
            "milestones": [
                MilestoneContract(
                    milestone_id="m1",
                    name="Design and QA",
                    slice_ids=["design", "qa-author"],
                    acceptance_assertions=["acceptance"],
                    validation_contract={"scrutiny": True, "usertest": True},
                    signoff_policy="scrutiny_and_usertest",
                )
            ]
        }
    )

    grade = _grade_plan(
        {"active_plan_contract": {"contract": plan.model_dump(mode="json")}}
    )

    assert grade["verdict"] == "revise"
    assert any(
        finding["code"] == "milestone_signoff_unachievable"
        for finding in grade["findings"]
    )


def test_live_role_grade_rejects_allocating_range_benchmark(tmp_path: Path) -> None:
    snapshots = tmp_path / "file-snapshots.json"
    benchmark_path = "benchmarks/src/jmh/java/com/joshorig/ull/lvc/bench/RangeScanBenchmark.java"
    conformance_path = (
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/"
        "RangeConformanceTest.java"
    )
    support_path = (
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/"
        "RangeApiTestSupport.java"
    )
    snapshots.write_text(
        json.dumps(
            {
                benchmark_path: {
                    "excerpt": "Proxy.newProxyInstance(... InvocationHandler ...)"
                },
                conformance_path: {
                    "excerpt": "assertPrefixOverloadPresent(store, \"ascendingRange\");"
                },
                support_path: {
                    "excerpt": "assertVisitedSlots(...); payload.putByte(4, value);"
                },
            }
        ),
        encoding="utf-8",
    )
    output_evidence = {
        "file_snapshots_path": str(snapshots),
        "changed_files": [benchmark_path],
    }
    aggregate = {
        "task_contracts": [
            {
                "input_contract": {"task_type": "engineering.qa.author"},
                "output_contract": {
                    "qa_author_contract": {
                        "tests_added": ["RangeConformanceTest"],
                        "red_proof": [{"cmd": ["./gradlew", "test"]}],
                        "matrix_coverage": {"assert-prefix": ["RangeConformanceTest"]},
                    }
                },
            },
            {
                "input_contract": {"task_type": "engineering.implement"},
                "output_contract": {
                    "task_result_contract": {
                        "changed_files": output_evidence["changed_files"],
                        "blockers": [],
                    }
                },
            },
        ]
    }

    qa_grade = _grade_qa_author(aggregate, output_evidence)
    impl_grade = _grade_implementation(aggregate, output_evidence)

    assert qa_grade["verdict"] == "revise"
    assert {finding["code"] for finding in qa_grade["findings"]} >= {
        "qa_benchmark_allocating_visitor",
        "qa_prefix_filter_only_structural",
    }
    assert impl_grade["verdict"] == "revise"
    assert {finding["code"] for finding in impl_grade["findings"]} >= {
        "implementation_allocating_benchmark_visitor",
        "implementation_range_benchmark_not_gated",
    }


def test_live_role_qa_grade_rejects_payload_prefix_drift(tmp_path: Path) -> None:
    snapshots = tmp_path / "file-snapshots.json"
    conformance_path = (
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/"
        "RangeScanConformanceTest.java"
    )
    snapshots.write_text(
        json.dumps(
            {
                conformance_path: {
                    "excerpt": """
                    class RangeScanConformanceTest {
                        void prefixFilterMatchesAndRejectsKeysForSingleAndDoubleStores() {
                            collectAscending(store, 0, 7, PREFIX_MATCH);
                        }
                        private static byte[] payloadFor(int slot) {
                            byte[] payload = new byte[16];
                            payload[0] = PREFIX_MATCH[0];
                            payload[1] = PREFIX_MATCH[1];
                            return payload;
                        }
                    }
                    """
                },
            }
        ),
        encoding="utf-8",
    )
    aggregate = {
        "task_contracts": [
            {
                "input_contract": {"task_type": "engineering.qa.author"},
                "output_contract": {
                    "qa_author_contract": {
                        "tests_added": ["RangeScanConformanceTest"],
                        "red_proof": [{"cmd": ["./gradlew", "test"]}],
                        "matrix_coverage": {"assert-prefix": ["RangeScanConformanceTest"]},
                    }
                },
            },
        ]
    }

    grade = _grade_qa_author(aggregate, {"file_snapshots_path": str(snapshots)})

    assert grade["verdict"] == "revise"
    assert any(
        finding["code"] == "qa_key_prefix_drifted_to_payload_prefix"
        for finding in grade["findings"]
    )


def test_live_role_grade_uses_latest_completed_role_output() -> None:
    aggregate = {
        "task_contracts": [
            {
                "status": "completed",
                "input_contract": {"task_type": "engineering.implement"},
                "output_contract": {
                    "task_result_contract": {
                        "changed_files": ["src/Old.java"],
                        "blockers": [],
                    }
                },
            },
            {
                "status": "completed",
                "input_contract": {"task_type": "engineering.implement"},
                "output_contract": {
                    "task_result_contract": {
                        "changed_files": ["src/New.java"],
                        "blockers": ["latest corrective output still failed"],
                    }
                },
            },
        ]
    }

    grade = _grade_implementation(aggregate, {"git_diff_path": ""})

    assert grade["verdict"] == "revise"
    assert any(
        "latest corrective output still failed" in finding["message"]
        for finding in grade["findings"]
    )


def test_live_role_grade_rejects_unfinished_workflow_tasks() -> None:
    grade = _grade_workflow_state(
        {
            "tasks": [
                {
                    "id": "impl-1",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "blocker_code": "engineering.implementation_verification_failed",
                },
                {
                    "id": "review-1",
                    "task_type": "engineering.review",
                    "state": "abandoned",
                },
            ]
        }
    )

    assert grade["verdict"] == "revise"
    assert grade["findings"] == [
        {
            "severity": "blocking",
            "code": "workflow_task_not_complete",
            "message": (
                "engineering.implement task impl-1 is blocked "
                "(engineering.implementation_verification_failed)."
            ),
        }
    ]


def test_forbidden_model_grade_ignores_guardrail_documentation() -> None:
    assert not _diff_introduces_forbidden_query_model(
        "\n".join(
            [
                "+- **Out of scope:** Sorted-by-value queries, composite keys, secondary indexes.",
                (
                    "+- Current implementation uses direct prefix comparison; "
                    "no secondary index is introduced."
                ),
            ]
        )
    )
    assert _diff_introduces_forbidden_query_model(
        "+Add a secondary index map for fast range lookup."
    )


def test_live_role_codex_commands_use_full_access_sandbox() -> None:
    command = _role_command(
        backend="codex",
        model="gpt-5.5",
        reasoning="medium",
        planner=False,
    )

    assert command[command.index("-s") + 1] == "danger-full-access"
    assert command[command.index("-C") + 1] == "{worktree}"


def test_live_role_env_enables_role_context_isolation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with _patched_env(
        {
            "planner": ["codex", "exec", "-m", "gpt-5.5"],
            "implementer": ["codex", "exec", "-m", "gpt-5.5"],
            "reviewer": ["codex", "exec", "-m", "gpt-5.5"],
            "qa_author": ["codex", "exec", "-m", "gpt-5.4"],
            "qa_validation": ["codex", "exec", "-m", "gpt-5.4"],
        }
    ):
        assert os.environ["PGLOOM_ENGINEERING_ROLE_MODEL_CONTEXT_ISOLATION_ENABLED"] == "false"
        assert (
            os.environ["PGLOOM_ENGINEERING_IMPLEMENTER_MODEL_CONTEXT_ISOLATION_ENABLED"]
            == "true"
        )
        assert (
            os.environ["PGLOOM_ENGINEERING_IMPLEMENTER_MODEL_CONTEXT_ADD_DIR_ENABLED"]
            == "false"
        )
        assert (
            os.environ["PGLOOM_ENGINEERING_REVIEWER_MODEL_CONTEXT_ADD_DIR_ENABLED"]
            == "false"
        )
        assert os.environ["PGLOOM_ENGINEERING_QA_AUTHOR_MODEL_CONTEXT_ISOLATION_ENABLED"] == "false"
        assert (
            os.environ["PGLOOM_ENGINEERING_QA_AUTHOR_MODEL_CONTEXT_ADD_DIR_ENABLED"]
            == "false"
        )
        assert (
            os.environ["PGLOOM_ENGINEERING_QA_VALIDATION_MODEL_CONTEXT_ISOLATION_ENABLED"]
            == "false"
        )
        assert (
            os.environ["PGLOOM_ENGINEERING_QA_VALIDATION_MODEL_CONTEXT_ADD_DIR_ENABLED"]
            == "false"
        )
        assert os.environ["PGLOOM_ENGINEERING_REVIEWER_MODEL_CONTEXT_ISOLATION_ENABLED"] == "true"
        assert os.environ["PGLOOM_ENGINEERING_PLANNER_CODEX_PANELIST_REASONING"] == "medium"
        assert os.environ["PGLOOM_ENGINEERING_PLANNER_CODEX_CONSOLIDATOR_REASONING"] == "medium"
        assert os.environ["PGLOOM_ENGINEERING_PLANNER_CODEX_CRITIC_REASONING"] == "medium"
        assert os.environ["PGLOOM_ENGINEERING_REVIEWER_INVOCATION_TIMEOUT_SECONDS"] == "1200"
        assert os.environ["PGLOOM_ENGINEERING_QA_VALIDATION_INVOCATION_TIMEOUT_SECONDS"] == "1200"
        context_root = os.environ["PGLOOM_ENGINEERING_ROLE_MODEL_CONTEXT_ROOT"]
        assert context_root == ".local/role-context-root"
        assert (tmp_path / context_root).is_dir()
