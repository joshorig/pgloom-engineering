from __future__ import annotations

from pgloom_engineering.contracts import DesignContract, PlanContract, TaskSliceContract
from pgloom_engineering.roles.planner import _canonicalize_plan_feature_id


def test_canonicalize_plan_feature_id_uses_workflow_id() -> None:
    plan = PlanContract(
        feature_id="R-003",
        project="trade-research-platform",
        problem_statement="Implement roadmap item.",
        design_contract=DesignContract(acceptance_tests=["coverage"]),
        affected_surfaces=["app-api/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write failing tests.",
                allowed_paths=["app-api/src/test/java/"],
                forbidden_paths=["app-api/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[["./gradlew", ":app-api:test"]],
            )
        ],
        acceptance_test_matrix=["coverage"],
    )

    canonical = _canonicalize_plan_feature_id(plan, {"workflow_id": "wf_live"})

    assert canonical.feature_id == "wf_live"
    assert plan.feature_id == "R-003"
