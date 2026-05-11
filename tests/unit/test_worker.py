from __future__ import annotations

from typing import Any

from pgloom_engineering import worker
from pgloom_engineering.contracts import MilestoneContract
from pgloom_engineering.worker import (
    _commands_run_from_result,
    _milestone_id,
    _milestone_signed_off,
    _persist_qa_result_contract,
    _post_execution_gate,
    _qa_result_artifact_ids,
    _record_dependency_handoffs,
    _requires_handoff,
)


def test_qa_verify_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.verify.scrutiny"})


def test_qa_author_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.author"})


def test_reviewer_requires_producer_handoff() -> None:
    assert _requires_handoff({"task_type": "engineering.review"})


def test_commands_run_from_result_falls_back_to_checks() -> None:
    assert _commands_run_from_result(
        {
            "task_result_contract": {
                "checks": [
                    {
                        "command": ["pytest", "-q"],
                        "exit_code": 0,
                        "duration_seconds": 1.2,
                    }
                ]
            }
        }
    ) == [{"cmd": ["pytest", "-q"], "exit_code": 0, "duration_s": 1.2}]


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
        return {}

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
