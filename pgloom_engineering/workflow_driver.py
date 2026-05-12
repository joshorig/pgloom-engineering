from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal

from pgloom.db.postgres import connect
from pgloom.states import TaskState
from pgloom.tasks import enqueue_task, transition_task

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import record_recovery_action
from pgloom_engineering.contracts import (
    FeatureGoalContract,
    PlanContract,
    RecoveryDecisionContract,
)
from pgloom_engineering.features import attach_task, get_feature_aggregate, update_feature_state
from pgloom_engineering.worker import run_once as run_engineering_worker_once

WorkerFn = Callable[..., dict[str, object]]

TERMINAL_STATES = {"done", "failed", "abandoned", "cancelled", "superseded"}
HUMAN_GATE_STATES = {"awaiting_approval"}
BLOCKED_STATES = {"blocked"}
PREFERRED_SLOT_ORDER = [
    "planner",
    "designer",
    "qa-engineer",
    "implementer",
    "reviewer",
    "qa-scrutiny",
    "qa-usertest",
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
        operator_replan = _maybe_consume_replan_from_milestone(
            feature_id,
            aggregate,
            database_url,
        )
        if operator_replan is not None:
            steps.append(operator_replan)
            continue
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
                feature_id=feature_id,
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
        failed = [
            task
            for task in tasks
            if str(task.get("state")) != "done"
            and not _terminal_task_superseded_by_recovery(task)
        ]
        if failed:
            return {
                "status": "failed",
                "feature_id": feature_id,
                "task_ids": [str(task["id"]) for task in failed],
            }
        return {"status": "done", "feature_id": feature_id}
    return None


def _terminal_task_superseded_by_recovery(task: dict[str, Any]) -> bool:
    state = str(task.get("state") or "")
    if state not in {"abandoned", "superseded"}:
        return False
    reason = str(task.get("terminal_reason") or "")
    return reason in {
        "workflow_recovery_replan",
        "operator_replan_from_milestone",
    }


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
    recovery_action: Literal["corrective_slice"] = "corrective_slice"
    decision = RecoveryDecisionContract(
        feature_id=feature_id,
        task_id=str(candidate["id"]),
        blocker_code=str(candidate.get("blocker_code") or "engineering.blocked"),
        action=recovery_action,
        rationale=str(payload["replan_context"]["summary"]),
        attempt=_recovery_attempt(candidate, aggregate),
        max_attempts=int(settings.workflow_replan_after_blocked_attempts),
    )
    record_recovery_action(
        decision,
        status="completed",
        outcome=f"enqueued corrective-slice planner task {planner['id']}",
        database_url=database_url,
    )
    return {
        "slot": "planner",
        "claimed": True,
        "status": recovery_action,
        "task_id": str(planner["id"]),
        "replanned_from_task_id": str(candidate["id"]),
    }


def _active_planner_exists(aggregate: dict[str, Any]) -> bool:
    for task in aggregate.get("tasks") or []:
        if str(task.get("task_type")) != "engineering.plan":
            continue
        if str(task.get("state")) in TERMINAL_STATES | BLOCKED_STATES:
            continue
        return True
    return False


def _recoverable_blocked_task(
    aggregate: dict[str, Any],
    settings: Any,
) -> dict[str, Any] | None:
    recoverable_codes = set(settings.workflow_replan_blocker_codes)
    immediate_codes = set(getattr(settings, "workflow_replan_immediate_blocker_codes", []))
    total_input_tokens = _total_model_input_tokens(aggregate)
    for task in aggregate.get("tasks") or []:
        state = str(task.get("state"))
        blocker_code = str(task.get("blocker_code") or "")
        if state != "blocked" or blocker_code not in recoverable_codes:
            continue
        completed_recoveries = _completed_recovery_count(aggregate, blocker_code)
        attempt = max(int(task.get("attempt") or 1), completed_recoveries + 1)
        if attempt > int(settings.workflow_replan_after_blocked_attempts):
            continue
        candidate = dict(task)
        candidate["recovery_attempt"] = attempt
        if blocker_code in immediate_codes:
            return candidate
        if attempt >= int(settings.workflow_replan_after_blocked_attempts):
            return candidate
        if total_input_tokens >= int(settings.workflow_replan_after_input_tokens):
            return candidate
    return None


def _maybe_consume_replan_from_milestone(
    feature_id: str,
    aggregate: dict[str, Any],
    database_url: str | None,
) -> dict[str, object] | None:
    if _active_planner_exists(aggregate):
        return None
    intervention = _next_replan_from_milestone_intervention(aggregate)
    if intervention is None:
        return None
    payload = _replan_from_milestone_payload(feature_id, aggregate, intervention)
    if payload is None:
        return None
    milestone_id = str(payload["replan_from_milestone_id"])
    task_ids_to_supersede = _task_ids_at_or_after_milestone(
        aggregate,
        milestone_id=milestone_id,
    )
    _supersede_tasks_for_operator_replan(
        task_ids_to_supersede,
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=feature_id,
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload=payload,
        priority=_next_planner_priority(aggregate),
        max_attempts=3,
        database_url=database_url,
    )
    attach_task(feature_id, planner["id"], role="planner", database_url=database_url)
    return {
        "slot": "planner",
        "claimed": True,
        "status": "replan_from_milestone",
        "task_id": str(planner["id"]),
        "operator_intervention_id": str(intervention["id"]),
        "replan_from_milestone_id": milestone_id,
        "superseded_task_ids": task_ids_to_supersede,
    }


def _next_replan_from_milestone_intervention(
    aggregate: dict[str, Any],
) -> dict[str, Any] | None:
    consumed_ids = _consumed_operator_intervention_ids(aggregate)
    for intervention in aggregate.get("operator_interventions") or []:
        if not isinstance(intervention, dict):
            continue
        if str(intervention.get("action_type") or "") != "replan_from_milestone":
            continue
        intervention_id = str(intervention.get("id") or "")
        if not intervention_id or intervention_id in consumed_ids:
            continue
        payload = intervention.get("payload")
        if not isinstance(payload, dict) or not payload.get("milestone_id"):
            continue
        return dict(intervention)
    return None


def _consumed_operator_intervention_ids(aggregate: dict[str, Any]) -> set[str]:
    consumed: set[str] = set()
    for task in aggregate.get("tasks") or []:
        payload = task.get("payload") if isinstance(task, dict) else None
        if not isinstance(payload, dict):
            continue
        intervention_id = payload.get("operator_intervention_id")
        if intervention_id is not None:
            consumed.add(str(intervention_id))
        context = payload.get("replan_context")
        if isinstance(context, dict) and context.get("operator_intervention_id") is not None:
            consumed.add(str(context["operator_intervention_id"]))
    return consumed


def _replan_from_milestone_payload(
    feature_id: str,
    aggregate: dict[str, Any],
    intervention: dict[str, Any],
) -> dict[str, Any] | None:
    feature = aggregate.get("feature") or {}
    metadata = feature.get("metadata") if isinstance(feature, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    raw_goal = metadata.get("feature_goal_contract")
    if not isinstance(raw_goal, dict):
        return None
    active_plan_row = aggregate.get("active_plan_contract")
    raw_plan = active_plan_row.get("contract") if isinstance(active_plan_row, dict) else None
    if not isinstance(raw_plan, dict):
        return None
    plan = PlanContract.model_validate(raw_plan)
    intervention_payload = intervention.get("payload")
    if not isinstance(intervention_payload, dict):
        return None
    milestone_id = str(intervention_payload.get("milestone_id") or "")
    if not milestone_id:
        return None
    milestone_ids = [milestone.milestone_id for milestone in plan.milestones]
    if milestone_id not in milestone_ids:
        return None
    frozen_slice_ids = _slice_ids_before_milestone(plan, milestone_id=milestone_id)
    frozen_task_ids = _task_ids_for_slice_ids(aggregate, frozen_slice_ids)
    at_after_slice_ids = _slice_ids_at_or_after_milestone(plan, milestone_id=milestone_id)
    reason = str(intervention_payload.get("reason") or "operator requested milestone replan")
    goal = FeatureGoalContract.model_validate(raw_goal)
    revised_goal = goal.model_copy(
        update={
            "requirements": _append_unique(
                goal.requirements,
                (
                    f"Operator requested replan from milestone {milestone_id}: {reason}. "
                    "Preserve every frozen-prefix slice byte-for-byte and replace only "
                    "the requested milestone and downstream work."
                ),
            )
        }
    )
    active_plan_id = (
        str(active_plan_row.get("id"))
        if isinstance(active_plan_row, dict) and active_plan_row.get("id")
        else None
    )
    return {
        "feature_goal_contract": revised_goal.model_dump(mode="json"),
        "agent_topology": aggregate.get("agent_topology") or metadata.get("agent_topology"),
        "project": metadata.get("project"),
        "allow_unregistered_project": False,
        "requires_multi_agent_council": True,
        "baseline_plan": plan.model_dump(mode="json"),
        "replan_from_milestone_id": milestone_id,
        "frozen_prefix_task_ids": frozen_task_ids,
        "operator_intervention_id": str(intervention["id"]),
        "replan_context": {
            "source": "workflow_driver",
            "mode": "replan_from_milestone",
            "operator_intervention_id": str(intervention["id"]),
            "active_plan_contract_id": active_plan_id,
            "replan_from_milestone_id": milestone_id,
            "frozen_prefix_task_ids": frozen_task_ids,
            "frozen_prefix_slice_ids": frozen_slice_ids,
            "replanned_slice_ids": at_after_slice_ids,
            "reason": reason,
            "summary": (
                f"Operator requested replan from milestone {milestone_id}. "
                "Frozen-prefix slices before that milestone must remain unchanged; "
                "replace only the requested milestone and downstream slices."
            ),
        },
    }


def _slice_ids_before_milestone(plan: PlanContract, *, milestone_id: str) -> list[str]:
    slice_ids: list[str] = []
    for milestone in plan.milestones:
        if milestone.milestone_id == milestone_id:
            break
        slice_ids.extend(milestone.slice_ids)
    return slice_ids


def _slice_ids_at_or_after_milestone(plan: PlanContract, *, milestone_id: str) -> list[str]:
    slice_ids: list[str] = []
    include = False
    for milestone in plan.milestones:
        if milestone.milestone_id == milestone_id:
            include = True
        if include:
            slice_ids.extend(milestone.slice_ids)
    return slice_ids


def _task_ids_for_slice_ids(
    aggregate: dict[str, Any],
    slice_ids: list[str],
) -> list[str]:
    wanted = set(slice_ids)
    task_ids: list[str] = []
    for row in aggregate.get("task_contracts") or []:
        if not isinstance(row, dict):
            continue
        slice_id = str(row.get("task_slice_id") or "")
        if not slice_id:
            input_contract = row.get("input_contract")
            if isinstance(input_contract, dict):
                inputs = input_contract.get("inputs")
                if isinstance(inputs, dict):
                    slice_id = str(inputs.get("task_slice_id") or "")
        if slice_id in wanted and row.get("task_id"):
            task_ids.append(str(row["task_id"]))
    return task_ids


def _task_ids_at_or_after_milestone(
    aggregate: dict[str, Any],
    *,
    milestone_id: str,
) -> list[str]:
    active_plan_row = aggregate.get("active_plan_contract")
    raw_plan = active_plan_row.get("contract") if isinstance(active_plan_row, dict) else None
    if not isinstance(raw_plan, dict):
        return []
    plan = PlanContract.model_validate(raw_plan)
    slice_ids = set(_slice_ids_at_or_after_milestone(plan, milestone_id=milestone_id))
    terminal_task_ids = {
        str(task["id"])
        for task in aggregate.get("tasks") or []
        if str(task.get("state")) in TERMINAL_STATES and task.get("id")
    }
    return [
        task_id
        for task_id in _task_ids_for_slice_ids(aggregate, list(slice_ids))
        if task_id not in terminal_task_ids
    ]


def _next_planner_priority(aggregate: dict[str, Any]) -> int:
    priorities = [
        int(task.get("priority") or 0)
        for task in aggregate.get("tasks") or []
        if isinstance(task, dict)
    ]
    return (max(priorities) if priorities else 0) + 1


def _supersede_tasks_for_operator_replan(
    task_ids: list[str],
    *,
    database_url: str | None,
) -> None:
    if not task_ids:
        return
    with connect(database_url) as conn, conn.transaction():
        conn.execute(
            """
            update tasks
            set state = 'superseded',
                terminal_reason = coalesce(terminal_reason, %s),
                terminal_detail = coalesce(terminal_detail, %s),
                updated_at = now()
            where id = any(%s)
              and state not in ('done', 'failed', 'abandoned', 'cancelled', 'superseded')
            """,
            (
                "operator_replan_from_milestone",
                "Superseded by operator replan-from-milestone intervention.",
                task_ids,
            ),
        )
        conn.execute(
            """
            update engineering_task_contracts
            set status = 'superseded',
                updated_at = now()
            where task_id = any(%s)
            """,
            (task_ids,),
        )


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
    repeat_count = _completed_recovery_count(
        aggregate,
        str(blocked_task.get("blocker_code") or ""),
    )
    blocked_contract = _task_contract_for_task(
        aggregate,
        str(blocked_task.get("id") or ""),
    )
    failure_context = _failure_context(blocked_task)
    benchmark_gate_classification = _benchmark_gate_classification(
        blocked_task,
        failure_context,
    )
    allocation_diagnosis = _benchmark_allocation_diagnosis(
        blocked_task,
        failure_context,
        classification=benchmark_gate_classification,
        repeat_count=repeat_count,
    )
    summary = _replan_summary(
        blocked_task,
        repeat_count=repeat_count,
        benchmark_gate_classification=benchmark_gate_classification,
        allocation_diagnosis=allocation_diagnosis,
    )
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
            "mode": "corrective_slice",
            "max_new_slices": 3,
            "blocked_task_id": str(blocked_task["id"]),
            "active_plan_contract_id": _active_plan_contract_id(aggregate),
            "blocker_code": str(blocked_task.get("blocker_code") or ""),
            "blocker_reason": str(blocked_task.get("blocker_reason") or ""),
            "failure_context": failure_context,
            "blocked_task_contract": blocked_contract,
            "blocked_slice_allowed_paths": _contract_string_list(
                blocked_contract,
                "allowed_paths",
            ),
            "blocked_slice_forbidden_paths": _contract_string_list(
                blocked_contract,
                "forbidden_paths",
            ),
            "blocked_slice_id": _blocked_slice_id(blocked_contract),
            "attempt": _recovery_attempt(blocked_task, aggregate),
            "same_blocker_recovery_count": repeat_count,
            "benchmark_gate_classification": benchmark_gate_classification,
            "benchmark_allocation_diagnosis": allocation_diagnosis,
            "summary": summary,
        },
    }


def _active_plan_contract_id(aggregate: dict[str, Any]) -> str | None:
    active = aggregate.get("active_plan_contract")
    if isinstance(active, dict) and active.get("id"):
        return str(active["id"])
    for row in aggregate.get("plan_contracts") or []:
        if isinstance(row, dict) and row.get("active") and row.get("id"):
            return str(row["id"])
    return None


def _completed_recovery_count(aggregate: dict[str, Any], blocker_code: str) -> int:
    if not blocker_code:
        return 0
    count = 0
    for action in aggregate.get("recovery_actions") or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("blocker_code") or "") != blocker_code:
            continue
        if str(action.get("action") or "") != "corrective_slice":
            continue
        if str(action.get("status") or "") != "completed":
            continue
        count += 1
    return count


def _recovery_attempt(
    blocked_task: dict[str, Any],
    aggregate: dict[str, Any],
) -> int:
    explicit = blocked_task.get("recovery_attempt")
    if explicit is not None:
        try:
            return max(1, int(explicit))
        except (TypeError, ValueError):
            pass
    blocker_code = str(blocked_task.get("blocker_code") or "")
    completed_recoveries = _completed_recovery_count(aggregate, blocker_code)
    task_attempt = int(blocked_task.get("attempt") or 1)
    return max(task_attempt, completed_recoveries + 1)


def _task_contract_for_task(
    aggregate: dict[str, Any],
    task_id: str,
) -> dict[str, Any] | None:
    if not task_id:
        return None
    for row in aggregate.get("task_contracts") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("task_id") or "") != task_id:
            continue
        contract = row.get("input_contract")
        return dict(contract) if isinstance(contract, dict) else None
    return None


def _contract_string_list(
    contract: dict[str, Any] | None,
    key: str,
) -> list[str]:
    if not isinstance(contract, dict):
        return []
    raw = contract.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def _blocked_slice_id(contract: dict[str, Any] | None) -> str | None:
    if not isinstance(contract, dict):
        return None
    inputs = contract.get("inputs")
    if isinstance(inputs, dict) and inputs.get("task_slice_id"):
        return str(inputs["task_slice_id"])
    if contract.get("task_slice_id"):
        return str(contract["task_slice_id"])
    return None


def _replan_summary(
    blocked_task: dict[str, Any],
    *,
    repeat_count: int = 0,
    benchmark_gate_classification: str | None = None,
    allocation_diagnosis: dict[str, Any] | None = None,
) -> str:
    blocker_code = str(blocked_task.get("blocker_code") or "engineering.blocked")
    blocker_reason = str(blocked_task.get("blocker_reason") or "No blocker reason recorded.")
    failure_context = _failure_context(blocked_task)
    detail = f" Failure evidence: {failure_context}" if failure_context else ""
    if blocker_code == "engineering.qa_semantic_quality_failed":
        if repeat_count >= 2:
            return (
                "Repeated QA semantic quality failures indicate the prior corrective "
                "plans are regenerating the same invalid QA-author shape. Replan must "
                "not emit another broad QA-author slice. Instead, preserve the exact "
                "semantic finding, emit a narrow QA harness repair slice that directly "
                "removes the rejected pattern, require typed project APIs rather than "
                "reflection/proxy/adapter shortcuts for benchmark harnesses, and rerun "
                "QA semantic review before implementation proceeds. Preserve these "
                f"failure details: {blocker_reason}{detail}"
            )
        return (
            "Previous QA author output failed semantic quality review. Replan with smaller QA "
            "author slices when coverage is broad, require project-approved public API or "
            "user-facing harnesses that directly address the semantic finding, and preserve "
            f"these failure details: {blocker_reason}{detail}"
        )
    if blocker_code == "engineering.qa_tests_do_not_compile":
        return (
            "Previous QA author output did not compile. Replan must include compile-first "
            "QA self-validation and line-level repair requirements before reviewer handoff: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.qa_tests_not_red":
        return (
            "Previous QA author output could not prove behavioral red. Replan must use "
            "module-local focused red-proof commands and repair authored tests before broad "
            f"gates: {blocker_reason}"
        )
    if blocker_code == "engineering.qa_path_violation":
        return (
            "Previous QA author output touched paths outside its task contract. Replan must "
            "repair the TaskContract path boundary so every expected QA output is explicitly "
            "allowed when project metadata authorizes it, or remove that output from the QA "
            f"slice. Preserve these path failure details: {blocker_reason}"
        )
    if blocker_code == "engineering.implementer_contract_invalid":
        return (
            "Previous implementer output was not a valid TaskResultContract after handler "
            "normalization. Replan must preserve the raw contract-validation details as "
            "repair input, emit the smallest corrective implementation or reporting slice "
            "needed, and require valid structured checks/commands_run before review: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.implementation_reported_blockers":
        return (
            "Previous implementation reported blockers instead of completing the slice. "
            "Replan must preserve the blocker text as repair input, emit the smallest "
            "corrective implementation slice that resolves or narrows the blocker, and "
            f"then rerun review and validators: {blocker_reason}{detail}"
        )
    if blocker_code == "engineering.implementation_path_violation":
        return (
            "Previous implementation touched paths outside its TaskContract. Replan must "
            "preserve the exact changed files and path-policy violations as repair input. "
            "If the violated paths are QA-owned tests, benchmarks, fixtures, or harness "
            "wiring, emit a QA-author repair slice with project-metadata-approved paths "
            "before any implementation slice; otherwise emit the narrowest production-code "
            f"repair slice with explicit allowed_paths. Details: {blocker_reason}{detail}"
        )
    if blocker_code == "engineering.review_rejected":
        review_context = f"{blocker_reason}{detail}"
        qa_owned = _review_rejection_mentions_qa_owned_surface(review_context)
        production_owned = _review_rejection_mentions_production_surface(review_context)
        if qa_owned and production_owned:
            return (
                "Previous reviewer verdict found mixed production API/implementation "
                "defects and QA-owned test or benchmark coverage gaps. Replan must "
                "preserve the production-code finding as implementation repair input, "
                "emit a narrow implementation repair slice, and include a QA-author "
                "repair slice only for the named project-metadata-approved test or "
                "benchmark paths. Do not treat public API or store implementation "
                "defects as QA-only harness work. Preserve these reviewer findings: "
                f"{blocker_reason}{detail}"
            )
        if qa_owned:
            return (
                "Previous reviewer verdict rejected QA-owned benchmark/test harness "
                "coverage. Replan must emit a narrow QA-author repair slice with "
                "project-metadata-approved benchmark/test paths, followed by review and "
                "split validators. Do not emit an implementation slice unless the reviewer "
                "finding explicitly names a production source defect under core/src/main "
                "or store/src/main. Preserve these reviewer findings: "
                f"{blocker_reason}{detail}"
            )
        return (
            "Previous reviewer verdict required coder repair. Replan must emit a narrow "
            "corrective implementation slice followed by review and validation, preserving "
            f"these reviewer findings: {blocker_reason}"
        )
    if blocker_code == "engineering.qa_verify_failed":
        return (
            "Previous QA scrutiny failed feature verification. Replan must emit targeted "
            "corrective implementation or QA-test repair slices, keep feature verification "
            "focused on lint/build, feature-specific tests, and benchmark smoke, then rerun "
            f"review and split validators. Preserve these QA findings: {blocker_reason}"
        )
    if blocker_code == "engineering.qa_usertest_failed":
        return (
            "Previous model-driven QA user-test found a product behavior failure. Replan "
            "must emit a targeted corrective implementation slice, preserve the user-test "
            "journey and evidence, then rerun review, QA scrutiny, and user-test: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.qa_usertest_contract_invalid":
        return (
            "Previous model-driven QA user-test output was not a valid QAResultContract "
            "after handler normalization. Replan must preserve the raw user-test output "
            "and schema error as repair input, keep the user-test model-driven rather than "
            "a deterministic command run, and require valid normalized validation evidence: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.implementation_verification_failed":
        if repeat_count >= 1 and "benchmark" in f"{blocker_reason} {detail}".lower():
            if benchmark_gate_classification == "material_allocation":
                diagnostic_summary = _allocation_diagnosis_summary(
                    allocation_diagnosis
                )
                return (
                    "Repeated implementer verification failure is blocked on a "
                    "material benchmark-smoke allocation failure, not a QA threshold "
                    "repair. Replan must consume benchmark_allocation_diagnosis before "
                    "choosing corrective work. If the diagnosis is inconclusive, emit a "
                    "diagnostic QA-scrutiny/performance slice that profiles only the "
                    "failing benchmark and writes an AllocationDiagnosisContract. If the "
                    "diagnosis is sufficient, emit exactly one narrow implementation "
                    "slice that names the measured allocation signal and suspected "
                    "hot-path allocation source; do not emit a QA-author repair slice "
                    "unless new evidence names an invalid benchmark harness, missing "
                    "benchmark result, or metadata-disallowed threshold. "
                    f"{diagnostic_summary} Preserve these verification details: "
                    f"{blocker_reason}{detail}"
                )
            if benchmark_gate_classification in {"near_threshold", "qa_harness"}:
                return (
                    "Repeated implementer verification failure is blocked on a "
                    f"{benchmark_gate_classification.replace('_', '-')} benchmark-smoke "
                    "gate. Replan must first repair or justify the QA-owned benchmark "
                    "harness, threshold, operations-per-invocation, or fixture setup "
                    "using project-metadata-approved benchmark paths. Do not broaden "
                    "production implementation work unless the repaired gate still "
                    "shows material allocation. Preserve these verification details: "
                    f"{blocker_reason}{detail}"
                )
            return (
                "Repeated implementer verification failure is still blocked on a "
                "benchmark-smoke allocation gate. Replan must stop regenerating broad "
                "implementation repairs. First inspect whether the benchmark harness, "
                "threshold, operations-per-invocation, or fixture setup is invalid; if "
                "the failure is QA-owned harness work, emit a QA-author benchmark repair "
                "slice using project-metadata-approved benchmark paths. If it is truly "
                "production allocation, emit one narrow implementation slice naming the "
                "exact hot-path allocation source. All downstream verification commands "
                "must reference only QA-authored test classes/methods that already exist "
                "unless a QA-author repair slice creates new ones. Preserve these "
                f"verification details: {blocker_reason}{detail}"
            )
        return (
            "Previous implementer verification failed. Replan must inspect whether the "
            "failure is production-code behavior or QA-owned test/benchmark harness "
            "invalidity; emit only the narrow corrective slice needed, then rerun review "
            "and split validators. If the corrective plan does not include a QA-author "
            "repair slice, every verification command in review, QA scrutiny, and QA "
            "user-test must reference only QA-authored test classes/methods that already "
            "exist in the active worktree or prior QA handoff. Do not invent replacement "
            "test classes such as generic conformance names during implementation-only "
            "recovery. Preserve these verification details: "
            f"{blocker_reason}"
            f"{detail}"
        )
    if blocker_code == "engineering.planner_council_exhausted":
        return (
            "Previous planner council exhausted before producing an accepted plan. Replan "
            "must use the prior critic findings and invalid proposals as inputs, reduce "
            "the feature into smaller milestone/slice contracts, avoid adding stricter "
            f"rubric requirements, and preserve these planner details: {blocker_reason}{detail}"
        )
    if blocker_code == "engineering.plan_contract_invalid":
        if "variant_slice_uses_broad_conformance_gate" in failure_context:
            return (
                "Previous planner output failed post-normalization production-grade "
                "validation because a variant-scoped implementer slice used a broad "
                "conformance/test gate. Replan must preserve the feature scope and "
                "repair only the invalid verification shape: either merge the variant "
                "implementation work into one implementer slice that may run the broad "
                "feature conformance command, or keep variant slices and give each "
                "variant slice method/class filters that prove only that slice's "
                "SINGLE/DOUBLE/direct/mmap responsibility. Do not emit broad class-level "
                "variant conformance commands from variant-scoped implementer slices. "
                f"Preserve these validation details: {blocker_reason}{detail}"
            )
        if "hot_path_implementation_surface_missing" in failure_context:
            return (
                "Previous planner output failed post-normalization production-grade "
                "validation because hot-path shared API implementation/delegating "
                "source paths were omitted. Replan must preserve the feature scope and "
                "add the exact source paths named in the validation evidence to the "
                "implementer/reviewer coverage without dropping sibling concrete "
                "implementations or variants. Do not satisfy this by mentioning generic "
                "wrappers only; task allowed_paths/objectives must include the named "
                f"production paths. Preserve these validation details: {blocker_reason}{detail}"
            )
        return (
            "Previous planner output failed PlanContract or post-normalization "
            "production-grade validation. Replan must preserve the schema and validation "
            "errors as inputs, repair only the invalid contract shape, keep the feature "
            "scope stable, and avoid changing unrelated slices: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.qa_handoff_missing":
        return (
            "Previous implementer task had no QA author handoff. Replan must restore the "
            "planner DAG dependency from QA author to implementer or emit a QA-author repair "
            "slice before any implementer slice, then rerun review and validation: "
            f"{blocker_reason}{detail}"
        )
    if blocker_code == "engineering.handoff_missing":
        return (
            "Previous downstream task had no producer handoff. Replan must emit the missing "
            "upstream producer slice before retrying the blocked downstream role, preserve "
            "existing QA-authored tests and support artifacts, and rerun review plus split "
            f"validators only after a valid task_result handoff exists: {blocker_reason}{detail}"
        )
    if blocker_code == "engineering.invalid_handler_output":
        return (
            "Previous handler result could not be persisted as the expected output contract. "
            "Replan must preserve the persistence/schema error and raw handler result as "
            "repair input, then emit the smallest corrective slice that produces a valid "
            f"normalized contract before downstream validation: {blocker_reason}{detail}"
        )
    return (
        "Previous autonomous workflow attempt blocked and needs a planner replan carrying "
        f"failure knowledge into implementer-ready QA and implementation slices. "
        f"{blocker_code}: {blocker_reason}{detail}"
    )


def _review_rejection_mentions_qa_owned_surface(context_text: str) -> bool:
    lowered = context_text.lower()
    return any(
        signal in lowered
        for signal in [
            "benchmarks/src/jmh",
            "benchmarks/build.gradle",
            "conformance-tests/src/test",
            "core/src/test",
            "store/src/test",
            "benchmark-smoke",
            "qa-authored",
        ]
    )


def _review_rejection_mentions_production_surface(context_text: str) -> bool:
    lowered = context_text.lower()
    return any(
        signal in lowered
        for signal in [
            "core/src/main",
            "store/src/main",
            "public prefix overload",
            "required public api",
            "required byte[]",
            "not implemented",
            "api shape",
            "production code",
            "production-code",
        ]
    )


def _failure_context(blocked_task: dict[str, Any]) -> str:
    result = blocked_task.get("result")
    if not isinstance(result, dict):
        return ""
    excerpts: list[str] = []
    for key in ("stderr_excerpt", "stdout_excerpt", "failure_excerpt"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            excerpts.append(" ".join(value.split()))
    for command in result.get("commands") or []:
        if isinstance(command, list):
            rendered = " ".join(str(part) for part in command)
            if rendered:
                excerpts.append(f"command={rendered}")
    artifact_hints = result.get("artifact_hints")
    if isinstance(artifact_hints, dict):
        rendered_hints = _compact_artifact_hints(artifact_hints)
        if rendered_hints:
            excerpts.append(f"artifact_hints={rendered_hints}")
    changed_files = result.get("changed_files")
    if isinstance(changed_files, list):
        rendered_files = [
            str(path)
            for path in changed_files[:20]
            if isinstance(path, str) and path.strip()
        ]
        if rendered_files:
            excerpts.append(f"changed_files={', '.join(rendered_files)}")
    violations = result.get("violations")
    if isinstance(violations, list):
        rendered_violations: list[str] = []
        for violation in violations[:20]:
            if not isinstance(violation, dict):
                continue
            path = violation.get("path")
            reason = violation.get("reason")
            if isinstance(path, str) and path.strip():
                rendered_violations.append(
                    f"{path}:{reason}" if isinstance(reason, str) else path
                )
        if rendered_violations:
            excerpts.append(f"path_violations={', '.join(rendered_violations)}")
    findings = result.get("findings")
    if isinstance(findings, list):
        rendered_findings: list[str] = []
        for finding in findings[:20]:
            rendered = _compact_validation_item(finding)
            if rendered:
                rendered_findings.append(rendered)
        if rendered_findings:
            excerpts.append(f"findings={'; '.join(rendered_findings)}")
    errors = result.get("errors")
    if isinstance(errors, list):
        rendered_errors = [
            rendered
            for item in errors[:20]
            if (rendered := _compact_validation_item(item))
        ]
        if rendered_errors:
            excerpts.append(f"errors={'; '.join(rendered_errors)}")
    contract_excerpts = _result_contract_excerpts(result)
    if contract_excerpts:
        excerpts.extend(contract_excerpts)
    return " | ".join(excerpts)[:3000]


def _compact_validation_item(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    code = str(item.get("code") or "").strip()
    path = str(item.get("file") or item.get("path") or "").strip()
    slice_id = str(item.get("slice_id") or "").strip()
    line = item.get("line")
    message = str(item.get("message") or "").strip()
    location = path or slice_id
    if path and line is not None:
        location = f"{path}:{line}"
    parts = [part for part in (code, location, message) if part]
    return " - ".join(parts)


def _result_contract_excerpts(result: dict[str, Any]) -> list[str]:
    excerpts: list[str] = []
    for key in ("qa_result_contract", "review_verdict_contract", "task_result_contract"):
        contract = result.get(key)
        if not isinstance(contract, dict):
            continue
        rendered = _contract_evidence_excerpt(contract)
        if rendered:
            excerpts.append(f"{key}={rendered}")
    return excerpts


def _contract_evidence_excerpt(contract: dict[str, Any]) -> str:
    parts: list[str] = []
    findings = contract.get("findings")
    if isinstance(findings, list):
        rendered_findings = [
            _compact_contract_item(item)
            for item in findings[:8]
            if _compact_contract_item(item)
        ]
        if rendered_findings:
            parts.append("findings=" + "; ".join(rendered_findings))
    evidence = contract.get("evidence")
    if isinstance(evidence, list):
        rendered_items = [
            _compact_contract_item(item)
            for item in evidence[:6]
            if _compact_contract_item(item)
        ]
        if rendered_items:
            parts.append("evidence=" + "; ".join(rendered_items))
    validation_evidence = contract.get("validation_evidence")
    if isinstance(validation_evidence, list):
        rendered_evidence: list[str] = []
        for item in validation_evidence[:6]:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            metadata = item.get("metadata")
            allocation = None
            if isinstance(metadata, dict):
                allocation = metadata.get("benchmark_allocation_diagnosis") or metadata.get(
                    "allocation_diagnosis"
                )
            rendered_summary = summary
            if isinstance(allocation, dict):
                rendered_summary = " ".join(
                    part
                    for part in (
                        rendered_summary,
                        "allocation_diagnosis="
                        + json.dumps(allocation, sort_keys=True, default=str),
                    )
                    if part
                )
            if rendered_summary:
                rendered_evidence.append(" ".join(rendered_summary.split())[:800])
        if rendered_evidence:
            parts.append("validation_evidence=" + "; ".join(rendered_evidence))
    return " ".join(parts)[:1800]


def _compact_contract_item(item: Any) -> str:
    if isinstance(item, str):
        return " ".join(item.split())[:500]
    if isinstance(item, dict):
        message = item.get("message") or item.get("summary") or item.get("code")
        if message:
            return " ".join(str(message).split())[:500]
        return json.dumps(item, sort_keys=True, default=str)[:500]
    return ""


def _benchmark_gate_classification(
    blocked_task: dict[str, Any],
    failure_context: str,
) -> str | None:
    if str(blocked_task.get("blocker_code") or "") not in {
        "engineering.implementation_verification_failed",
        "engineering.qa_verify_failed",
    }:
        return None
    text = " ".join(
        part
        for part in (
            str(blocked_task.get("blocker_reason") or ""),
            failure_context,
        )
        if part
    ).lower()
    if not _mentions_benchmark_gate(text):
        return None
    if any(
        signal in text
        for signal in (
            "missing smoke benchmark result",
            "no matching benchmarks",
            "miss-spelled regexp",
            "wrongmethodtypeexception",
            "classnotfoundexception",
            "invalid benchmark",
            "benchmark harness",
            "metadata-disallowed threshold",
        )
    ):
        return "qa_harness"
    if _benchmark_context_mentions_source_allocation(text):
        return "material_allocation"
    b_op_values = _benchmark_b_op_values(text)
    if b_op_values and max(b_op_values) > 0.005:
        return "material_allocation"
    if b_op_values and max(b_op_values) <= 0.005:
        return "near_threshold"
    return "unknown"


def _benchmark_allocation_diagnosis(
    blocked_task: dict[str, Any],
    failure_context: str,
    *,
    classification: str | None,
    repeat_count: int,
) -> dict[str, Any] | None:
    if classification is None:
        return None
    text = " ".join(
        part
        for part in (
            str(blocked_task.get("blocker_reason") or ""),
            failure_context,
        )
        if part
    )
    threshold = _benchmark_threshold_b_op(text)
    failures = _benchmark_allocation_failures(text, threshold=threshold)
    max_b_op = max((item["b_op"] for item in failures), default=None)
    source_allocation_known = _benchmark_context_mentions_source_allocation(text.lower())
    blocker_code = str(blocked_task.get("blocker_code") or "")
    diagnostic_required = bool(
        classification == "material_allocation"
        and not source_allocation_known
        and blocker_code
        not in {
            "engineering.implementation_verification_failed",
            "engineering.qa_verify_failed",
        }
    )
    recommended_owner = _benchmark_diagnosis_owner(classification, failures)
    if diagnostic_required:
        recommended_owner = "diagnostic"
    elif classification == "material_allocation" and (
        source_allocation_known
        or blocker_code
        in {
            "engineering.implementation_verification_failed",
            "engineering.qa_verify_failed",
        }
    ):
        recommended_owner = "implementer"
    diagnosis: dict[str, Any] = {
        "contract_type": "AllocationDiagnosisContract",
        "classification": classification,
        "threshold_b_op": threshold,
        "max_b_op": max_b_op,
        "failing_benchmarks": failures[:20],
        "repeat_count": repeat_count,
        "recommended_owner": recommended_owner,
        "source_allocation_known": source_allocation_known,
        "diagnostic_required": diagnostic_required,
        "evidence_source": "workflow_driver.failure_context",
    }
    if classification == "material_allocation":
        diagnosis["repair_directive"] = (
            "Do not relax thresholds or route to QA-author unless diagnostic evidence "
            "proves a harness defect. Route material allocation failures from "
            "implementation or verification gates back to implementer-owned hot-path "
            "repair, and require the implementer to identify the allocation source for "
            "the listed benchmark/mode/variant tuples while rerunning the same gate."
        )
    elif classification == "qa_harness":
        directive = (
            "Repair benchmark harness/result discovery under project-approved QA paths "
            "before assigning more production implementation work."
        )
        if "no matching benchmarks" in text.lower() or "miss-spelled regexp" in text.lower():
            directive += (
                " The failure says the benchmark runner discovered no matching "
                "benchmarks, so the corrective QA slice must fix the benchmark include "
                "regex or JMH discovery pattern to match the generated fully qualified "
                "benchmark class/method names; do not route this to production source "
                "or allocation repair until the benchmark is actually discovered and run."
            )
        diagnosis["repair_directive"] = directive
    else:
        diagnosis["repair_directive"] = (
            "Run a focused benchmark allocation diagnostic before choosing QA or "
            "implementation ownership."
        )
    return diagnosis


def _allocation_diagnosis_summary(diagnosis: dict[str, Any] | None) -> str:
    if not diagnosis:
        return ""
    failures = diagnosis.get("failing_benchmarks")
    rendered: list[str] = []
    if isinstance(failures, list):
        for item in failures[:6]:
            if not isinstance(item, dict):
                continue
            benchmark = item.get("benchmark")
            b_op = item.get("b_op")
            threshold = diagnosis.get("threshold_b_op")
            if benchmark and b_op is not None:
                rendered.append(f"{benchmark}={b_op} B/op over {threshold} B/op")
    if not rendered:
        return ""
    owner = diagnosis.get("recommended_owner")
    diagnostic_required = diagnosis.get("diagnostic_required")
    return (
        "Allocation diagnosis: "
        + "; ".join(rendered)
        + f". Recommended owner={owner}; diagnostic_required={diagnostic_required}."
    )


def _benchmark_threshold_b_op(text: str) -> float:
    threshold_values = [
        float(match.group(1))
        for match in re.finditer(
            r"(?:threshold|above)\s+([0-9]+(?:\.[0-9]+)?)\s*b/op",
            text,
            flags=re.IGNORECASE,
        )
    ]
    return min(threshold_values) if threshold_values else 0.005


def _benchmark_allocation_failures(
    text: str,
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<benchmark>[A-Za-z0-9_.$:-]*(?:Benchmark|benchmark)[A-Za-z0-9_.$:-]*)"
        r"[^|\\n;]*?allocated\s+(?P<bop>[0-9]+(?:\.[0-9]+)?)\s*B/op",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        b_op = float(match.group("bop"))
        if b_op <= threshold:
            continue
        benchmark = match.group("benchmark").strip(" .:-")
        if not benchmark:
            benchmark = "unknown"
        failures.append(
            {
                "benchmark": benchmark,
                "b_op": b_op,
                "threshold_b_op": threshold,
                "severity": (
                    "material" if b_op >= max(0.5, threshold * 100) else "near_threshold"
                ),
            }
        )
    if failures:
        return failures
    for value in _benchmark_b_op_values(text):
        if value > threshold:
            failures.append(
                {
                    "benchmark": "unknown",
                    "b_op": value,
                    "threshold_b_op": threshold,
                    "severity": (
                        "material"
                        if value >= max(0.5, threshold * 100)
                        else "near_threshold"
                    ),
                }
            )
    return failures


def _benchmark_diagnosis_owner(
    classification: str,
    failures: list[dict[str, Any]],
) -> str:
    if classification == "qa_harness":
        return "qa-author"
    if classification != "material_allocation":
        return "diagnostic"
    if not failures:
        return "diagnostic"
    if any(str(item.get("severity")) == "material" for item in failures):
        return "implementer"
    return "diagnostic"


def _benchmark_b_op_values(text: str) -> list[float]:
    return [
        float(match.group(1))
        for match in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*b/op", text)
    ]


def _benchmark_context_mentions_source_allocation(text: str) -> bool:
    return any(
        signal in text
        for signal in (
            "bytebuffer.allocate",
            "new byte[",
            "new object",
            "proxy.newproxyinstance",
            "invocationhandler",
            "arrays.copyof",
            "stream()",
            ".iterator()",
            "source-level allocation",
            "hot-path allocation source",
        )
    )


def _mentions_benchmark_gate(text: str) -> bool:
    return any(
        signal in text
        for signal in (
            "jmhsmokecheck",
            "benchmark_smoke_diagnostic",
            "benchmark smoke",
            "allocated",
            "b/op",
            "allocation threshold",
        )
    )


def _compact_artifact_hints(hints: dict[str, Any]) -> str:
    parts: list[str] = []
    failure_lines = hints.get("failure_output_lines")
    if isinstance(failure_lines, list):
        rendered_lines = [
            " ".join(str(line).split())
            for line in failure_lines[:6]
            if isinstance(line, str) and line.strip()
        ]
        if rendered_lines:
            parts.append("failure_output_lines=" + " ; ".join(rendered_lines))
    gradle_failures = hints.get("gradle_test_failures")
    if isinstance(gradle_failures, list):
        rendered_failures: list[str] = []
        for failure in gradle_failures[:6]:
            if not isinstance(failure, dict):
                continue
            test = failure.get("test") or failure.get("suite") or failure.get("path")
            message = failure.get("message") or failure.get("type")
            if isinstance(test, str) and test.strip():
                rendered_failures.append(
                    f"{test}:{message}" if isinstance(message, str) else test
                )
        if rendered_failures:
            parts.append("gradle_test_failures=" + " ; ".join(rendered_failures))
    benchmark = hints.get("benchmark_smoke_diagnostic")
    if isinstance(benchmark, str) and benchmark.strip():
        parts.append("benchmark_smoke_diagnostic=" + " ".join(benchmark.split()))
    return " | ".join(parts)[:1400]


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
        _record_task_terminal_reason(
            task_id,
            reason="workflow_recovery_replan",
            detail="Superseded by workflow recovery replan.",
            database_url=database_url,
        )


def _record_task_terminal_reason(
    task_id: str,
    *,
    reason: str,
    detail: str | None = None,
    database_url: str | None,
) -> None:
    try:
        with connect(database_url) as conn, conn.transaction():
            conn.execute(
                """
                update tasks
                set terminal_reason = coalesce(terminal_reason, %s::text),
                    terminal_detail = coalesce(terminal_detail, %s::text),
                    updated_at = now()
                where id = %s
                """,
                (reason, detail, task_id),
            )
    except Exception:
        return
