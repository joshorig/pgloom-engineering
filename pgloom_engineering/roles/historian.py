from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult

from pgloom_engineering.role_gate_contracts import build_task_role_gate_contract


class HistorianHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        return HandlerResult.done(
            {
                "role": "historian",
                "task_id": task.get("id"),
                "role_gate_contract": build_task_role_gate_contract(role="historian"),
            }
        )
