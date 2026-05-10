from __future__ import annotations

import pytest

from pgloom_engineering.contracts import (
    AgentTopologyPolicy,
    DesignContract,
    HandoffEnvelope,
    ImplementationTopology,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
    ValidationEvidence,
    validate_plan_contract,
)


def valid_plan() -> PlanContract:
    return PlanContract(
        feature_id="wf_demo",
        project="pgloom",
        problem_statement="Deliver the demo feature.",
        design_contract=DesignContract(
            public_api="CLI feature",
            acceptance_tests=["pytest"],
        ),
        affected_surfaces=["pgloom_engineering"],
        implementation_topology=ImplementationTopology.COUNCIL_DECIDES,
        task_slices=[
            TaskSliceContract(
                slice_id="slice-1",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement the feature.",
                allowed_paths=["pgloom_engineering"],
                forbidden_paths=["pgloom"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["pytest"]],
            )
        ],
        acceptance_test_matrix=["pytest verifies feature behavior"],
    )


def test_agent_topology_defaults_to_multi_agent_planning_and_review() -> None:
    policy = AgentTopologyPolicy()
    assert policy.planning == "multi_agent"
    assert policy.review == "multi_agent"
    assert policy.implementation == ImplementationTopology.COUNCIL_DECIDES


def test_valid_plan_contract_passes_validation() -> None:
    assert validate_plan_contract(valid_plan()) == []


def test_plan_validation_rejects_weak_handoffs() -> None:
    plan = valid_plan()
    plan.acceptance_test_matrix = []
    plan.task_slices[0].forbidden_paths = []
    plan.task_slices[0].verification_commands = []
    errors = validate_plan_contract(plan)
    assert {row["code"] for row in errors} >= {
        "missing_acceptance_matrix",
        "missing_forbidden_paths",
        "missing_verification_commands",
    }


def test_plan_validation_rejects_role_task_type_mismatch() -> None:
    plan = valid_plan()
    plan.task_slices[0].role = "reviewer"
    plan.task_slices[0].task_type = "engineering.finalization"

    errors = validate_plan_contract(plan)

    assert {row["code"] for row in errors} >= {"invalid_role_task_type"}


def test_plan_validation_accepts_module_test_roots_for_qa() -> None:
    plan = valid_plan()
    plan.task_slices[0].role = "qa"
    plan.task_slices[0].task_type = "engineering.qa.verify.scrutiny"
    plan.task_slices[0].allowed_paths = ["tests/", "store/src/test/"]

    errors = validate_plan_contract(plan)

    assert "qa_paths_not_restricted" not in {row["code"] for row in errors}


def test_plan_validation_accepts_metadata_test_support_paths_for_qa() -> None:
    plan = valid_plan()
    plan.task_slices[0].role = "qa"
    plan.task_slices[0].task_type = "engineering.qa.author"
    plan.task_slices[0].allowed_paths = ["benchmarks/src/jmh/java/", "benchmarks/build.gradle/"]

    errors = validate_plan_contract(
        plan,
        qa_write_paths=["benchmarks/src/jmh/java", "benchmarks/build.gradle"],
    )

    assert "qa_paths_not_restricted" not in {row["code"] for row in errors}


def test_plan_validation_accepts_default_fixture_root_with_metadata_paths() -> None:
    plan = valid_plan()
    plan.task_slices[0].role = "qa"
    plan.task_slices[0].task_type = "engineering.qa.verify.usertest"
    plan.task_slices[0].allowed_paths = ["qa/fixtures/", "core/src/test/java/"]

    errors = validate_plan_contract(
        plan,
        qa_write_paths=["core/src/test/java", "benchmarks/src/jmh/java"],
    )

    assert "qa_paths_not_restricted" not in {row["code"] for row in errors}


def test_plan_validation_rejects_legacy_single_qa_verify() -> None:
    plan = valid_plan()
    plan.task_slices[0].role = "qa"
    plan.task_slices[0].task_type = "engineering.qa.verify"
    plan.task_slices[0].allowed_paths = ["tests/"]

    errors = validate_plan_contract(plan)

    assert {row["code"] for row in errors} >= {"invalid_role_task_type"}


def test_plan_validation_enforces_milestones_and_assertion_coverage() -> None:
    plan = valid_plan()
    plan.acceptance_assertions = ["assert-1", "assert-2"]
    plan.task_slices[0].acceptance_assertion_ids = ["assert-1"]
    plan.milestones = [
        MilestoneContract(
            milestone_id="m1",
            name="Milestone 1",
            slice_ids=["slice-1"],
            acceptance_assertions=["assert-1", "assert-2"],
        )
    ]

    errors = validate_plan_contract(plan)

    assert {row["code"] for row in errors} >= {"acceptance_assertion_unclaimed"}


def test_plan_validation_matches_assertion_ids_with_descriptions() -> None:
    plan = valid_plan()
    plan.acceptance_assertions = ["assert-1: Feature behavior is covered."]
    plan.task_slices[0].acceptance_assertion_ids = ["assert-1"]
    plan.milestones = [
        MilestoneContract(
            milestone_id="m1",
            name="Milestone 1",
            slice_ids=["slice-1"],
            acceptance_assertions=["assert-1: Feature behavior is covered."],
        )
    ]

    errors = validate_plan_contract(plan)

    assert "acceptance_assertion_unclaimed" not in {row["code"] for row in errors}
    assert "acceptance_assertion_unknown" not in {row["code"] for row in errors}


def test_handoff_envelope_carries_typed_evidence_and_cumulative_telemetry() -> None:
    envelope = HandoffEnvelope(
        handoff_id="handoff-1",
        handoff_type="validation",
        feature_id="feature-1",
        task_id="task-1",
        role="qa",
        summary="Scrutiny passed.",
        evidence=[
            ValidationEvidence(
                evidence_id="evidence-1",
                kind="test_run",
                summary="pytest passed",
                verdict="pass",
            )
        ],
        cumulative_cost_usd="1.25",
        cumulative_wall_clock_seconds=12.5,
        cumulative_input_tokens=100,
        cumulative_output_tokens=20,
        cumulative_tokens_saved=75,
        cumulative_model_calls=2,
    )

    assert envelope.evidence[0].kind == "test_run"
    assert envelope.cumulative_model_calls == 2


def test_plan_validation_rejects_implementer_qa_write_paths() -> None:
    plan = valid_plan()
    plan.task_slices[0].allowed_paths = ["pgloom_engineering", "app-api/src/test/"]

    errors = validate_plan_contract(plan)

    assert {row["code"] for row in errors} >= {"implementer_claims_qa_paths"}


def test_plan_validation_rejects_drift_without_supersession() -> None:
    plan = valid_plan()
    plan.design_contract.public_api = "new API"

    errors = validate_plan_contract(
        plan,
        origin_contract={"design_contract": {"public_api": "old API"}},
    )

    assert {row["code"] for row in errors} == {"planner_contract_drift"}


def test_plan_validation_allows_drift_with_supersession() -> None:
    plan = valid_plan()
    plan.design_contract.public_api = "new API"
    plan.supersedes_plan_id = "plan_old"
    plan.supersession_rationale = "Replace outdated API."

    assert (
        validate_plan_contract(
            plan,
            origin_contract={"design_contract": {"public_api": "old API"}},
        )
        == []
    )


def test_plan_validation_rejects_stateful_lifecycle_without_edge_coverage() -> None:
    plan = valid_plan()
    plan.design_contract.public_api = "Store.snapshot(Path) and Store.restore(Path)"
    plan.design_contract.acceptance_tests = ["happy path round trip"]
    plan.acceptance_test_matrix = ["happy path round trip"]

    errors = validate_plan_contract(plan)

    assert {row["code"] for row in errors} == {"planner_contract_incomplete"}


def test_plan_contract_rejects_forward_dependencies() -> None:
    with pytest.raises(ValueError, match="depends on unknown or later slices"):
        PlanContract(
            feature_id="wf_demo",
            project="pgloom",
            problem_statement="Bad dependency.",
            design_contract=DesignContract(),
            affected_surfaces=["pgloom_engineering"],
            task_slices=[
                TaskSliceContract(
                    slice_id="slice-1",
                    role="implementer",
                    task_type="engineering.implement",
                    objective="Implement.",
                    allowed_paths=["pgloom_engineering"],
                    forbidden_paths=["pgloom"],
                    depends_on=["slice-2"],
                    expected_outputs=["TaskResultContract"],
                    verification_commands=[["pytest"]],
                )
            ],
            acceptance_test_matrix=["pytest"],
        )
