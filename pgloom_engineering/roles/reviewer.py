from __future__ import annotations

from typing import Any

from pgloom.harness.result import HandlerResult

from pgloom_engineering.contracts import ReviewVerdictContract


class ReviewerHandler:
    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        raw_verdict = payload.get("review_verdict_contract")
        if raw_verdict is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.review_contract_missing",
                blocker_reason="review task requires a multi-agent ReviewVerdictContract",
                result={
                    "role": "reviewer",
                    "task_id": task.get("id"),
                    "requires_multi_agent_council": True,
                },
            )
        verdict = ReviewVerdictContract.model_validate(raw_verdict)
        return HandlerResult.done(
            {
                "role": "reviewer",
                "task_id": task.get("id"),
                "review": "multi_agent",
                "verdict": verdict.verdict,
            }
        )
