from __future__ import annotations

from typing import Any

from pgloom_engineering.contracts import DesignContract, PlanContract, TaskContract
from pgloom_engineering.roles.designer import DesignerHandler


def test_designer_returns_active_plan_design_contract(monkeypatch: Any) -> None:
    plan = PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Design a small change.",
        design_contract=DesignContract(public_api="GET /api/demo", acceptance_tests=["red"]),
        affected_surfaces=["app-api/"],
        task_slices=[],
        acceptance_test_matrix=["red"],
    )
    task_contract = TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="designer",
        task_type="engineering.design",
        objective="Lock design.",
        allowed_paths=["docs/"],
        forbidden_paths=["src/"],
        expected_outputs=["DesignContract"],
        verification_commands=[["./qa/smoke.sh"]],
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.designer.get_task_contract",
        lambda *args, **kwargs: {"input_contract": task_contract.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        "pgloom_engineering.roles.designer.get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )

    result = DesignerHandler().handle(
        {
            "id": "design-task-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.design",
            "payload": {"database_url": None},
        }
    )

    assert result.status == "done"
    assert result.result["role_gate_contract"]["contract_version"] == (
        "engineering.role_gate_contract.v1"
    )
    assert result.result["role_gate_contract"]["role"] == "designer"
    assert result.result == {
        "role": "designer",
        "task_id": "design-task-1",
        "role_gate_contract": result.result["role_gate_contract"],
        "design_contract": {
            "acceptance_tests": ["red"],
            "concurrency_protocol": "",
            "forbidden_alternatives": [],
            "hard_constraints": [],
            "ownership_boundaries": "",
            "persistence_protocol": "",
            "public_api": "GET /api/demo",
        },
    }
