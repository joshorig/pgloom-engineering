from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

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
    recovery_action: Literal["corrective_slice"] = "corrective_slice"
    decision = RecoveryDecisionContract(
        feature_id=feature_id,
        task_id=str(candidate["id"]),
        blocker_code=str(candidate.get("blocker_code") or "engineering.blocked"),
        action=recovery_action,
        rationale=str(payload["replan_context"]["summary"]),
        attempt=int(candidate.get("attempt") or 1),
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
        if blocker_code in immediate_codes:
            return dict(task)
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
    repeat_count = _completed_recovery_count(
        aggregate,
        str(blocked_task.get("blocker_code") or ""),
    )
    blocked_contract = _task_contract_for_task(
        aggregate,
        str(blocked_task.get("id") or ""),
    )
    summary = _replan_summary(blocked_task, repeat_count=repeat_count)
    failure_context = _failure_context(blocked_task)
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
            "attempt": int(blocked_task.get("attempt") or 1),
            "same_blocker_recovery_count": repeat_count,
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


def _replan_summary(blocked_task: dict[str, Any], *, repeat_count: int = 0) -> str:
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
        return (
            "Previous implementer verification failed. Replan must inspect whether the "
            "failure is production-code behavior or QA-owned test/benchmark harness "
            "invalidity; emit only the narrow corrective slice needed, then rerun review "
            f"and split validators. Preserve these verification details: {blocker_reason}"
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
        return (
            "Previous planner output failed PlanContract validation. Replan must preserve "
            "the schema and validation errors as inputs, repair only the invalid contract "
            f"shape, and keep the feature scope stable: {blocker_reason}{detail}"
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
    return " | ".join(excerpts)[:3000]


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
