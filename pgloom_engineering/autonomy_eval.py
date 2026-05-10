from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pgloom.tasks import get_task

from pgloom_engineering.features import get_feature_aggregate
from pgloom_engineering.handlers.registry import build_registry
from pgloom_engineering.worker import run_once

AUTONOMY_STEPS = [
    "engineering.plan",
    "engineering.qa.author",
    "engineering.implement",
    "engineering.review",
    "engineering.qa.verify.scrutiny",
    "engineering.qa.verify.usertest",
]


@dataclass
class AutonomyEvalStep:
    task_id: str
    task_type: str
    slot: str
    state: str
    worker_result: dict[str, object] | None = None


@dataclass
class AutonomyEvalReport:
    feature_id: str
    steps: list[AutonomyEvalStep] = field(default_factory=list)
    aggregate: dict[str, Any] | None = None

    @property
    def executed_task_types(self) -> list[str]:
        return [step.task_type for step in self.steps]


def run_autonomy_eval(
    feature_id: str,
    *,
    slots: list[str] | None = None,
    max_steps: int = 50,
    database_url: str | None = None,
) -> AutonomyEvalReport:
    """Drive a feature through the live worker runtime for deterministic evals.

    The harness intentionally calls `worker.run_once` with the production
    registry, so evals exercise the same pre-gates, role handlers, post-gates,
    telemetry recording, handoffs, and recovery paths as live dispatch.
    """

    registry = build_registry()
    report = AutonomyEvalReport(feature_id=feature_id)
    candidate_slots = slots or [
        "planner",
        "designer",
        "qa-engineer",
        "implementer",
        "reviewer",
        "qa-scrutiny",
        "qa-usertest",
        "historian",
    ]
    for index in range(max_steps):
        progressed = False
        for slot in candidate_slots:
            result = run_once(
                slot=slot,
                worker_id=f"autonomy-eval-{slot}-{index}",
                registry=registry,
                database_url=database_url,
            )
            if not result.get("claimed"):
                continue
            progressed = True
            task_id = str(result["task_id"])
            task = get_task(task_id, database_url=database_url)
            if task is not None:
                report.steps.append(
                    AutonomyEvalStep(
                        task_id=task_id,
                        task_type=str(task["task_type"]),
                        slot=str(task["slot"]),
                        state=str(task["state"]),
                        worker_result=result,
                    )
                )
        if not progressed:
            break
    report.aggregate = get_feature_aggregate(feature_id, database_url=database_url)
    return report


def assert_autonomy_eval_covered(report: AutonomyEvalReport) -> None:
    task_types = set(report.executed_task_types)
    missing = [step for step in AUTONOMY_STEPS if step not in task_types]
    if missing:
        raise AssertionError(f"autonomy eval did not execute steps: {missing}")
    if report.aggregate is None:
        raise AssertionError("autonomy eval did not produce a feature aggregate")
    worker_runs = report.aggregate.get("worker_runs")
    if not isinstance(worker_runs, list) or len(worker_runs) < len(AUTONOMY_STEPS):
        raise AssertionError("autonomy eval did not record worker telemetry for each step")
    handoffs = report.aggregate.get("handoffs")
    if not isinstance(handoffs, list) or not handoffs:
        raise AssertionError("autonomy eval did not record handoff evidence")
