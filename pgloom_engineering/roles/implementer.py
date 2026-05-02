from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult


class ImplementerHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        return HandlerResult.done({"role": "implementer", "task_id": task.get("id")})
