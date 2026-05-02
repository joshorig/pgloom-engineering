from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult

from pgloom_engineering.contracts import TaskContract


class ImplementerHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        raw_contract = payload.get("task_contract")
        if raw_contract is not None:
            TaskContract.model_validate(raw_contract)
        return HandlerResult.done(
            {
                "role": "implementer",
                "task_id": task.get("id"),
                "implementation_topology": payload.get("implementation_topology", "project_policy"),
                "requires_task_result_contract": True,
            }
        )
