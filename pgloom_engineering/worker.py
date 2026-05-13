from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pgloom.db.postgres import connect
from pgloom.events import append_event
from pgloom.harness.registry import HandlerRegistry
from pgloom.harness.result import HandlerResult
from pgloom.policies import resolve_retry_policy
from pgloom.resources import acquire_lock
from pgloom.slots import get_slot_concurrency
from pgloom.states import TaskState
from pgloom.tasks import (
    _reserve_dispatch_constraints,
    claim_next,
    retry_or_fail_task,
    transition_task,
)
from pgloom.time import utcnow
from pgloom.workers import register_worker, set_busy

from pgloom_engineering.contract_store import (
    feature_is_paused,
    finish_worker_run,
    get_active_plan_contract,
    get_task_contract,
    list_qa_signoffs,
    list_task_contracts,
    list_task_handoffs,
    record_handoff,
    record_qa_signoff,
    record_recovery_action,
    start_worker_run,
    upsert_task_contract,
)
from pgloom_engineering.contracts import (
    PlanContract,
    QAAuthorContract,
    QAResultContract,
    RecoveryDecisionContract,
    ReviewVerdictContract,
    TaskContract,
    TaskResultContract,
)
from pgloom_engineering.features import get_feature
from pgloom_engineering.handlers.registry import build_registry
from pgloom_engineering.projects import get_project, role_gate_contract


def run_once(
    *,
    slot: str,
    worker_id: str,
    registry: HandlerRegistry | None = None,
    database_url: str | None = None,
    lease_seconds: int = 300,
    feature_id: str | None = None,
) -> dict[str, object]:
    if feature_id:
        task = _claim_next_for_feature(
            slot=slot,
            worker_id=worker_id,
            feature_id=feature_id,
            lease_seconds=lease_seconds,
            database_url=database_url,
        )
    else:
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
    feature_id = str(payload.get("feature_id") or task.get("workflow_id"))
    role, phase, validator_type = _worker_run_identity(task)
    worker_run = start_worker_run(
        feature_id=feature_id,
        task_id=str(task["id"]),
        role=role,
        phase=phase,
        validator_type=validator_type,
        attempt=int(task.get("attempt") or 1) + 1,
        queued_at=_datetime_or_none(task.get("created_at")),
        leased_at=_datetime_or_none(task.get("updated_at")),
        started_at=datetime.now(UTC),
        metadata={"slot": slot, "worker_id": worker_id, "task_type": task["task_type"]},
        database_url=database_url,
    )

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
        finish_worker_run(
            int(worker_run["id"]),
            status="blocked",
            blocker_code=gate_result.blocker_code,
            metadata_patch={"blocker_reason": gate_result.blocker_reason},
            database_url=database_url,
        )
        return {"claimed": True, "task_id": task["id"], "status": "blocked"}

    transition_task(task["id"], TaskState.RUNNING, database_url=database_url)
    try:
        handler = (registry or build_registry()).get(task["task_type"])
        result = handler.handle(task)
        result = _post_execution_gate(task, result, database_url=database_url)
    except Exception as exc:
        crash_result = _worker_crash_result(task, exc)
        _record_recovery(
            task,
            blocker_code="engineering.worker_crash",
            action="record_crash",
            rationale=crash_result["worker_crash"]["message"],
            outcome=crash_result["worker_crash"]["traceback_excerpt"],
            status="open",
            database_url=database_url,
        )
        crash_status = _retry_or_block_worker_crash(
            task,
            crash_result=crash_result,
            database_url=database_url,
        )
        finish_worker_run(
            int(worker_run["id"]),
            status="crashed",
            blocker_code="engineering.worker_crash",
            terminal_reason="lifecycle_error",
            terminal_detail=str(exc),
            metadata_patch=crash_result["worker_crash"],
            database_url=database_url,
        )
        _release_task_resource_locks(str(task["id"]), database_url=database_url)
        return {"claimed": True, "task_id": task["id"], "status": crash_status}

    if result.status == "done":
        transition_task(task["id"], TaskState.DONE, result=result.result, database_url=database_url)
        finish_worker_run(
            int(worker_run["id"]),
            status="done",
            commands_run=_commands_run_from_result(result.result),
            evidence_ids=_evidence_ids_from_result(result.result),
            artifact_ids=_artifact_ids_from_result(result.result),
            artifact_evidence_links=_artifact_evidence_links_from_result(result.result),
            handoff_id=_handoff_id_from_result(result.result),
            database_url=database_url,
        )
        _release_task_resource_locks(str(task["id"]), database_url=database_url)
    elif result.status == "blocked":
        if task["task_type"] in {
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }:
            _persist_qa_result_contract(
                task,
                result,
                feature_id=feature_id,
                status="blocked",
                database_url=database_url,
            )
        _record_recovery(
            task,
            blocker_code=result.blocker_code or "engineering.handler_blocked",
            action="block_execution",
            rationale=result.blocker_reason or result.message or "handler blocked execution",
            status="open",
            database_url=database_url,
        )
        transition_task(
            task["id"],
            TaskState.BLOCKED,
            blocker_code=result.blocker_code or "engineering.handler_blocked",
            blocker_reason=result.blocker_reason or result.message,
            result=result.result,
            database_url=database_url,
        )
        finish_worker_run(
            int(worker_run["id"]),
            status="blocked",
            blocker_code=result.blocker_code or "engineering.handler_blocked",
            commands_run=_commands_run_from_result(result.result),
            evidence_ids=_evidence_ids_from_result(result.result),
            artifact_ids=_artifact_ids_from_result(result.result),
            artifact_evidence_links=_artifact_evidence_links_from_result(result.result),
            metadata_patch={"blocker_reason": result.blocker_reason or result.message},
            database_url=database_url,
        )
        _release_task_resource_locks(str(task["id"]), database_url=database_url)
    elif result.status == "retry":
        retry_or_fail_task(
            task["id"],
            reason=result.message or "handler requested retry",
            database_url=database_url,
        )
        finish_worker_run(
            int(worker_run["id"]),
            status="retry",
            blocker_code=result.blocker_code,
            terminal_reason=result.blocker_code or "handler_retry",
            terminal_detail=result.message,
            commands_run=_commands_run_from_result(result.result),
            database_url=database_url,
        )
        _release_task_resource_locks(str(task["id"]), database_url=database_url)
    else:
        transition_task(task["id"], TaskState.AWAITING_APPROVAL, database_url=database_url)
        finish_worker_run(
            int(worker_run["id"]),
            status=result.status,
            commands_run=_commands_run_from_result(result.result),
            database_url=database_url,
        )
        _release_task_resource_locks(str(task["id"]), database_url=database_url)
    return {"claimed": True, "task_id": task["id"], "status": result.status}


def _claim_next_for_feature(
    *,
    slot: str,
    worker_id: str,
    feature_id: str,
    lease_seconds: int,
    database_url: str | None,
) -> dict[str, Any] | None:
    with connect(database_url) as conn, conn.transaction():
        blocked = conn.execute(
            """
            select 1 from health_checks
            where blocks_dispatch is true and status != 'ok'
            order by created_at desc limit 1
            """
        ).fetchone()
        if blocked:
            return None
        concurrency = get_slot_concurrency(conn, slot)
        if concurrency is not None:
            in_flight = conn.execute(
                """
                select count(*) as count from tasks
                where slot = %s and state in (%s, %s)
                """,
                (slot, TaskState.LEASED.value, TaskState.RUNNING.value),
            ).fetchone()
            if in_flight and int(in_flight["count"]) >= concurrency:
                return None
        row = conn.execute(
            """
            select * from tasks
            where slot = %s and workflow_id = %s and state = %s and run_after <= now()
            order by priority desc, run_after asc, created_at asc
            for update skip locked
            limit 1
            """,
            (slot, feature_id, TaskState.QUEUED.value),
        ).fetchone()
        if not row:
            return None
        if not _reserve_dispatch_constraints(conn, row, worker_id):
            return None
        lease_expires = utcnow() + timedelta(seconds=lease_seconds)
        updated = conn.execute(
            """
            update tasks
            set state = %s, lease_owner = %s, lease_expires_at = %s,
                attempt = attempt + 1, updated_at = now()
            where id = %s
            returning *
            """,
            (TaskState.LEASED.value, worker_id, lease_expires, row["id"]),
        ).fetchone()
        assert updated is not None
        register_worker(conn, worker_id=worker_id, slot=slot)
        set_busy(conn, worker_id=worker_id, task_id=row["id"])
        append_event(
            conn,
            event_type="task.claimed",
            workflow_id=row["workflow_id"],
            task_id=row["id"],
            from_state=row["state"],
            to_state=TaskState.LEASED.value,
            metadata={"worker_id": worker_id},
        )
        return dict(updated)


def _retry_or_block_worker_crash(
    task: dict[str, Any],
    *,
    crash_result: dict[str, Any],
    database_url: str | None,
) -> str:
    policy = resolve_retry_policy(dict(task.get("payload") or {}))
    max_attempts = min(int(task.get("max_attempts") or 1), policy.max_attempts)
    attempt = int(task.get("attempt") or 1)
    reason = crash_result["worker_crash"]["message"]
    if attempt < max_attempts:
        retry_or_fail_task(str(task["id"]), reason=reason, database_url=database_url)
        return "retry"
    transition_task(
        str(task["id"]),
        TaskState.BLOCKED,
        blocker_code="engineering.worker_crash",
        blocker_reason=reason,
        result=crash_result,
        database_url=database_url,
    )
    return "blocked"


def _worker_crash_result(task: dict[str, Any], exc: Exception) -> dict[str, Any]:
    exc_type = type(exc).__name__
    message = f"worker crash: {exc}"
    return {
        "worker_crash": {
            "exception_type": exc_type,
            "exception": str(exc),
            "message": message,
            "traceback_excerpt": traceback.format_exc(limit=8),
            "task_type": str(task.get("task_type") or ""),
            "task_id": str(task.get("id") or ""),
            "attempt": int(task.get("attempt") or 1),
            "max_attempts": int(task.get("max_attempts") or 1),
        }
    }


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
    if feature_is_paused(feature_id, database_url=database_url):
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.feature_paused",
            action="block_execution",
            rationale="Feature dispatch is paused by operator intervention.",
            database_url=database_url,
        )

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
    plan = PlanContract.model_validate(active_plan["contract"])
    input_contract = TaskContract.model_validate(task_contract["input_contract"])
    role_gate_blocker = _role_gate_blocker(project, input_contract)
    if role_gate_blocker is not None:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.role_gate_disabled",
            action="block_execution",
            rationale=role_gate_blocker,
            database_url=database_url,
        )
    milestone_blocker = _milestone_blocker(plan, input_contract, database_url=database_url)
    if milestone_blocker is not None:
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.milestone_locked",
            action="block_execution",
            rationale=milestone_blocker,
            database_url=database_url,
        )
    if task["task_type"] == "engineering.qa.verify.usertest":
        resource_key = f"engineering:{project_name}:full_app_run"
        if not acquire_lock(
            resource_key=resource_key,
            owner_id=str(task["id"]),
            task_id=str(task["id"]),
            ttl_seconds=1800,
            database_url=database_url,
        ):
            return _blocked_with_recovery(
                task,
                feature_id=feature_id,
                blocker_code="engineering.usertest_resource_locked",
                action="block_execution",
                rationale=f"User-test resource lock is busy: {resource_key}",
                database_url=database_url,
            )
    if _requires_handoff(task) and not _has_reviewable_dependency_output(
        input_contract,
        task_id=str(task["id"]),
        database_url=database_url,
    ):
        return _blocked_with_recovery(
            task,
            feature_id=feature_id,
            blocker_code="engineering.handoff_missing",
            action="block_execution",
            rationale="Review task requires a producer handoff or dependency output.",
            database_url=database_url,
        )
    return None


def _role_gate_blocker(
    project: Any,
    task_contract: TaskContract,
) -> str | None:
    if project is None:
        return None
    expected = task_contract.role_gate
    current = role_gate_contract(project, task_contract.role)
    if current.status == "disabled":
        return current.reason
    if expected is None:
        return None
    if expected.project != current.project or expected.role != current.role:
        return (
            "TaskContract role_gate contract does not match current project role: "
            f"expected {expected.project}/{expected.role}, got "
            f"{current.project}/{current.role}."
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
                _attach_handoff_ids(
                    result,
                    _record_dependency_handoffs(
                        feature_id=feature_id,
                        from_task_id=str(task["id"]),
                        handoff_type="task_result",
                        contract=contract.model_dump(mode="json"),
                        database_url=database_url,
                    ),
                )
        elif task["task_type"] == "engineering.review":
            review_verdict_contract = ReviewVerdictContract.model_validate(
                result.result.get("review_verdict_contract")
            )
            row = get_task_contract(task["id"], database_url=database_url)
            if row is not None:
                output_contract = {
                    "review_verdict_contract": review_verdict_contract.model_dump(mode="json")
                }
                upsert_task_contract(
                    task["id"],
                    _task_contract_from_row(row),
                    output_contract=output_contract,
                    status="completed",
                    database_url=database_url,
                )
                _attach_handoff_ids(
                    result,
                    _record_dependency_handoffs(
                        feature_id=feature_id,
                        from_task_id=str(task["id"]),
                        handoff_type="review",
                        contract=output_contract,
                        database_url=database_url,
                    ),
                )
                if review_verdict_contract.verdict != "approve":
                    return _blocked_with_recovery(
                        task,
                        feature_id=feature_id,
                        blocker_code="engineering.review_rejected",
                        action="corrective_slice",
                        rationale=_review_rejection_rationale(review_verdict_contract),
                        database_url=database_url,
                    )
        elif task["task_type"] == "engineering.qa.author":
            qa_author_contract = QAAuthorContract.model_validate(
                result.result.get("qa_author_contract")
            )
            row = get_task_contract(task["id"], database_url=database_url)
            if row is not None:
                output_contract = {
                    "qa_author_contract": qa_author_contract.model_dump(mode="json")
                }
                upsert_task_contract(
                    task["id"],
                    _task_contract_from_row(row),
                    output_contract=output_contract,
                    status="completed",
                    database_url=database_url,
                )
                _attach_handoff_ids(
                    result,
                    _record_dependency_handoffs(
                        feature_id=feature_id,
                        from_task_id=str(task["id"]),
                        handoff_type="qa_author_contract",
                        contract=output_contract,
                        database_url=database_url,
                    ),
                )
        elif task["task_type"] in {
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }:
            _persist_qa_result_contract(
                task,
                result,
                feature_id=feature_id,
                status="completed",
                database_url=database_url,
            )
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


def _persist_qa_result_contract(
    task: dict[str, Any],
    result: HandlerResult,
    *,
    feature_id: str,
    status: str,
    database_url: str | None,
) -> QAResultContract | None:
    payload = result.result.get("qa_result_contract") if isinstance(result.result, dict) else None
    if payload is None:
        return None
    qa_result_contract = QAResultContract.model_validate(payload)
    row = get_task_contract(task["id"], database_url=database_url)
    if row is None:
        return qa_result_contract
    task_contract = _task_contract_from_row(row)
    output_contract = {"qa_result_contract": qa_result_contract.model_dump(mode="json")}
    upsert_task_contract(
        task["id"],
        task_contract,
        output_contract=output_contract,
        status=status,
        database_url=database_url,
    )
    _attach_handoff_ids(
        result,
        _record_dependency_handoffs(
            feature_id=feature_id,
            from_task_id=str(task["id"]),
            handoff_type="validation",
            contract=output_contract,
            database_url=database_url,
        ),
    )
    if status == "completed" and qa_result_contract.verdict == "pass":
        record_qa_signoff(
            feature_id=feature_id,
            task_id=str(task["id"]),
            plan_contract_id=str(task_contract.plan_contract_id),
            milestone_id=_milestone_id(task_contract),
            validator_type=str(qa_result_contract.validator_type or ""),
            verdict=qa_result_contract.verdict,
            qa_result_contract=qa_result_contract.model_dump(mode="json"),
            evidence=qa_result_contract.validation_evidence,
            artifact_ids=_qa_result_artifact_ids(qa_result_contract),
            metadata={"task_type": task["task_type"]},
            database_url=database_url,
        )
    return qa_result_contract


def _task_contract_from_row(row: dict[str, Any]) -> Any:
    from pgloom_engineering.contracts import TaskContract

    return TaskContract.model_validate(row["input_contract"])


def _record_dependency_handoffs(
    *,
    feature_id: str,
    from_task_id: str,
    handoff_type: str,
    contract: dict[str, Any],
    database_url: str | None,
) -> list[dict[str, Any]]:
    handoffs: list[dict[str, Any]] = []
    for row in list_task_contracts(feature_id, database_url=database_url):
        input_contract = row.get("input_contract")
        if not isinstance(input_contract, dict):
            continue
        dependencies = input_contract.get("dependencies")
        if not isinstance(dependencies, list) or from_task_id not in dependencies:
            continue
        handoffs.append(
            record_handoff(
                feature_id=feature_id,
                from_task_id=from_task_id,
                to_task_id=str(row["task_id"]),
                handoff_type=handoff_type,
                contract=contract,
                database_url=database_url,
            )
        )
    return handoffs


def _attach_handoff_ids(result: HandlerResult, handoffs: list[dict[str, Any]]) -> None:
    if not handoffs or not isinstance(result.result, dict):
        return
    ids = [str(row["id"]) for row in handoffs if row.get("id")]
    if not ids:
        return
    result.result["handoff_ids"] = ids
    result.result.setdefault("handoff_id", ids[0])


def _requires_handoff(task: dict[str, Any]) -> bool:
    return task["task_type"] in {
        "engineering.review",
    }


def _has_reviewable_dependency_output(
    task_contract: TaskContract,
    *,
    task_id: str,
    database_url: str | None,
) -> bool:
    if list_task_handoffs(task_id, handoff_type="task_result", database_url=database_url):
        return True
    if list_task_handoffs(task_id, handoff_type="qa_author_contract", database_url=database_url):
        return True
    for dependency_id in reversed(task_contract.dependencies):
        row = get_task_contract(dependency_id, database_url=database_url)
        if row is None:
            continue
        output = row.get("output_contract")
        if isinstance(output, dict) and output:
            return True
    return False


def _milestone_blocker(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    database_url: str | None,
) -> str | None:
    milestone_id = task_contract.inputs.get("milestone_id")
    if not isinstance(milestone_id, str) or not milestone_id:
        return None
    milestone = next(
        (item for item in plan.milestones if item.milestone_id == milestone_id),
        None,
    )
    if milestone is None:
        return None
    for dependency_id in milestone.depends_on:
        dependency = next(
            (item for item in plan.milestones if item.milestone_id == dependency_id),
            None,
        )
        if dependency is None:
            return f"Milestone {milestone_id} depends on missing milestone {dependency_id}."
        if not _milestone_signed_off(
            plan.feature_id,
            dependency,
            database_url=database_url,
        ):
            return (
                f"Milestone {milestone_id} is locked until milestone "
                f"{dependency_id} is signed off."
            )
    return None


def _milestone_signed_off(
    feature_id: str,
    milestone: Any,
    *,
    database_url: str | None,
) -> bool:
    try:
        signoffs = list_qa_signoffs(
            feature_id,
            milestone_id=milestone.milestone_id,
            database_url=database_url,
        )
    except Exception:
        signoffs = []
    signoff_results = {
        f"engineering.qa.verify.{row['validator_type']}": row["verdict"]
        for row in signoffs
        if row.get("verdict") == "pass"
    }
    if milestone.signoff_policy == "scrutiny_only" and signoff_results:
        return signoff_results.get("engineering.qa.verify.scrutiny") == "pass"
    if signoff_results:
        return (
            signoff_results.get("engineering.qa.verify.scrutiny") == "pass"
            and signoff_results.get("engineering.qa.verify.usertest") == "pass"
        )

    rows = list_task_contracts(feature_id, database_url=database_url)
    task_by_slice = {
        row["input_contract"].get("inputs", {}).get("task_slice_id"): row
        for row in rows
        if isinstance(row.get("input_contract"), dict)
    }
    validator_results: dict[str, str] = {}
    for slice_id in milestone.slice_ids:
        row = task_by_slice.get(slice_id)
        if row is None or row.get("status") != "completed":
            continue
        input_contract = row.get("input_contract")
        output_contract = row.get("output_contract")
        if not isinstance(input_contract, dict) or not isinstance(output_contract, dict):
            continue
        task_type = input_contract.get("task_type")
        result = output_contract.get("qa_result_contract")
        if (
            task_type
            in {"engineering.qa.verify.scrutiny", "engineering.qa.verify.usertest"}
            and isinstance(result, dict)
            and result.get("verdict") == "pass"
        ):
            validator_results[str(task_type)] = "pass"
    if milestone.signoff_policy == "scrutiny_only":
        return validator_results.get("engineering.qa.verify.scrutiny") == "pass"
    return (
        validator_results.get("engineering.qa.verify.scrutiny") == "pass"
        and validator_results.get("engineering.qa.verify.usertest") == "pass"
    )


def _milestone_id(task_contract: TaskContract) -> str | None:
    value = task_contract.inputs.get("milestone_id")
    return value if isinstance(value, str) and value else None


def _qa_result_artifact_ids(contract: QAResultContract) -> list[str]:
    artifact_ids: list[str] = []
    for evidence in contract.validation_evidence:
        raw_ids = evidence.get("artifact_ids") if isinstance(evidence, dict) else None
        if isinstance(raw_ids, list):
            artifact_ids.extend(str(item) for item in raw_ids if item is not None)
    for command in contract.commands_run:
        raw_ids = command.get("artifact_ids") if isinstance(command, dict) else None
        if isinstance(raw_ids, list):
            artifact_ids.extend(str(item) for item in raw_ids if item is not None)
    return list(dict.fromkeys(artifact_ids))


def _worker_run_identity(task: dict[str, Any]) -> tuple[str, str, str | None]:
    task_type = str(task["task_type"])
    if task_type == "engineering.plan":
        return "planner", "plan", None
    if task_type == "engineering.qa.author":
        return "qa", "author", None
    if task_type == "engineering.qa.verify.scrutiny":
        return "qa", "verify", "scrutiny"
    if task_type == "engineering.qa.verify.usertest":
        return "qa", "verify", "usertest"
    if task_type == "engineering.implement":
        return "implementer", "implement", None
    if task_type == "engineering.review":
        return "reviewer", "review", None
    if task_type in {"engineering.history", "engineering.historian"}:
        return "historian", "history", None
    return str(task.get("slot") or "worker"), task_type, None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _commands_run_from_result(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    qa_author = result.get("qa_author_contract")
    if isinstance(qa_author, dict):
        commands = _commands_run_from_red_proof(qa_author.get("red_proof"))
        if commands:
            return commands
    for key in ("task_result_contract", "qa_result_contract", "handoff_envelope"):
        payload = result.get(key)
        if isinstance(payload, dict) and isinstance(payload.get("commands_run"), list):
            return _commands_run_from_checks(payload["commands_run"])
        if isinstance(payload, dict) and isinstance(payload.get("checks"), list):
            commands = _commands_run_from_checks(payload["checks"])
            if commands:
                return commands
    if isinstance(result.get("commands_run"), list):
        commands = _commands_run_from_checks(result["commands_run"])
        if commands:
            return commands
    commands = _commands_run_from_blocked_result(result)
    if commands:
        return commands
    return []


def _commands_run_from_blocked_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_commands = result.get("commands")
    if not isinstance(raw_commands, list):
        return []
    stdout_excerpt = result.get("stdout_excerpt")
    stderr_excerpt = result.get("stderr_excerpt")
    commands: list[dict[str, Any]] = []
    for command in raw_commands:
        if not isinstance(command, list):
            continue
        payload: dict[str, Any] = {
            "cmd": [str(part) for part in command],
            "exit_code": 1,
            "duration_s": 0.0,
        }
        if isinstance(stdout_excerpt, str) and stdout_excerpt.strip():
            payload["stdout_excerpt"] = stdout_excerpt[:1200]
        if isinstance(stderr_excerpt, str) and stderr_excerpt.strip():
            payload["stderr_excerpt"] = stderr_excerpt[:1200]
        commands.append(payload)
    return commands


def _commands_run_from_red_proof(red_proof: Any) -> list[dict[str, Any]]:
    if not isinstance(red_proof, list):
        return []
    commands: list[dict[str, Any]] = []
    for item in red_proof:
        if not isinstance(item, dict):
            continue
        command = item.get("command") or item.get("cmd")
        if not isinstance(command, list):
            continue
        artifact_ids = item.get("artifact_ids")
        exit_code, exit_warning = _safe_int_field(item.get("exit_code"), field="exit_code")
        duration_s, duration_warning = _safe_float_field(
            item.get("duration_s"),
            field="duration_s",
        )
        payload: dict[str, Any] = {
            "cmd": [str(part) for part in command],
            "exit_code": exit_code,
            "duration_s": duration_s,
        }
        warnings = [warning for warning in (exit_warning, duration_warning) if warning]
        if warnings:
            payload["normalization_warnings"] = warnings
        if isinstance(artifact_ids, list):
            payload["artifact_ids"] = [
                str(artifact_id) for artifact_id in artifact_ids if artifact_id is not None
            ]
        stdout_excerpt = item.get("stdout_excerpt")
        stderr_excerpt = item.get("stderr_excerpt")
        if isinstance(stdout_excerpt, str) and stdout_excerpt.strip():
            payload["stdout_excerpt"] = stdout_excerpt[:1200]
        if isinstance(stderr_excerpt, str) and stderr_excerpt.strip():
            payload["stderr_excerpt"] = stderr_excerpt[:1200]
        commands.append(payload)
    return commands


def _commands_run_from_checks(checks: list[Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        command = item.get("command") or item.get("cmd")
        if not isinstance(command, list):
            continue
        exit_code, exit_warning = _safe_int_field(item.get("exit_code"), field="exit_code")
        duration_s, duration_warning = _safe_float_field(
            item.get("duration_s", item.get("duration_seconds")),
            field="duration_s",
        )
        payload: dict[str, Any] = {
            "cmd": [str(part) for part in command],
            "exit_code": exit_code,
            "duration_s": duration_s,
        }
        warnings = [warning for warning in (exit_warning, duration_warning) if warning]
        if warnings:
            payload["normalization_warnings"] = warnings
        artifact_ids = item.get("artifact_ids")
        if isinstance(artifact_ids, list):
            payload["artifact_ids"] = [
                str(artifact_id) for artifact_id in artifact_ids if artifact_id is not None
            ]
        stdout_excerpt = item.get("stdout_excerpt")
        stderr_excerpt = item.get("stderr_excerpt")
        if isinstance(stdout_excerpt, str) and stdout_excerpt.strip():
            payload["stdout_excerpt"] = stdout_excerpt[:1200]
        if isinstance(stderr_excerpt, str) and stderr_excerpt.strip():
            payload["stderr_excerpt"] = stderr_excerpt[:1200]
        commands.append(payload)
    return commands


def _safe_int_field(value: Any, *, field: str) -> tuple[int, dict[str, Any] | None]:
    if value in (None, ""):
        return 0, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return 0, _normalization_warning(field=field, value=value, default=0)


def _safe_float_field(value: Any, *, field: str) -> tuple[float, dict[str, Any] | None]:
    if value in (None, ""):
        return 0.0, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return 0.0, _normalization_warning(field=field, value=value, default=0.0)


def _normalization_warning(*, field: str, value: Any, default: int | float) -> dict[str, Any]:
    return {
        "code": "command_metadata_coercion_failed",
        "field": field,
        "raw_value": str(value)[:240],
        "default": default,
    }


def _evidence_ids_from_result(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    payload = result.get("qa_result_contract")
    if not isinstance(payload, dict):
        return []
    evidence = payload.get("validation_evidence")
    if not isinstance(evidence, list):
        return []
    ids: list[str] = []
    for item in evidence:
        if isinstance(item, dict) and item.get("evidence_id"):
            ids.append(str(item["evidence_id"]))
    return ids


def _artifact_ids_from_result(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    ids: list[str] = []
    for key in ("task_result_contract", "qa_result_contract", "handoff_envelope"):
        payload = result.get(key)
        if isinstance(payload, dict):
            raw = payload.get("artifact_ids") or payload.get("artifacts")
            if isinstance(raw, list):
                ids.extend(str(item) for item in raw)
    qa_author = result.get("qa_author_contract")
    if isinstance(qa_author, dict):
        for command in _commands_run_from_red_proof(qa_author.get("red_proof")):
            raw = command.get("artifact_ids")
            if isinstance(raw, list):
                ids.extend(str(item) for item in raw)
    return ids


def _artifact_evidence_links_from_result(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    payload = result.get("qa_result_contract")
    if not isinstance(payload, dict):
        return []
    evidence = payload.get("validation_evidence")
    if not isinstance(evidence, list):
        return []
    links: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        evidence_id = item.get("evidence_id")
        raw_artifact_ids = item.get("artifact_ids")
        if not evidence_id or not isinstance(raw_artifact_ids, list):
            continue
        for artifact_id in raw_artifact_ids:
            if artifact_id is None:
                continue
            links.append(
                {
                    "artifact_id": str(artifact_id),
                    "evidence_id": str(evidence_id),
                    "evidence_kind": item.get("kind"),
                }
            )
    return links


def _handoff_id_from_result(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    if result.get("handoff_id"):
        return str(result["handoff_id"])
    handoff_ids = result.get("handoff_ids")
    if isinstance(handoff_ids, list) and handoff_ids:
        return str(handoff_ids[0])
    for key in ("task_result_contract", "handoff_envelope"):
        payload = result.get(key)
        if isinstance(payload, dict) and payload.get("handoff_id"):
            return str(payload["handoff_id"])
    return None


def _release_task_resource_locks(task_id: str, *, database_url: str | None) -> None:
    with connect(database_url) as conn, conn.transaction():
        conn.execute("delete from resource_locks where task_id = %s", (task_id,))


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


def _review_rejection_rationale(verdict: ReviewVerdictContract) -> str:
    rationale = (
        f"Reviewer verdict was {verdict.verdict}: {verdict.rationale}"
    ).strip()
    findings = [str(finding).strip() for finding in verdict.findings if str(finding).strip()]
    if not findings:
        return rationale
    finding_text = " | ".join(findings[:5])
    if len(finding_text) > 4000:
        finding_text = f"{finding_text[:3997]}..."
    return f"{rationale} Findings: {finding_text}"


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
