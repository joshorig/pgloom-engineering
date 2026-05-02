from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult
from pgloom.tasks import enqueue_task

from pgloom_engineering.contract_store import (
    create_plan_contract,
    record_handoff,
    upsert_task_contract,
)
from pgloom_engineering.contracts import PlanContract, TaskContract
from pgloom_engineering.features import attach_task


class PlannerHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        raw_contract = payload.get("plan_contract")
        if not raw_contract:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_missing",
                blocker_reason="planner task requires a multi-agent council PlanContract",
                result={
                    "role": "planner",
                    "task_id": task.get("id"),
                    "requires_multi_agent_council": True,
                },
            )
        try:
            contract = PlanContract.model_validate(raw_contract)
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_invalid",
                blocker_reason=str(exc),
            )
        database_url = payload.get("database_url")
        plan_row = create_plan_contract(
            contract,
            planner_task_id=task.get("id"),
            database_url=database_url,
        )
        if plan_row["status"] != "valid":
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_invalid",
                blocker_reason="plan contract failed validation",
                result={
                    "plan_contract_id": plan_row["id"],
                    "errors": plan_row["validation_errors"],
                },
            )

        created: dict[str, str] = {}
        for task_slice in contract.task_slices:
            depends_on = [created[dep] for dep in task_slice.depends_on]
            child = enqueue_task(
                workflow_id=contract.feature_id,
                domain="engineering",
                task_type=task_slice.task_type,
                slot=task_slice.role,
                payload={
                    "feature_id": contract.feature_id,
                    "plan_contract_id": plan_row["id"],
                    "plan_contract_hash": plan_row["contract_hash"],
                    "task_slice_id": task_slice.slice_id,
                    "project": payload.get("project"),
                    "allow_unregistered_project": payload.get("allow_unregistered_project", False),
                    "requires_multi_agent_review": True,
                },
                depends_on=depends_on,
                database_url=database_url,
            )
            created[task_slice.slice_id] = child["id"]
            attach_task(
                contract.feature_id,
                child["id"],
                role=task_slice.role,
                database_url=database_url,
            )
            task_contract = TaskContract(
                feature_id=contract.feature_id,
                plan_contract_id=plan_row["id"],
                role=task_slice.role,
                task_type=task_slice.task_type,
                objective=task_slice.objective,
                inputs={"plan_contract_id": plan_row["id"], "task_slice_id": task_slice.slice_id},
                allowed_paths=task_slice.allowed_paths,
                forbidden_paths=task_slice.forbidden_paths,
                dependencies=depends_on,
                expected_outputs=task_slice.expected_outputs,
                verification_commands=task_slice.verification_commands,
                handoff_requirements=["produce TaskResultContract"],
            )
            upsert_task_contract(child["id"], task_contract, database_url=database_url)
            record_handoff(
                feature_id=contract.feature_id,
                from_task_id=task.get("id"),
                to_task_id=child["id"],
                handoff_type="plan_to_task",
                contract=task_contract.model_dump(mode="json"),
                database_url=database_url,
            )

        return HandlerResult.done(
            {
                "role": "planner",
                "task_id": task.get("id"),
                "plan_contract_id": plan_row["id"],
                "child_task_ids": list(created.values()),
                "planning": "multi_agent",
            }
        )
