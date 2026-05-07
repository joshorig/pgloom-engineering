from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pgloom.states import TaskState
from pgloom.tasks import enqueue_task, transition_task

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import record_recovery_action
from pgloom_engineering.contracts import FeatureGoalContract, RecoveryDecisionContract
from pgloom_engineering.features import attach_task, get_feature_aggregate, update_feature_state
from pgloom_engineering.worker import run_once as run_engineering_worker_once

WorkerFn = Callable[..., dict[str, object]]

TERMINAL_STATES = {"done", "failed", "abandoned", "cancelled"}
HUMAN_GATE_STATES = {"awaiting_approval"}
BLOCKED_STATES = {"blocked"}
PREFERRED_SLOT_ORDER = [
    "planner",
    "qa-engineer",
    "implementer",
    "reviewer",
    "historian",
]


def run_workflow(
    feature_id: str,
    *,
    database_url: str | None = None,
    max_steps: int = 50,
    lease_seconds: int = 300,
    worker: WorkerFn = run_engineering_worker_once,
) -> dict[str, Any]:
    steps: list[dict[str, object]] = []
    for _ in range(max_steps):
        aggregate = get_feature_aggregate(feature_id, database_url=database_url)
        if aggregate is None:
            return {"status": "not_found", "feature_id": feature_id, "steps": steps}
        replan = _maybe_replan_blocked_feature(feature_id, aggregate, database_url)
        if replan is not None:
            steps.append(replan)
            continue
        terminal = _terminal_status(aggregate)
        if terminal is not None:
            _update_terminal_feature_state(feature_id, terminal["status"], database_url)
            terminal["steps"] = steps
            return terminal

        claimed = False
        for slot in _ready_slots(aggregate):
            result = worker(
                slot=slot,
                worker_id=f"pgloom-engineering-workflow-{slot}",
                lease_seconds=lease_seconds,
                database_url=database_url,
            )
            steps.append({"slot": slot, **result})
            if result.get("claimed"):
                claimed = True
                break
        if claimed:
            continue

        stalled = _stalled_status(feature_id, aggregate)
        stalled["steps"] = steps
        return stalled

    return {
        "status": "step_limit",
        "feature_id": feature_id,
        "max_steps": max_steps,
        "steps": steps,
    }


def _terminal_status(aggregate: dict[str, Any]) -> dict[str, Any] | None:
    feature = aggregate["feature"]
    tasks = list(aggregate.get("tasks") or [])
    feature_id = str(feature["id"])
    if not tasks:
        return None
    blocked = [
        task
        for task in tasks
        if str(task.get("state")) in BLOCKED_STATES
        and (task.get("blocker_code") or task.get("blocker_reason"))
    ]
    if blocked:
        return {
            "status": "blocked",
            "feature_id": feature_id,
            "blocked_task_ids": [str(task["id"]) for task in blocked],
        }
    human_gate = [task for task in tasks if str(task.get("state")) in HUMAN_GATE_STATES]
    if human_gate:
        return {
            "status": "human_gate",
            "feature_id": feature_id,
            "task_ids": [str(task["id"]) for task in human_gate],
        }
    if all(str(task.get("state")) in TERMINAL_STATES for task in tasks):
        failed = [task for task in tasks if str(task.get("state")) != "done"]
        if failed:
            return {
                "status": "failed",
                "feature_id": feature_id,
                "task_ids": [str(task["id"]) for task in failed],
            }
        return {"status": "done", "feature_id": feature_id}
    return None


def _ready_slots(aggregate: dict[str, Any]) -> list[str]:
    tasks = [
        task
        for task in aggregate.get("tasks") or []
        if str(task.get("state")) not in TERMINAL_STATES
        and str(task.get("state")) not in BLOCKED_STATES
        and str(task.get("state")) not in HUMAN_GATE_STATES
    ]
    slots = {str(task.get("slot")) for task in tasks if task.get("slot")}
    return sorted(slots, key=_slot_order)


def _slot_order(slot: str) -> tuple[int, str]:
    try:
        return (PREFERRED_SLOT_ORDER.index(slot), slot)
    except ValueError:
        return (len(PREFERRED_SLOT_ORDER), slot)


def _stalled_status(feature_id: str, aggregate: dict[str, Any]) -> dict[str, Any]:
    tasks = list(aggregate.get("tasks") or [])
    return {
        "status": "stalled",
        "feature_id": feature_id,
        "active_task_ids": [
            str(task["id"])
            for task in tasks
            if str(task.get("state")) not in TERMINAL_STATES
        ],
    }


def _update_terminal_feature_state(
    feature_id: str,
    status: str,
    database_url: str | None,
) -> None:
    feature_state = {
        "done": "ready_for_finalization",
        "human_gate": "awaiting_human",
        "blocked": "blocked",
        "failed": "failed",
    }.get(status)
    if feature_state is None:
        return
    update_feature_state(feature_id, state=feature_state, database_url=database_url)


def _maybe_replan_blocked_feature(
    feature_id: str,
    aggregate: dict[str, Any],
    database_url: str | None,
) -> dict[str, object] | None:
    settings = get_settings()
    if _active_planner_exists(aggregate):
        return None
    candidate = _recoverable_blocked_task(aggregate, settings)
    if candidate is None:
        return None
    payload = _replan_payload(feature_id, aggregate, candidate)
    if payload is None:
        return None

    planner = enqueue_task(
        workflow_id=feature_id,
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload=payload,
        priority=int(candidate.get("priority") or 0) + 1,
        max_attempts=3,
        database_url=database_url,
    )
    attach_task(feature_id, planner["id"], role="planner", database_url=database_url)
    _abandon_nonterminal_tasks(
        aggregate,
        exclude_task_ids={str(planner["id"])},
        database_url=database_url,
    )
    decision = RecoveryDecisionContract(
        feature_id=feature_id,
        task_id=str(candidate["id"]),
        blocker_code=str(candidate.get("blocker_code") or "engineering.blocked"),
        action="replan",
        rationale=str(payload["replan_context"]["summary"]),
        attempt=int(candidate.get("attempt") or 1),
        max_attempts=int(settings.workflow_replan_after_blocked_attempts),
    )
    record_recovery_action(
        decision,
        status="completed",
        outcome=f"enqueued planner replan task {planner['id']}",
        database_url=database_url,
    )
    return {
        "slot": "planner",
        "claimed": True,
        "status": "replan",
        "task_id": str(planner["id"]),
        "replanned_from_task_id": str(candidate["id"]),
    }


def _active_planner_exists(aggregate: dict[str, Any]) -> bool:
    for task in aggregate.get("tasks") or []:
        if str(task.get("task_type")) != "engineering.plan":
            continue
        if str(task.get("state")) in TERMINAL_STATES:
            continue
        return True
    return False


def _recoverable_blocked_task(
    aggregate: dict[str, Any],
    settings: Any,
) -> dict[str, Any] | None:
    recoverable_codes = set(settings.workflow_replan_blocker_codes)
    total_input_tokens = _total_model_input_tokens(aggregate)
    for task in aggregate.get("tasks") or []:
        state = str(task.get("state"))
        blocker_code = str(task.get("blocker_code") or "")
        if state != "blocked" or blocker_code not in recoverable_codes:
            continue
        attempt = int(task.get("attempt") or 1)
        if attempt >= int(settings.workflow_replan_after_blocked_attempts):
            return dict(task)
        if total_input_tokens >= int(settings.workflow_replan_after_input_tokens):
            return dict(task)
    return None


def _total_model_input_tokens(aggregate: dict[str, Any]) -> int:
    usage = aggregate.get("model_usage") or {}
    rows = usage.get("by_profile") if isinstance(usage, dict) else None
    if not isinstance(rows, list):
        return 0
    total = 0
    for row in rows:
        if isinstance(row, dict):
            total += int(row.get("input_tokens") or 0)
    return total


def _replan_payload(
    feature_id: str,
    aggregate: dict[str, Any],
    blocked_task: dict[str, Any],
) -> dict[str, Any] | None:
    feature = aggregate.get("feature") or {}
    metadata = feature.get("metadata") if isinstance(feature, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    raw_goal = metadata.get("feature_goal_contract")
    if not isinstance(raw_goal, dict):
        return None
    goal = FeatureGoalContract.model_validate(raw_goal)
    summary = _replan_summary(blocked_task)
    revised_goal = goal.model_copy(
        update={
            "requirements": _append_unique(goal.requirements, summary),
            "constraints": _append_unique(
                goal.constraints,
                (
                    "Planner replans must carry prior QA failure evidence into task slices, "
                    "split broad QA into implementer-ready slices, and require deterministic "
                    "self-validation before review."
                ),
            ),
        }
    )
    return {
        "feature_goal_contract": revised_goal.model_dump(mode="json"),
        "agent_topology": aggregate.get("agent_topology") or metadata.get("agent_topology"),
        "project": metadata.get("project"),
        "allow_unregistered_project": False,
        "requires_multi_agent_council": True,
        "replan_context": {
            "source": "workflow_driver",
            "blocked_task_id": str(blocked_task["id"]),
            "blocker_code": str(blocked_task.get("blocker_code") or ""),
            "blocker_reason": str(blocked_task.get("blocker_reason") or ""),
            "attempt": int(blocked_task.get("attempt") or 1),
            "summary": summary,
        },
    }


def _replan_summary(blocked_task: dict[str, Any]) -> str:
    blocker_code = str(blocked_task.get("blocker_code") or "engineering.blocked")
    blocker_reason = str(blocked_task.get("blocker_reason") or "No blocker reason recorded.")
    if blocker_code == "engineering.qa_semantic_quality_failed":
        return (
            "Previous QA author output failed semantic quality review. Replan with smaller QA "
            "author slices when endpoint coverage is broad, require project-approved HTTP "
            "harnesses such as MockMvc/WebTestClient/TestRestTemplate for route/query/status "
            f"acceptance, and preserve these failure details: {blocker_reason}"
        )
    if blocker_code == "engineering.qa_tests_do_not_compile":
        return (
            "Previous QA author output did not compile. Replan must include compile-first "
            "QA self-validation and line-level repair requirements before reviewer handoff: "
            f"{blocker_reason}"
        )
    if blocker_code == "engineering.qa_tests_not_red":
        return (
            "Previous QA author output could not prove behavioral red. Replan must use "
            "module-local focused red-proof commands and repair authored tests before broad "
            f"gates: {blocker_reason}"
        )
    return (
        "Previous autonomous workflow attempt blocked and needs a planner replan carrying "
        f"failure knowledge into implementer-ready QA and implementation slices. "
        f"{blocker_code}: {blocker_reason}"
    )


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


def _abandon_nonterminal_tasks(
    aggregate: dict[str, Any],
    *,
    exclude_task_ids: set[str],
    database_url: str | None,
) -> None:
    for task in aggregate.get("tasks") or []:
        task_id = str(task["id"])
        if task_id in exclude_task_ids:
            continue
        if str(task.get("state")) in TERMINAL_STATES:
            continue
        transition_task(
            task_id,
            TaskState.ABANDONED,
            message="Superseded by workflow recovery replan.",
            database_url=database_url,
        )
