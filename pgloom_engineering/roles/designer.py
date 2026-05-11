from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult

from pgloom_engineering.contract_store import get_active_plan_contract, get_task_contract
from pgloom_engineering.contracts import PlanContract, TaskContract
from pgloom_engineering.role_gate_contracts import build_task_role_gate_contract


class DesignerHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = dict(task.get("payload") or {})
        database_url = payload.get("database_url")
        task_id = str(task["id"])
        task_row = get_task_contract(task_id, database_url=database_url)
        if task_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.task_contract_missing",
                blocker_reason="designer requires a persisted TaskContract",
            )
        task_contract = TaskContract.model_validate(task_row["input_contract"])
        plan_row = get_active_plan_contract(task_contract.feature_id, database_url=database_url)
        if plan_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.active_plan_missing",
                blocker_reason="designer requires an active PlanContract",
            )
        plan = PlanContract.model_validate(plan_row["contract"])
        return HandlerResult.done(
            {
                "role": "designer",
                "task_id": task_id,
                "role_gate_contract": build_task_role_gate_contract(
                    role="designer",
                    plan=plan,
                    task_contract=task_contract,
                ),
                "design_contract": plan.design_contract.model_dump(mode="json"),
            }
        )
