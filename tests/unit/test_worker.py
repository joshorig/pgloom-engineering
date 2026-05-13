from __future__ import annotations

from pathlib import Path
from typing import Any

from pgloom_engineering import worker
from pgloom_engineering.contracts import MilestoneContract, RoleGateContract, TaskContract
from pgloom_engineering.projects import ProjectConfig
from pgloom_engineering.worker import (
    _artifact_evidence_links_from_result,
    _commands_run_from_result,
    _has_reviewable_dependency_output,
    _milestone_id,
    _milestone_signed_off,
    _persist_qa_result_contract,
    _post_execution_gate,
    _qa_result_artifact_ids,
    _record_dependency_handoffs,
    _requires_handoff,
    _role_gate_blocker,
)


def test_qa_verify_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.verify.scrutiny"})


def test_qa_author_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.author"})


def test_reviewer_requires_producer_handoff() -> None:
    assert _requires_handoff({"task_type": "engineering.review"})


def test_role_gate_blocker_allows_missing_task_contract_gate_when_current_gate_enabled() -> None:
    task_contract = TaskContract(
        role="implementer",
        task_type="engineering.implement",
        feature_id="feature-1",
        plan_contract_id="plan-1",
        objective="Implement.",
        allowed_paths=["src/main/"],
        forbidden_paths=["src/test/"],
    )

    blocker = _role_gate_blocker(
        ProjectConfig(name="demo", root=Path("/tmp/demo")),
        task_contract,
    )

    assert blocker is None


def test_role_gate_blocker_uses_current_project_gate() -> None:
    task_contract = TaskContract(
        role="implementer",
        task_type="engineering.implement",
        feature_id="feature-1",
        plan_contract_id="plan-1",
        objective="Implement.",
        allowed_paths=["src/main/"],
        forbidden_paths=["src/test/"],
        role_gate=RoleGateContract(
            project="demo",
            role="implementer",
            status="enabled",
            reason="role enabled by engineering_projects.metadata.role_gates",
        ),
    )

    blocker = _role_gate_blocker(
        ProjectConfig(
            name="demo",
            root=Path("/tmp/demo"),
            metadata={"role_gates": {"implementer": "disabled"}},
        ),
        task_contract,
    )

    assert blocker == "role gated to disabled in engineering_projects.metadata.role_gates"


def test_review_handoff_gate_accepts_qa_author_repair_dependency(monkeypatch: Any) -> None:
    task_contract = TaskContract(
        role="reviewer",
        task_type="engineering.review",
        feature_id="feature-1",
        plan_contract_id="plan-1",
        objective="Review QA-only corrective repair.",
        allowed_paths=["tests/"],
        forbidden_paths=["src/main/"],
        dependencies=["qa-author-1"],
        expected_outputs=["ReviewVerdictContract"],
    )

    monkeypatch.setattr(worker, "list_task_handoffs", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        worker,
        "get_task_contract",
        lambda task_id, **kwargs: {
            "output_contract": {"qa_author_contract": {"task_id": task_id}}
        }
        if task_id == "qa-author-1"
        else None,
    )

    assert _has_reviewable_dependency_output(
        task_contract,
        task_id="review-1",
        database_url=None,
    )


def test_commands_run_from_result_falls_back_to_checks() -> None:
    assert _commands_run_from_result(
        {
            "task_result_contract": {
                "checks": [
                    {
                        "command": ["pytest", "-q"],
                        "exit_code": 0,
                        "duration_seconds": 1.2,
                        "artifact_ids": ["artifact-log"],
                    }
                ]
            }
        }
    ) == [
        {
            "cmd": ["pytest", "-q"],
            "exit_code": 0,
            "duration_s": 1.2,
            "artifact_ids": ["artifact-log"],
        }
    ]


def test_commands_run_from_result_uses_qa_author_red_proof_artifacts() -> None:
    result = {
        "qa_author_contract": {
            "red_proof": [
                {
                    "command": ["./gradlew", ":core:test"],
                    "exit_code": 1,
                    "duration_s": 2.5,
                    "artifact_ids": ["artifact-stdout", "artifact-stderr"],
                }
            ]
        }
    }

    assert _commands_run_from_result(result) == [
        {
            "cmd": ["./gradlew", ":core:test"],
            "exit_code": 1,
            "duration_s": 2.5,
            "artifact_ids": ["artifact-stdout", "artifact-stderr"],
        }
    ]
    assert worker._artifact_ids_from_result(result) == [  # noqa: SLF001
        "artifact-stdout",
        "artifact-stderr",
    ]


def test_commands_run_from_result_keeps_malformed_command_metadata() -> None:
    commands = _commands_run_from_result(
        {
            "qa_author_contract": {
                "red_proof": [
                    {
                        "command": ["pytest", "-q"],
                        "exit_code": "not an int",
                        "duration_s": "duration()",
                    }
                ]
            }
        }
    )

    assert commands == [
        {
            "cmd": ["pytest", "-q"],
            "exit_code": 0,
            "duration_s": 0.0,
            "normalization_warnings": [
                {
                    "code": "command_metadata_coercion_failed",
                    "field": "exit_code",
                    "raw_value": "not an int",
                    "default": 0,
                },
                {
                    "code": "command_metadata_coercion_failed",
                    "field": "duration_s",
                    "raw_value": "duration()",
                    "default": 0.0,
                },
            ],
        }
    ]


def test_exhausted_worker_crash_blocks_instead_of_failing(monkeypatch: Any) -> None:
    transitions: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []

    monkeypatch.setattr(
        worker,
        "retry_or_fail_task",
        lambda *args, **kwargs: retries.append({"args": args, **kwargs}),
    )
    monkeypatch.setattr(
        worker,
        "transition_task",
        lambda *args, **kwargs: transitions.append({"args": args, **kwargs}),
    )

    status = worker._retry_or_block_worker_crash(  # noqa: SLF001
        {
            "id": "task-1",
            "task_type": "engineering.qa.author",
            "attempt": 3,
            "max_attempts": 3,
            "payload": {},
        },
        crash_result={
            "worker_crash": {
                "message": "worker crash: invalid output",
                "exception_type": "ValueError",
            }
        },
        database_url=None,
    )

    assert status == "blocked"
    assert retries == []
    assert transitions[0]["args"][1].value == "blocked"
    assert transitions[0]["blocker_code"] == "engineering.worker_crash"
    assert transitions[0]["result"]["worker_crash"]["exception_type"] == "ValueError"


def test_commands_run_from_result_uses_blocked_command_excerpts() -> None:
    assert _commands_run_from_result(
        {
            "commands": [["./gradlew", ":benchmarks:compileJmhJava"]],
            "stdout_excerpt": "> Task :benchmarks:compileJmhJava FAILED",
            "stderr_excerpt": "Compilation failed",
        }
    ) == [
        {
            "cmd": ["./gradlew", ":benchmarks:compileJmhJava"],
            "exit_code": 1,
            "duration_s": 0.0,
            "stdout_excerpt": "> Task :benchmarks:compileJmhJava FAILED",
            "stderr_excerpt": "Compilation failed",
        }
    ]


def test_commands_run_from_result_prefers_blocked_per_command_evidence() -> None:
    assert _commands_run_from_result(
        {
            "commands": [["pytest", "-q"], ["ruff", "check"]],
            "stdout_excerpt": "generic failure",
            "commands_run": [
                {
                    "cmd": ["pytest", "-q"],
                    "exit_code": 1,
                    "duration_s": 2.0,
                    "stdout_excerpt": "test failure",
                    "artifact_ids": ["artifact-test"],
                },
                {
                    "cmd": ["ruff", "check"],
                    "exit_code": 0,
                    "duration_s": 1.0,
                    "stdout_excerpt": "clean",
                    "artifact_ids": ["artifact-lint"],
                },
            ],
        }
    ) == [
        {
            "cmd": ["pytest", "-q"],
            "exit_code": 1,
            "duration_s": 2.0,
            "artifact_ids": ["artifact-test"],
            "stdout_excerpt": "test failure",
        },
        {
            "cmd": ["ruff", "check"],
            "exit_code": 0,
            "duration_s": 1.0,
            "artifact_ids": ["artifact-lint"],
            "stdout_excerpt": "clean",
        },
    ]


def test_record_dependency_handoffs_targets_dependent_task_contracts(monkeypatch: Any) -> None:
    handoffs: list[dict[str, Any]] = []
    monkeypatch.setattr(
        worker,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {
                "task_id": "review-1",
                "input_contract": {"dependencies": ["impl-1"]},
            },
            {
                "task_id": "qa-verify-1",
                "input_contract": {"dependencies": ["review-1"]},
            },
        ],
    )
    def record_handoff_stub(**kwargs: Any) -> dict[str, Any]:
        handoffs.append(kwargs)
        return {"id": "handoff-1", **kwargs}

    monkeypatch.setattr(
        worker,
        "record_handoff",
        record_handoff_stub,
    )

    _record_dependency_handoffs(
        feature_id="feature-1",
        from_task_id="impl-1",
        handoff_type="task_result",
        contract={"changed_files": ["src/App.java"]},
        database_url=None,
    )

    assert handoffs == [
        {
            "feature_id": "feature-1",
            "from_task_id": "impl-1",
            "to_task_id": "review-1",
            "handoff_type": "task_result",
            "contract": {"changed_files": ["src/App.java"]},
            "database_url": None,
        }
    ]


def test_record_dependency_handoffs_returns_created_handoff_rows(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        worker,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {
                "task_id": "review-1",
                "input_contract": {"dependencies": ["impl-1"]},
            }
        ],
    )
    monkeypatch.setattr(
        worker,
        "record_handoff",
        lambda **kwargs: {"id": "handoff-1", **kwargs},
    )

    handoffs = _record_dependency_handoffs(
        feature_id="feature-1",
        from_task_id="impl-1",
        handoff_type="task_result",
        contract={"changed_files": ["src/App.java"]},
        database_url=None,
    )

    assert handoffs[0]["id"] == "handoff-1"
    assert handoffs[0]["to_task_id"] == "review-1"


def test_handoff_id_from_result_accepts_top_level_handoff_ids() -> None:
    assert worker._handoff_id_from_result({"handoff_id": "handoff-1"}) == "handoff-1"  # noqa: SLF001
    assert (
        worker._handoff_id_from_result(  # noqa: SLF001
            {"handoff_ids": ["handoff-2", "handoff-3"]}
        )
        == "handoff-2"
    )


def test_milestone_signoff_requires_both_split_validators(monkeypatch: Any) -> None:
    milestone = MilestoneContract(
        milestone_id="m1",
        name="Milestone 1",
        slice_ids=["qa-scrutiny", "qa-usertest"],
    )
    monkeypatch.setattr(
        worker,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {
                "status": "completed",
                "input_contract": {
                    "task_type": "engineering.qa.verify.scrutiny",
                    "inputs": {"task_slice_id": "qa-scrutiny"},
                },
                "output_contract": {
                    "qa_result_contract": {"verdict": "pass"},
                },
            }
        ],
    )

    assert not _milestone_signed_off("feature-1", milestone, database_url=None)

    monkeypatch.setattr(
        worker,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {
                "status": "completed",
                "input_contract": {
                    "task_type": "engineering.qa.verify.scrutiny",
                    "inputs": {"task_slice_id": "qa-scrutiny"},
                },
                "output_contract": {"qa_result_contract": {"verdict": "pass"}},
            },
            {
                "status": "completed",
                "input_contract": {
                    "task_type": "engineering.qa.verify.usertest",
                    "inputs": {"task_slice_id": "qa-usertest"},
                },
                "output_contract": {"qa_result_contract": {"verdict": "pass"}},
            },
        ],
    )

    assert _milestone_signed_off("feature-1", milestone, database_url=None)


def test_milestone_signoff_uses_durable_qa_signoffs(monkeypatch: Any) -> None:
    milestone = MilestoneContract(
        milestone_id="m1",
        name="Milestone 1",
        slice_ids=["qa-scrutiny", "qa-usertest"],
    )
    monkeypatch.setattr(
        worker,
        "list_qa_signoffs",
        lambda *args, **kwargs: [
            {"validator_type": "scrutiny", "verdict": "pass"},
            {"validator_type": "usertest", "verdict": "pass"},
        ],
    )
    monkeypatch.setattr(worker, "list_task_contracts", lambda *args, **kwargs: [])

    assert _milestone_signed_off("feature-1", milestone, database_url=None)


def test_qa_result_artifact_ids_collects_validation_and_command_artifacts() -> None:
    from pgloom_engineering.contracts import QAResultContract

    contract = QAResultContract(
        feature_id="feature-1",
        task_id="qa-1",
        verdict="pass",
        validation_evidence=[
            {"artifact_ids": ["artifact-a", "artifact-b"]},
            {"artifact_ids": ["artifact-a"]},
        ],
        commands_run=[{"artifact_ids": ["artifact-c"]}],
    )

    assert _qa_result_artifact_ids(contract) == ["artifact-a", "artifact-b", "artifact-c"]


def test_artifact_evidence_links_collects_validation_artifact_mapping() -> None:
    assert _artifact_evidence_links_from_result(
        {
            "qa_result_contract": {
                "validation_evidence": [
                    {
                        "evidence_id": "ev-1",
                        "kind": "ui_exercise",
                        "artifact_ids": ["artifact-a", "artifact-b"],
                    },
                    {"evidence_id": "ev-2", "artifact_ids": ["artifact-c"]},
                    {"artifact_ids": ["artifact-ignored"]},
                ]
            }
        }
    ) == [
        {
            "artifact_id": "artifact-a",
            "evidence_id": "ev-1",
            "evidence_kind": "ui_exercise",
        },
        {
            "artifact_id": "artifact-b",
            "evidence_id": "ev-1",
            "evidence_kind": "ui_exercise",
        },
        {
            "artifact_id": "artifact-c",
            "evidence_id": "ev-2",
            "evidence_kind": None,
        },
    ]


def test_blocked_qa_result_contract_is_persisted(monkeypatch: Any) -> None:
    from pgloom.harness.result import HandlerResult

    upserts: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    signoffs: list[dict[str, Any]] = []
    input_contract = {
        "feature_id": "feature-1",
        "plan_contract_id": "plan-1",
        "role": "qa",
        "task_type": "engineering.qa.verify.scrutiny",
        "objective": "verify",
        "allowed_paths": ["tests/"],
        "forbidden_paths": ["src/"],
    }
    monkeypatch.setattr(
        worker,
        "get_task_contract",
        lambda *args, **kwargs: {"input_contract": input_contract},
    )
    def upsert_task_contract_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        upserts.append(kwargs)
        return {}

    monkeypatch.setattr(
        worker,
        "upsert_task_contract",
        upsert_task_contract_stub,
    )
    monkeypatch.setattr(
        worker,
        "_record_dependency_handoffs",
        lambda **kwargs: handoffs.append(kwargs),
    )
    monkeypatch.setattr(
        worker,
        "record_qa_signoff",
        lambda **kwargs: signoffs.append(kwargs),
    )

    result = HandlerResult(
        status="blocked",
        result={
            "qa_result_contract": {
                "feature_id": "feature-1",
                "task_id": "qa-1",
                "verdict": "fail",
                "commands_run": [
                    {
                        "cmd": ["pytest", "-q"],
                        "exit_code": 1,
                        "duration_s": 1.0,
                        "artifact_ids": ["artifact-stdout"],
                    }
                ],
                "validation_evidence": [
                    {
                        "evidence_id": "qa-1:command:0",
                        "kind": "test_run",
                        "summary": "failed",
                        "verdict": "fail",
                        "artifact_ids": ["artifact-stdout"],
                    }
                ],
                "validator_type": "scrutiny",
            }
        },
    )

    _persist_qa_result_contract(
        {"id": "qa-1", "task_type": "engineering.qa.verify.scrutiny"},
        result,
        feature_id="feature-1",
        status="blocked",
        database_url=None,
    )

    assert upserts[0]["status"] == "blocked"
    assert upserts[0]["output_contract"]["qa_result_contract"]["verdict"] == "fail"
    assert (
        upserts[0]["output_contract"]["qa_result_contract"]["commands_run"][0]["artifact_ids"]
        == ["artifact-stdout"]
    )
    assert handoffs[0]["handoff_type"] == "validation"
    assert not signoffs


def test_review_non_approve_blocks_after_persisting_contract(monkeypatch: Any) -> None:
    from pgloom.harness.result import HandlerResult

    upserts: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    input_contract = {
        "feature_id": "feature-1",
        "plan_contract_id": "plan-1",
        "role": "reviewer",
        "task_type": "engineering.review",
        "objective": "review",
        "allowed_paths": ["src/"],
        "forbidden_paths": [],
    }
    monkeypatch.setattr(
        worker,
        "get_task_contract",
        lambda *args, **kwargs: {"input_contract": input_contract},
    )
    def upsert_task_contract_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        upserts.append(kwargs)
        return {}

    monkeypatch.setattr(
        worker,
        "upsert_task_contract",
        upsert_task_contract_stub,
    )
    monkeypatch.setattr(
        worker,
        "_record_dependency_handoffs",
        lambda **kwargs: handoffs.append(kwargs),
    )
    monkeypatch.setattr(
        worker,
        "_record_recovery",
        lambda *args, **kwargs: recoveries.append(kwargs),
    )

    result = _post_execution_gate(
        {
            "id": "review-1",
            "task_type": "engineering.review",
            "workflow_id": "feature-1",
            "payload": {"feature_id": "feature-1"},
        },
        HandlerResult.done(
            {
                "review_verdict_contract": {
                    "feature_id": "feature-1",
                    "task_id": "review-1",
                    "panel": ["reviewer"],
                    "verdict": "coder_repair",
                    "findings": [
                        (
                            "conformance-tests/src/test/java/RangeScanConformanceTest.java "
                            "needs prefix coverage"
                        )
                    ],
                    "rationale": "needs repair",
                }
            }
        ),
        database_url=None,
    )

    assert result.status == "blocked"
    assert result.blocker_code == "engineering.review_rejected"
    assert result.blocker_reason is not None
    assert "conformance-tests/src/test/java/RangeScanConformanceTest.java" in (
        result.blocker_reason
    )
    assert upserts[0]["status"] == "completed"
    assert handoffs[0]["handoff_type"] == "review"
    assert recoveries[0]["blocker_code"] == "engineering.review_rejected"
    assert recoveries[0]["action"] == "corrective_slice"
    assert "conformance-tests/src/test/java/RangeScanConformanceTest.java" in recoveries[0][
        "rationale"
    ]


def test_milestone_id_reads_task_contract_input() -> None:
    from pgloom_engineering.contracts import TaskContract

    contract = TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="qa",
        task_type="engineering.qa.verify.scrutiny",
        objective="verify",
        inputs={"milestone_id": "m1"},
        allowed_paths=["tests/"],
        forbidden_paths=["src/"],
    )

    assert _milestone_id(contract) == "m1"
