from __future__ import annotations

import traceback
from typing import Any, cast

from pgloom.harness.registry import HandlerRegistry
from pgloom.harness.result import HandlerResult
from pgloom.states import TaskState
from pgloom.tasks import claim_next, retry_or_fail_task, transition_task

from pgloom_engineering.contract_store import (
    get_active_plan_contract,
    get_task_contract,
    list_task_handoffs,
    record_recovery_action,
    upsert_task_contract,
)
from pgloom_engineering.contracts import (
    QAAuthorContract,
    QAResultContract,
    RecoveryDecisionContract,
    ReviewVerdictContract,
    TaskResultContract,
)
from pgloom_engineering.features import get_feature
from pgloom_engineering.handlers.registry import build_registry
from pgloom_engineering.projects import get_project


def run_once(
    *,
    slot: str,
    worker_id: str,
    registry: HandlerRegistry | None = None,
    database_url: str | None = None,
    lease_seconds: int = 300,
) -> dict[str, object]:
    task = claim_next(
        slot=slot,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        database_url=database_url,
    )
    if not task:
        return {"claimed": False}

    task = dict(task)
    payload = dict(task.get("payload") or {})
    payload["database_url"] = database_url
    task["payload"] = payload

    gate_result = _pre_execution_gate(task, database_url=database_url)
    if gate_result is not None:
        transition_task(
            task["id"],
            TaskState.BLOCKED,
            blocker_code=gate_result.blocker_code,
            blocker_reason=gate_result.blocker_reason,
            result=gate_result.result,
            database_url=database_url,
        )
        return {"claimed": True, "task_id": task["id"], "status": "blocked"}

    transition_task(task["id"], TaskState.RUNNING, database_url=database_url)
    try:
        handler = (registry or build_registry()).get(task["task_type"])
        result = handler.handle(task)
        result = _post_execution_gate(task, result, database_url=database_url)
    except Exception as exc:
        _record_recovery(
            task,
            blocker_code="engineering.worker_crash",
            action="record_crash",
            rationale=str(exc),
            outcome=traceback.format_exc(limit=8),
            status="open",
            database_url=database_url,
        )
        retry_or_fail_task(task["id"], reason=f"worker crash: {exc}", database_url=database_url)
        return {"claimed": True, "task_id": task["id"], "status": "retry"}

    if result.status == "done":
        transition_task(task["id"], TaskState.DONE, result=result.result, database_url=database_url)
    elif result.status == "blocked":
        transition_task(
            task["id"],
            TaskState.BLOCKED,
            blocker_code=result.blocker_code or "engineering.handler_blocked",
            blocker_reason=result.blocker_reason or result.message,
            result=result.result,
            database_url=database_url,
        )
    elif result.status == "retry":
        retry_or_fail_task(
            task["id"],
            reason=result.message or "handler requested retry",
            database_url=database_url,
        )
    else:
        transition_task(task["id"], TaskState.AWAITING_APPROVAL, database_url=database_url)
    return {"claimed": True, "task_id": task["id"], "status": result.status}


def _pre_execution_gate(
    task: dict[str, Any], *, database_url: str | None
) -> HandlerResult | None:
    payload = dict(task.get("payload") or {})
    feature_id = str(payload.get("feature_id") or task.get("workflow_id"))
    feature = get_feature(feature_id, database_url=database_url)
    project_name = _project_name(task, feature)
    if not project_name:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.project_unresolved",
            action="block_execution",
            rationale="Task does not identify an engineering project.",
            database_url=database_url,
        )

    project = get_project(project_name, database_url=database_url)
    if project is None and not payload.get("allow_unregistered_project"):
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.project_unregistered",
            action="block_execution",
            rationale=f"Project is not registered: {project_name}",
            database_url=database_url,
        )
    if project is not None and project.state != "active":
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.project_disabled",
            action="block_execution",
            rationale=f"Project {project_name} is {project.state}.",
            database_url=database_url,
        )

    if task["task_type"] == "engineering.plan":
        return None

    active_plan = get_active_plan_contract(feature_id, database_url=database_url)
    task_contract = get_task_contract(task["id"], database_url=database_url)
    if task_contract is None:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.task_contract_missing",
            action="block_execution",
            rationale="Non-planner task has no persisted TaskContract.",
            database_url=database_url,
        )
    if active_plan is None:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.active_plan_missing",
            action="block_execution",
            rationale="Non-planner task has no active feature plan.",
            database_url=database_url,
        )
    if task_contract["plan_contract_id"] != active_plan["id"]:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.stale_task_contract",
            action="retire_superseded",
            rationale=(
                f"Task contract targets {task_contract['plan_contract_id']} "
                f"but active plan is {active_plan['id']}."
            ),
            database_url=database_url,
        )
    if _requires_handoff(task) and not list_task_handoffs(
        task["id"], handoff_type="task_result", database_url=database_url
    ):
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.handoff_missing",
            action="block_execution",
            rationale="Review/QA task requires a producer task_result handoff.",
            database_url=database_url,
        )
    return None


def _post_execution_gate(
    task: dict[str, Any],
    result: HandlerResult,
    *,
    database_url: str | None,
) -> HandlerResult:
    if result.status != "done":
        return result
    feature_id = str((task.get("payload") or {}).get("feature_id") or task.get("workflow_id"))
    try:
        if task["task_type"] == "engineering.implement":
            output = result.result.get("task_result_contract")
            if not output:
                raise ValueError("implementer result missing task_result_contract")
            contract = TaskResultContract.model_validate(output)
            row = get_task_contract(task["id"], database_url=database_url)
            if row is not None:
                upsert_task_contract(
                    task["id"],
                    _task_contract_from_row(row),
                    output_contract=contract.model_dump(mode="json"),
                    status="completed",
                    database_url=database_url,
                )
        elif task["task_type"] == "engineering.review":
            ReviewVerdictContract.model_validate(result.result.get("review_verdict_contract"))
        elif task["task_type"] == "engineering.qa.author":
            QAAuthorContract.model_validate(result.result.get("qa_author_contract"))
        elif task["task_type"] in {"engineering.qa", "engineering.qa.verify"}:
            QAResultContract.model_validate(result.result.get("qa_result_contract"))
    except Exception as exc:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.invalid_handler_output",
            action="record_invalid_output",
            rationale=str(exc),
            database_url=database_url,
        )
    return result


def _task_contract_from_row(row: dict[str, Any]) -> Any:
    from pgloom_engineering.contracts import TaskContract

    return TaskContract.model_validate(row["input_contract"])


def _requires_handoff(task: dict[str, Any]) -> bool:
    return task["task_type"] in {"engineering.review", "engineering.qa", "engineering.qa.verify"}


def _project_name(task: dict[str, Any], feature: dict[str, Any] | None) -> str | None:
    payload = dict(task.get("payload") or {})
    project_payload = payload.get("project")
    if isinstance(project_payload, dict) and project_payload.get("name"):
        return str(project_payload["name"])
    if isinstance(project_payload, str):
        return project_payload
    if feature is not None:
        return str(feature["project"])
    return None


def _blocked_with_recovery(
    task: dict[str, Any],
    *,
    feature_id: str,
    blocker_code: str,
    action: str,
    rationale: str,
    database_url: str | None,
) -> HandlerResult:
    _record_recovery(
        task,
        feature_id=feature_id,
        blocker_code=blocker_code,
        action=action,
        rationale=rationale,
        database_url=database_url,
    )
    return HandlerResult(
        status="blocked",
        blocker_code=blocker_code,
        blocker_reason=rationale,
        result={"recovery": {"blocker_code": blocker_code, "action": action}},
    )


def _record_recovery(
    task: dict[str, Any],
    *,
    blocker_code: str,
    action: str,
    rationale: str,
    feature_id: str | None = None,
    outcome: str | None = None,
    status: str = "open",
    database_url: str | None,
) -> None:
    payload = dict(task.get("payload") or {})
    resolved_feature_id = str(feature_id or payload.get("feature_id") or task.get("workflow_id"))
    decision = RecoveryDecisionContract(
        feature_id=resolved_feature_id,
        task_id=str(task["id"]),
        blocker_code=blocker_code,
        action=cast(Any, action),
        rationale=rationale,
        attempt=int(task.get("attempt") or 1),
        max_attempts=int(task.get("max_attempts") or 3),
    )
    record_recovery_action(
        decision,
        status=status,
        outcome=outcome,
        database_url=database_url,
    )
