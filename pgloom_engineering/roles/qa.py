from __future__ import annotations

from pathlib import Path
from typing import Any

from pgloom.harness.result import HandlerResult
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import get_active_plan_contract, get_task_contract
from pgloom_engineering.contracts import (
    PlanContract,
    QAAuthorContract,
    QAResultContract,
    TaskContract,
)
from pgloom_engineering.integrations.git import changed_files, create_task_worktree
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import get_project
from pgloom_engineering.qa_author_runtime import (
    build_qa_author_prompt,
    command_for_worktree,
    normalize_qa_author_payload,
    path_violations,
    qa_model_route,
    route_model_command,
    semantic_quality_findings,
)
from pgloom_engineering.qa_author_runtime import (
    verification_commands as select_verification_commands,
)
from pgloom_engineering.qa_runtime import (
    canonical_red_proof,
    command_with_env,
    hydrate_dependencies,
    is_red_test_failure,
    qa_env,
    relevant_changed_files,
    run_qa_verification,
    validate_required_qa_gates,
)


class QAHandler:
    def __init__(self, *, provider: EngineeringCLIModelProvider | None = None) -> None:
        self._provider = provider

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        if task["task_type"] == "engineering.qa.author":
            return self._handle_author(task)
        if task["task_type"] in {"engineering.qa", "engineering.qa.verify"}:
            return self._handle_verify(task)
        return HandlerResult(
            status="blocked",
            blocker_code="engineering.qa_unknown_task_type",
            blocker_reason=f"unsupported task_type: {task['task_type']}",
        )

    def _handle_verify(self, task: dict[str, Any]) -> HandlerResult:
        task_id = str(task.get("id") or "")
        feature_id = str(
            (task.get("payload") or {}).get("feature_id") or task.get("workflow_id") or ""
        )
        contract = QAResultContract(
            feature_id=feature_id,
            task_id=task_id,
            verdict="inconclusive",
            commands=[],
            evidence=[],
            findings=["engineering.qa.verify handler is not implemented yet"],
        )
        return HandlerResult.done(
            {
                "role": "qa",
                "task_id": task_id,
                "qa_result_contract": contract.model_dump(mode="json"),
            }
        )

    def _handle_author(self, task: dict[str, Any]) -> HandlerResult:
        payload = dict(task.get("payload") or {})
        database_url = payload.get("database_url")
        task_id = str(task["id"])
        task_row = get_task_contract(task_id, database_url=database_url)
        if task_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.task_contract_missing",
                blocker_reason="qa.author requires a persisted TaskContract",
            )
        task_contract = TaskContract.model_validate(task_row["input_contract"])
        plan_row = get_active_plan_contract(task_contract.feature_id, database_url=database_url)
        if plan_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.active_plan_missing",
                blocker_reason="qa.author requires an active PlanContract",
            )
        plan = PlanContract.model_validate(plan_row["contract"])
        project = get_project(plan.project, database_url=database_url)
        if project is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unregistered",
                blocker_reason=f"Project is not registered: {plan.project}",
            )

        settings = get_settings()
        worktree_root = Path(project.metadata.get("worktree_root") or settings.qa_worktree_root)
        if not worktree_root.is_absolute():
            worktree_root = project.root / worktree_root
        handle = create_task_worktree(
            repo=project.root,
            worktree_root=worktree_root,
            feature_id=task_contract.feature_id,
            task_id=task_id,
            slice_id=str(task_contract.inputs.get("task_slice_id") or "qa-author"),
            base_ref=project.base_branch,
        )
        hydrate_dependencies(project.root, handle.worktree, project.metadata)

        profile = CLIModelProfile(
            name=settings.qa_author_profile,
            command=command_with_env(
                route_model_command(
                    command_for_worktree(settings.qa_author_command, handle.worktree),
                    **qa_model_route(project.metadata, settings),
                ),
                qa_env(project.metadata, project_root=project.root),
            ),
            timeout_seconds=settings.qa_author_invocation_timeout_seconds,
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        response = provider.invoke(
            profile=profile,
            prompt=build_qa_author_prompt(
                plan,
                task_contract,
                project_metadata=project.metadata,
                project_root=handle.worktree,
            ),
            workflow_id=task_contract.feature_id,
            task_id=task_id,
        )
        try:
            contract = QAAuthorContract.model_validate(
                normalize_qa_author_payload(extract_json(response.text))
            )
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_author_contract_invalid",
                blocker_reason=str(exc),
                result={"raw_response": response.text},
            )
        touched = relevant_changed_files(changed_files(handle.worktree), project.metadata)
        violations = path_violations(touched, task_contract)
        if violations:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_path_violation",
                blocker_reason="qa.author touched paths outside its contract",
                result={"violations": violations, "changed_files": touched},
            )
        gate_validation = validate_required_qa_gates(handle.worktree, project.metadata)
        gate_failures = [item for item in gate_validation if item.get("status") != "configured"]
        if gate_failures:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_gate_validation_failed",
                blocker_reason="required project QA gate is not deterministically configured",
                result={"gate_validation": gate_validation, "changed_files": touched},
            )
        semantic_findings = semantic_quality_findings(
            worktree=handle.worktree,
            changed_paths=touched,
            plan=plan,
            task_contract=task_contract,
            project_metadata=project.metadata,
        )
        blocking_semantic_findings = [
            finding for finding in semantic_findings if finding.get("severity") == "blocking"
        ]
        if blocking_semantic_findings:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_semantic_quality_failed",
                blocker_reason="qa.author output failed deterministic semantic quality review",
                result={
                    "findings": blocking_semantic_findings,
                    "gate_validation": gate_validation,
                    "changed_files": touched,
                },
            )
        verification_results = [
            run_qa_verification(
                command,
                worktree=handle.worktree,
                project_metadata=project.metadata,
                timeout_seconds=settings.qa_author_invocation_timeout_seconds,
                database_url=database_url,
                workflow_id=task.get("workflow_id"),
                task_id=task_id,
                feature_id=task_contract.feature_id,
            )
            for command in select_verification_commands(task_contract)
        ]
        infra_verification = next(
            (
                verification
                for verification in verification_results
                if verification.infra_error is not None
            ),
            None,
        )
        if infra_verification is not None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unhealthy",
                blocker_reason=infra_verification.infra_error,
                result={
                    "command": infra_verification.original.argv,
                    "exit_code": infra_verification.original.exit_code,
                    "stdout_excerpt": infra_verification.stdout_excerpt,
                    "stderr_excerpt": infra_verification.stderr_excerpt,
                    "changed_files": touched,
                },
            )
        red_verifications = [
            verification
            for verification in verification_results
            if is_red_test_failure(verification)
        ]
        if not red_verifications:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_tests_not_red",
                blocker_reason="qa.author verification commands passed; expected failing tests",
                result={
                    "commands": [
                        verification.original.argv for verification in verification_results
                    ],
                    "stdout_excerpt": verification_results[-1].stdout_excerpt,
                    "stderr_excerpt": verification_results[-1].stderr_excerpt,
                    "changed_files": touched,
                },
            )
        contract = contract.model_copy(
            update={
                "feature_id": task_contract.feature_id,
                "task_id": task_id,
                "red_proof": [
                    proof
                    for verification in red_verifications
                    for proof in canonical_red_proof(verification)
                ],
                "paths_touched": sorted(set([*contract.paths_touched, *touched])),
                "branch": handle.branch,
                "worktree_path": str(handle.worktree),
                "model_usage_ids": [
                    *contract.model_usage_ids,
                    *([response.model_usage_id] if response.model_usage_id is not None else []),
                ],
            }
        )
        return HandlerResult.done(
            {
                "role": "qa",
                "task_id": task_id,
                "qa_author_contract": contract.model_dump(mode="json"),
                "gate_validation": gate_validation,
            }
        )
