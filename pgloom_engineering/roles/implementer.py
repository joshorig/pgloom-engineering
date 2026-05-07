from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pgloom.harness.result import HandlerResult
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import (
    get_active_plan_contract,
    get_task_contract,
    list_task_handoffs,
)
from pgloom_engineering.contracts import (
    PlanContract,
    QAAuthorContract,
    TaskContract,
    TaskResultContract,
)
from pgloom_engineering.integrations.git import changed_files
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import get_project
from pgloom_engineering.qa_author_runtime import (
    command_for_worktree,
    isolate_codex_worktree_context,
    path_matches,
    route_model_command,
)
from pgloom_engineering.qa_runtime import (
    command_with_env,
    qa_env,
    relevant_changed_files,
    run_qa_verification,
)
from pgloom_engineering.role_context import build_role_context, record_role_context_usage


class ImplementerHandler:
    def __init__(self, *, provider: EngineeringCLIModelProvider | None = None) -> None:
        self._provider = provider

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        database_url = payload.get("database_url")
        task_id = str(task.get("id"))
        feature_id = str(payload.get("feature_id") or task.get("workflow_id"))

        row = get_task_contract(task_id, database_url=database_url)
        if row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.task_contract_missing",
                blocker_reason="implementer task has no persisted TaskContract",
            )
        task_contract = TaskContract.model_validate(row["input_contract"])
        plan_row = get_active_plan_contract(feature_id, database_url=database_url)
        if plan_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.active_plan_missing",
                blocker_reason="implementer task has no active PlanContract",
            )
        plan = PlanContract.model_validate(plan_row["contract"])
        project = get_project(plan.project, database_url=database_url)
        if project is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unregistered",
                blocker_reason=f"Project is not registered: {plan.project}",
            )

        qa_contract = _dependency_qa_contract(task_contract, database_url=database_url)
        worktree = (
            Path(qa_contract.worktree_path)
            if qa_contract and qa_contract.worktree_path
            else None
        )
        if worktree is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_handoff_missing",
                blocker_reason="implementer requires a QA author worktree handoff",
            )
        assert qa_contract is not None

        settings = get_settings()
        profile = CLIModelProfile(
            name=settings.implementer_profile,
            command=command_with_env(
                isolate_codex_worktree_context(
                    route_model_command(
                        command_for_worktree(settings.implementer_command, worktree),
                        claude_model=settings.implementer_claude_model,
                        codex_model=settings.implementer_codex_model,
                        codex_reasoning=settings.implementer_codex_reasoning,
                    ),
                    worktree=worktree,
                    context_root=getattr(settings, "role_model_context_root", Path(".")),
                    enabled=bool(
                        getattr(settings, "role_model_context_isolation_enabled", False)
                    ),
                ),
                qa_env(project.metadata, project_root=project.root),
            ),
            timeout_seconds=settings.implementer_invocation_timeout_seconds,
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        role_context = build_role_context(
            role="implementer",
            project=project,
            plan=plan,
            task_contract=task_contract,
            workflow_id=feature_id,
            database_url=database_url,
        )
        baseline = _changed_file_snapshot(worktree, project.metadata)
        prompt = build_implementer_prompt(
            plan=plan,
            task_contract=task_contract,
            qa_contract=qa_contract,
            worktree=worktree,
            project_metadata=project.metadata,
            task_id=task_id,
            role_context=role_context.prompt_payload(),
        )
        model_usage_ids: list[int] = []
        response = provider.invoke(
            profile=profile,
            prompt=prompt,
            workflow_id=feature_id,
            task_id=task_id,
        )
        if response.model_usage_id is not None:
            model_usage_ids.append(response.model_usage_id)
        token_savior_usage_ids: list[int] = []
        usage_id = record_role_context_usage(
            role_context,
            feature_id=feature_id,
            workflow_id=feature_id,
            task_id=task_id,
            profile_name=profile.name,
            model_usage_id=response.model_usage_id,
            database_url=database_url,
        )
        if usage_id is not None:
            token_savior_usage_ids.append(usage_id)

        repair_attempts = 0
        max_repair_attempts = 2
        while True:
            touched = _implementation_changed_files(worktree, baseline, project.metadata)
            violations = implementation_path_violations(touched, task_contract)
            verification_results = [
                run_qa_verification(
                    command,
                    worktree=worktree,
                    project_metadata=project.metadata,
                    timeout_seconds=settings.implementer_invocation_timeout_seconds,
                    database_url=database_url,
                    workflow_id=feature_id,
                    task_id=task_id,
                    feature_id=feature_id,
                )
                for command in task_contract.verification_commands
            ]
            failed_verifications = [
                result
                for result in verification_results
                if result.original.exit_code != 0
                or result.original.timed_out
                or result.original.killed
            ]
            contract_error: str | None = None
            try:
                output = TaskResultContract.model_validate(
                    normalize_task_result_payload(extract_json(response.text))
                )
            except Exception as exc:
                output = None
                contract_error = str(exc)

            if (
                (contract_error or violations or failed_verifications)
                and repair_attempts < max_repair_attempts
            ):
                repair_attempts += 1
                response = provider.invoke(
                    profile=profile,
                    prompt=build_implementer_repair_prompt(
                        plan=plan,
                        task_contract=task_contract,
                        qa_contract=qa_contract,
                        worktree=worktree,
                        changed_files=touched,
                        path_violations=violations,
                        failed_verifications=failed_verifications,
                        contract_error=contract_error,
                        raw_response=response.text,
                        role_context=role_context.prompt_payload(),
                    ),
                    workflow_id=feature_id,
                    task_id=task_id,
                )
                if response.model_usage_id is not None:
                    model_usage_ids.append(response.model_usage_id)
                usage_id = record_role_context_usage(
                    role_context,
                    feature_id=feature_id,
                    workflow_id=feature_id,
                    task_id=task_id,
                    profile_name=profile.name,
                    model_usage_id=response.model_usage_id,
                    database_url=database_url,
                )
                if usage_id is not None:
                    token_savior_usage_ids.append(usage_id)
                continue

            if contract_error:
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.implementer_contract_invalid",
                    blocker_reason=contract_error,
                    result={"changed_files": touched, "repair_attempts": repair_attempts},
                )
            if violations:
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.implementation_path_violation",
                    blocker_reason="implementer touched paths outside its contract",
                    result={
                        "violations": violations,
                        "changed_files": touched,
                        "repair_attempts": repair_attempts,
                    },
                )
            if failed_verifications:
                first = failed_verifications[0]
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.implementation_verification_failed",
                    blocker_reason="implementer verification commands failed",
                    result={
                        "commands": [item.original.argv for item in verification_results],
                        "stdout_excerpt": first.stdout_excerpt,
                        "stderr_excerpt": first.stderr_excerpt,
                        "changed_files": touched,
                        "repair_attempts": repair_attempts,
                    },
                )
            if output is None:
                raise AssertionError("TaskResultContract unexpectedly missing after validation")
            output = output.model_copy(
                update={
                    "feature_id": task_contract.feature_id,
                    "task_id": task_id,
                    "changed_files": sorted(set([*output.changed_files, *touched])),
                    "branch": output.branch or qa_contract.branch,
                    "model_usage_ids": [*output.model_usage_ids, *model_usage_ids],
                    "checks": [
                        *output.checks,
                        *[
                            {
                                "command": result.original.argv,
                                "exit_code": result.original.exit_code,
                                "status": "passed",
                            }
                            for result in verification_results
                        ],
                    ],
                    "token_savior_usage_ids": [
                        *output.token_savior_usage_ids,
                        *token_savior_usage_ids,
                    ],
                }
            )
            return HandlerResult.done(
                {
                    "role": "implementer",
                    "task_id": task_id,
                    "task_result_contract": output.model_dump(mode="json"),
                    "repair_attempts": repair_attempts,
                }
            )


def build_implementer_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
    worktree: Path,
    project_metadata: dict[str, Any],
    task_id: str,
    role_context: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "role": "implementation_engineer",
            "instructions": [
                "Implement the production code required to make the QA-authored tests pass.",
                "Work in the provided worktree and preserve QA-authored test files unchanged.",
                "Only edit paths allowed by the TaskContract; never edit forbidden paths.",
                "Run the focused verification commands before returning.",
                "Return only a TaskResultContract JSON object.",
            ],
            "worktree": str(worktree),
            "role_context": role_context or {},
            "plan_contract": plan.model_dump(mode="json"),
            "task_contract": task_contract.model_dump(mode="json"),
            "qa_author_contract": qa_contract.model_dump(mode="json"),
            "project_metadata": _safe_project_metadata(project_metadata),
            "required_response": {
                "contract_version": "engineering.contracts.v1",
                "feature_id": task_contract.feature_id,
                "task_id": task_id,
                "changed_files": [],
                "checks": [],
                "blockers": [],
            },
        },
        indent=2,
        sort_keys=True,
    )


def build_implementer_repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
    worktree: Path,
    changed_files: list[str],
    path_violations: list[dict[str, str]],
    failed_verifications: list[Any],
    contract_error: str | None,
    raw_response: str,
    role_context: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "role": "implementation_repair_engineer",
            "instructions": [
                "Repair the implementation in the same worktree.",
                (
                    "Do not edit QA-authored test files or forbidden paths; "
                    "revert any forbidden-path edits."
                ),
                "Fix compile/runtime/test failures by changing production code only.",
                "Return only a valid TaskResultContract JSON object.",
            ],
            "worktree": str(worktree),
            "role_context": role_context or {},
            "plan_contract": plan.model_dump(mode="json"),
            "task_contract": task_contract.model_dump(mode="json"),
            "qa_author_contract": qa_contract.model_dump(mode="json"),
            "changed_files": changed_files,
            "path_violations": path_violations,
            "contract_error": contract_error,
            "failed_verifications": [
                {
                    "command": item.original.argv,
                    "exit_code": item.original.exit_code,
                    "stdout_excerpt": item.stdout_excerpt,
                    "stderr_excerpt": item.stderr_excerpt,
                }
                for item in failed_verifications
            ],
            "previous_response": raw_response,
        },
        indent=2,
        sort_keys=True,
    )


def normalize_task_result_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("TaskResultContract"), dict):
        return payload["TaskResultContract"]
    if isinstance(payload, dict) and isinstance(payload.get("task_result_contract"), dict):
        return payload["task_result_contract"]
    return payload


def implementation_path_violations(
    paths: list[str],
    task_contract: TaskContract,
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in paths:
        allowed = any(path_matches(path, root) for root in task_contract.allowed_paths)
        forbidden = any(path_matches(path, root) for root in task_contract.forbidden_paths)
        if not allowed or forbidden:
            violations.append(
                {
                    "path": path,
                    "reason": "forbidden_path" if forbidden else "outside_allowed_paths",
                }
            )
    return violations


def _dependency_qa_contract(
    task_contract: TaskContract,
    *,
    database_url: str | None,
) -> QAAuthorContract | None:
    for dependency_id in reversed(task_contract.dependencies):
        row = get_task_contract(dependency_id, database_url=database_url)
        contract = _qa_contract_from_payload(row.get("output_contract") if row else None)
        if contract is not None:
            return contract
    input_task_id = str(task_contract.inputs.get("task_id") or "")
    for handoff in reversed(
        list_task_handoffs(input_task_id, database_url=database_url)
    ):
        contract = _qa_contract_from_payload(handoff.get("contract"))
        if contract is not None:
            return contract
    return None


def _qa_contract_from_payload(payload: Any) -> QAAuthorContract | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("qa_author_contract", payload)
    if not isinstance(raw, dict):
        return None
    try:
        return QAAuthorContract.model_validate(raw)
    except Exception:
        return None


def _changed_file_snapshot(
    worktree: Path,
    project_metadata: dict[str, Any],
) -> dict[str, str | None]:
    return {
        path: _file_hash(worktree / path)
        for path in relevant_changed_files(changed_files(worktree), project_metadata)
    }


def _implementation_changed_files(
    worktree: Path,
    baseline: dict[str, str | None],
    project_metadata: dict[str, Any],
) -> list[str]:
    current_paths = relevant_changed_files(changed_files(worktree), project_metadata)
    touched: list[str] = []
    for path in current_paths:
        current_hash = _file_hash(worktree / path)
        if path not in baseline or baseline[path] != current_hash:
            touched.append(path)
    for path, old_hash in baseline.items():
        if old_hash is not None and not (worktree / path).exists():
            touched.append(path)
    return sorted(dict.fromkeys(touched))


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_project_metadata(project_metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in project_metadata.items()
        if key in {"qa", "relevant_paths", "roadmap_path", "regression_command", "smoke_command"}
    }
