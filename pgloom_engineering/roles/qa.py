from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from pgloom.harness.result import HandlerResult
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import (
    get_active_plan_contract,
    get_task_contract,
    list_task_contracts,
    list_task_handoffs,
)
from pgloom_engineering.contracts import (
    CommandRun,
    PlanContract,
    QAAuthorContract,
    QAResultContract,
    TaskContract,
    ValidationEvidence,
)
from pgloom_engineering.integrations.git import (
    changed_files,
    create_task_worktree,
    reset_worktree_to_ref,
)
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import get_project
from pgloom_engineering.qa_author_runtime import (
    add_configured_gate_matrix_coverage,
    build_qa_author_prompt,
    build_qa_code_repair_prompt,
    build_qa_contract_repair_prompt,
    build_qa_quality_repair_prompt,
    command_for_worktree,
    isolate_codex_worktree_context,
    normalize_qa_author_payload,
    path_violations,
    qa_code_repairable,
    qa_model_route,
    qa_quality_repairable,
    red_proof_verification_commands,
    route_model_command,
    semantic_quality_findings,
    verification_commands,
)
from pgloom_engineering.qa_runtime import (
    canonical_red_proof,
    command_with_env,
    hydrate_dependencies,
    is_authored_test_compile_failure,
    is_red_test_failure,
    qa_env,
    relevant_changed_files,
    run_qa_verification,
    validate_required_qa_gates,
)
from pgloom_engineering.role_context import build_role_context, record_role_context_usage
from pgloom_engineering.role_payloads import compact_plan_payload


class QAHandler:
    def __init__(self, *, provider: EngineeringCLIModelProvider | None = None) -> None:
        self._provider = provider

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        if task["task_type"] == "engineering.qa.author":
            return self._handle_author(task)
        if task["task_type"] == "engineering.qa.verify.scrutiny":
            return self._handle_verify(task, validator_type="scrutiny")
        if task["task_type"] == "engineering.qa.verify.usertest":
            return self._handle_verify(task, validator_type="usertest")
        if task["task_type"] == "engineering.qa":
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_deprecated_task_type",
                blocker_reason=(
                    "engineering.qa is deprecated; use split scrutiny/usertest validators"
                ),
            )
        return HandlerResult(
            status="blocked",
            blocker_code="engineering.qa_unknown_task_type",
            blocker_reason=f"unsupported task_type: {task['task_type']}",
        )

    def _handle_verify(
        self, task: dict[str, Any], *, validator_type: str
    ) -> HandlerResult:
        payload = dict(task.get("payload") or {})
        database_url = payload.get("database_url")
        task_id = str(task.get("id") or "")
        task_row = get_task_contract(task_id, database_url=database_url)
        if task_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.task_contract_missing",
                blocker_reason=f"qa.verify.{validator_type} requires a persisted TaskContract",
            )
        task_contract = TaskContract.model_validate(task_row["input_contract"])
        if task_contract.inputs.get("task_id") != task_id:
            task_contract = task_contract.model_copy(
                update={"inputs": {**task_contract.inputs, "task_id": task_id}}
            )
        plan_row = get_active_plan_contract(task_contract.feature_id, database_url=database_url)
        if plan_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.active_plan_missing",
                blocker_reason=f"qa.verify.{validator_type} requires an active PlanContract",
            )
        plan = PlanContract.model_validate(plan_row["contract"])
        project = get_project(plan.project, database_url=database_url)
        if project is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unregistered",
                blocker_reason=f"Project is not registered: {plan.project}",
            )
        if validator_type == "usertest" and _usertest_skip_authorized(project.metadata):
            contract = QAResultContract(
                feature_id=task_contract.feature_id,
                task_id=task_id,
                verdict="pass",
                validator_type="usertest",
                evidence=["metadata-authorized user-test skip for pure-library project"],
                validation_evidence=[
                    ValidationEvidence(
                        evidence_id=f"{task_id}:usertest-skip",
                        kind="integration_check",
                        summary=(
                            "User-test skipped because project metadata declares "
                            "usertest_harness.kind=none."
                        ),
                        verdict="pass",
                        metadata={"skip_authorized": True},
                    ).model_dump(mode="json")
                ],
                procedures_attestation=_procedures_attestation(task_contract),
            )
            return HandlerResult.done(
                {
                    "role": "qa",
                    "task_id": task_id,
                    "qa_result_contract": contract.model_dump(mode="json"),
                }
            )
        verify_root = _qa_verify_worktree(
            task_contract,
            task_id=task_id,
            database_url=database_url,
        )
        if verify_root is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_verify_worktree_missing",
                blocker_reason=(
                    f"qa.verify.{validator_type} requires an authored worktree path "
                    "from QA handoff"
                ),
            )
        if validator_type == "usertest":
            return self._handle_user_test(
                task=task,
                task_contract=task_contract,
                plan=plan,
                project=project,
                verify_root=verify_root,
                database_url=database_url,
            )
        workflow_id = (
            task.get("workflow_id")
            or payload.get("workflow_id")
            or payload.get("feature_id")
            or task_contract.feature_id
        )
        verification_results = [
            run_qa_verification(
                command,
                worktree=verify_root,
                project_metadata=project.metadata,
                timeout_seconds=get_settings().qa_author_invocation_timeout_seconds,
                database_url=database_url,
                workflow_id=str(workflow_id),
                task_id=task_id,
                feature_id=task_contract.feature_id,
            )
            for command in verification_commands(task_contract)
        ]
        infra_findings = [
            item.infra_error
            for item in verification_results
            if item.infra_error is not None
        ]
        command_findings = _qa_verify_command_findings(verification_results)
        contract = QAResultContract(
            feature_id=task_contract.feature_id,
            task_id=task_id,
            verdict="pass"
            if all(item.original.exit_code == 0 for item in verification_results)
            else "fail",
            commands=[item.original.argv for item in verification_results],
            commands_run=[
                CommandRun(
                    cmd=item.original.argv,
                    exit_code=item.original.exit_code,
                    duration_s=item.original.duration_seconds,
                    artifact_ids=[
                        str(artifact_id)
                        for artifact_id in [
                            item.artifact_id_unfiltered_stdout,
                            item.artifact_id_unfiltered_stderr,
                        ]
                        if artifact_id is not None
                    ],
                ).model_dump(mode="json")
                for item in verification_results
            ],
            evidence=[
                item.stdout_excerpt or item.stderr_excerpt or f"exit_code={item.original.exit_code}"
                for item in verification_results
            ],
            validation_evidence=[
                ValidationEvidence(
                    evidence_id=f"{task_id}:command:{index}",
                    kind="ui_exercise" if validator_type == "usertest" else "test_run",
                    summary=(
                        item.stdout_excerpt
                        or item.stderr_excerpt
                        or f"command exited {item.original.exit_code}"
                    ),
                    verdict="pass" if item.original.exit_code == 0 else "fail",
                    command_run_ids=[str(index)],
                    artifact_ids=[
                        str(artifact_id)
                        for artifact_id in [
                            item.artifact_id_unfiltered_stdout,
                            item.artifact_id_unfiltered_stderr,
                        ]
                        if artifact_id is not None
                    ],
                    metadata={"validator_type": validator_type},
                ).model_dump(mode="json")
                for index, item in enumerate(verification_results)
            ],
            findings=[*infra_findings, *command_findings],
            validator_type=validator_type,  # type: ignore[arg-type]
            procedures_attestation=_procedures_attestation(task_contract),
        )
        if infra_findings:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unhealthy",
                blocker_reason=infra_findings[0],
                result={"qa_result_contract": contract.model_dump(mode="json")},
            )
        if contract.verdict != "pass":
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_verify_failed",
                blocker_reason=(
                    command_findings[0]
                    if command_findings
                    else f"qa.verify.{validator_type} command failed"
                ),
                result={"qa_result_contract": contract.model_dump(mode="json")},
            )
        return HandlerResult.done(
            {
                "role": "qa",
                "task_id": task_id,
                "qa_result_contract": contract.model_dump(mode="json"),
            }
        )

    def _handle_user_test(
        self,
        *,
        task: dict[str, Any],
        task_contract: TaskContract,
        plan: PlanContract,
        project: Any,
        verify_root: Path,
        database_url: str | None,
    ) -> HandlerResult:
        settings = get_settings()
        task_id = str(task.get("id") or "")
        command = getattr(settings, "qa_validation_command", settings.qa_author_command)
        profile = CLIModelProfile(
            name=str(getattr(settings, "qa_validation_profile", "qa-validation")),
            command=command_with_env(
                isolate_codex_worktree_context(
                    route_model_command(
                        command_for_worktree(command, verify_root),
                        claude_model=str(getattr(settings, "qa_validation_claude_model", "haiku")),
                        codex_model=str(getattr(settings, "qa_validation_codex_model", "gpt-5.4")),
                        codex_reasoning=str(
                            getattr(settings, "qa_validation_codex_reasoning", "medium")
                        ),
                    ),
                    worktree=verify_root,
                    context_root=getattr(settings, "role_model_context_root", Path(".")),
                    enabled=bool(
                        getattr(settings, "role_model_context_isolation_enabled", False)
                        or getattr(
                            settings,
                            "qa_validation_model_context_isolation_enabled",
                            False,
                        )
                    ),
                ),
                qa_env(project.metadata, project_root=project.root),
            ),
            timeout_seconds=getattr(
                settings,
                "qa_validation_invocation_timeout_seconds",
                settings.qa_author_invocation_timeout_seconds,
            ),
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        role_context = build_role_context(
            role="qa.usertest",
            project=project,
            plan=plan,
            task_contract=task_contract,
            workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
            database_url=database_url,
        )
        response = provider.invoke(
            profile=profile,
            prompt=build_qa_usertest_prompt(
                plan=plan,
                task_contract=task_contract,
                worktree=verify_root,
                project_metadata=project.metadata,
                role_context=role_context.prompt_payload(),
            ),
            workflow_id=task_contract.feature_id,
            task_id=task_id,
        )
        token_savior_usage_ids: list[int] = []
        usage_id = record_role_context_usage(
            role_context,
            feature_id=task_contract.feature_id,
            workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
            task_id=task_id,
            profile_name=profile.name,
            model_usage_id=response.model_usage_id,
            database_url=database_url,
        )
        if usage_id is not None:
            token_savior_usage_ids.append(usage_id)
        try:
            contract = QAResultContract.model_validate(
                normalize_qa_result_payload(extract_json(response.text))
            )
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_usertest_contract_invalid",
                blocker_reason=str(exc),
                result={"raw_response": response.text},
            )
        contract = contract.model_copy(
            update={
                "feature_id": task_contract.feature_id,
                "task_id": task_id,
                "validator_type": "usertest",
                "procedures_attestation": {
                    **contract.procedures_attestation,
                    **_procedures_attestation(task_contract),
                },
            }
        )
        broad_command = _first_broad_usertest_command(contract)
        if broad_command is not None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_usertest_failed",
                blocker_reason=(
                    "qa.verify.usertest must exercise the feature through a user-facing "
                    "flow and must not substitute broad project test/check commands: "
                    f"{' '.join(broad_command)}"
                ),
                result={
                    "role": "qa",
                    "task_id": task_id,
                    "qa_result_contract": contract.model_dump(mode="json"),
                },
            )
        result = {
            "role": "qa",
            "task_id": task_id,
            "qa_result_contract": contract.model_dump(mode="json"),
            "token_savior_usage_ids": token_savior_usage_ids,
        }
        if contract.verdict != "pass":
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_usertest_failed",
                blocker_reason="qa.verify.usertest model reported non-pass verdict",
                result=result,
            )
        return HandlerResult.done(result)

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
        if task_contract.inputs.get("task_id") != task_id:
            task_contract = task_contract.model_copy(
                update={"inputs": {**task_contract.inputs, "task_id": task_id}}
            )
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
        if int(task.get("attempt") or 0) > 1:
            reset_worktree_to_ref(worktree=handle.worktree, base_ref=project.base_branch)
        hydrate_dependencies(project.root, handle.worktree, project.metadata)

        model_tier = "default"
        if int(task.get("attempt") or 0) >= int(
            getattr(settings, "qa_author_escalate_after_attempts", 2)
        ):
            model_tier = "escalate"

        profile = CLIModelProfile(
            name=settings.qa_author_profile,
            command=command_with_env(
                isolate_codex_worktree_context(
                    route_model_command(
                        command_for_worktree(settings.qa_author_command, handle.worktree),
                        **qa_model_route(project.metadata, settings, tier=model_tier),
                    ),
                    worktree=handle.worktree,
                    context_root=getattr(settings, "role_model_context_root", Path(".")),
                    enabled=bool(
                        getattr(settings, "role_model_context_isolation_enabled", False)
                        or getattr(
                            settings,
                            "qa_author_model_context_isolation_enabled",
                            False,
                        )
                    ),
                ),
                qa_env(project.metadata, project_root=project.root),
            ),
            timeout_seconds=settings.qa_author_invocation_timeout_seconds,
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        role_context = build_role_context(
            role="qa.author",
            project=project,
            plan=plan,
            task_contract=task_contract,
            workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
            database_url=database_url,
        )
        response = provider.invoke(
            profile=profile,
            prompt=build_qa_author_prompt(
                plan,
                task_contract,
                project_metadata=project.metadata,
                project_root=handle.worktree,
                role_context=role_context.prompt_payload(),
            ),
            workflow_id=task_contract.feature_id,
            task_id=task_id,
        )
        model_usage_ids = []
        token_savior_usage_ids = []
        if response.model_usage_id is not None:
            model_usage_ids.append(response.model_usage_id)
        usage_id = record_role_context_usage(
            role_context,
            feature_id=task_contract.feature_id,
            workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
            task_id=task_id,
            profile_name=profile.name,
            model_usage_id=response.model_usage_id,
            database_url=database_url,
        )
        if usage_id is not None:
            token_savior_usage_ids.append(usage_id)
        repair_attempts = 0
        contract_repair_attempts = 0
        quality_repair_attempts = 0
        max_repair_attempts = 2
        max_contract_repair_attempts = 1
        max_quality_repair_attempts = 2
        while True:
            try:
                contract = QAAuthorContract.model_validate(
                    normalize_qa_author_payload(extract_json(response.text))
                )
            except Exception as exc:
                touched = relevant_changed_files(changed_files(handle.worktree), project.metadata)
                if touched and contract_repair_attempts < max_contract_repair_attempts:
                    contract_repair_attempts += 1
                    response = provider.invoke(
                        profile=profile,
                        prompt=build_qa_contract_repair_prompt(
                            plan=plan,
                            task_contract=task_contract,
                            worktree=handle.worktree,
                            changed_files=touched,
                            raw_response=response.text,
                            validation_error=str(exc),
                        ),
                        workflow_id=task_contract.feature_id,
                        task_id=task_id,
                    )
                    if response.model_usage_id is not None:
                        model_usage_ids.append(response.model_usage_id)
                    usage_id = record_role_context_usage(
                        role_context,
                        feature_id=task_contract.feature_id,
                        workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
                        task_id=task_id,
                        profile_name=profile.name,
                        model_usage_id=response.model_usage_id,
                        database_url=database_url,
                    )
                    if usage_id is not None:
                        token_savior_usage_ids.append(usage_id)
                    continue
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_author_contract_invalid",
                    blocker_reason=str(exc),
                    result={
                        "raw_response": response.text,
                        "changed_files": touched,
                        "contract_repair_attempts": contract_repair_attempts,
                        "repair_attempts": repair_attempts,
                    },
                )
            touched = relevant_changed_files(changed_files(handle.worktree), project.metadata)
            violations = path_violations(touched, task_contract, project.metadata)
            if violations:
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_path_violation",
                    blocker_reason="qa.author touched paths outside its contract",
                    result={"violations": violations, "changed_files": touched},
                )
            gate_validation = validate_required_qa_gates(handle.worktree, project.metadata)
            gate_failures = [
                item for item in gate_validation if item.get("status") != "configured"
            ]
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
                quality_review = {"blocking_findings": blocking_semantic_findings}
                if (
                    quality_repair_attempts < max_quality_repair_attempts
                    and qa_quality_repairable(quality_review)
                ):
                    quality_repair_attempts += 1
                    response = provider.invoke(
                        profile=profile,
                        prompt=build_qa_quality_repair_prompt(
                            plan=plan,
                            task_contract=task_contract,
                            worktree=handle.worktree,
                            changed_files=touched,
                            quality_review=quality_review,
                            current_contract=contract.model_dump(mode="json"),
                            project_metadata=project.metadata,
                        ),
                        workflow_id=task_contract.feature_id,
                        task_id=task_id,
                    )
                    if response.model_usage_id is not None:
                        model_usage_ids.append(response.model_usage_id)
                    usage_id = record_role_context_usage(
                        role_context,
                        feature_id=task_contract.feature_id,
                        workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
                        task_id=task_id,
                        profile_name=profile.name,
                        model_usage_id=response.model_usage_id,
                        database_url=database_url,
                    )
                    if usage_id is not None:
                        token_savior_usage_ids.append(usage_id)
                    continue
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_semantic_quality_failed",
                    blocker_reason="qa.author output failed deterministic semantic quality review",
                    result={
                        "findings": blocking_semantic_findings,
                        "gate_validation": gate_validation,
                        "changed_files": touched,
                        "quality_repair_attempts": quality_repair_attempts,
                        "contract_repair_attempts": contract_repair_attempts,
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
                for command in red_proof_verification_commands(task_contract, touched)
            ]
            touched = relevant_changed_files(changed_files(handle.worktree), project.metadata)
            violations = path_violations(touched, task_contract, project.metadata)
            if violations:
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_path_violation",
                    blocker_reason="qa.author touched paths outside its contract",
                    result={"violations": violations, "changed_files": touched},
                )
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
            if not touched:
                if repair_attempts < max_repair_attempts:
                    repair_attempts += 1
                    response = provider.invoke(
                        profile=profile,
                        prompt=build_qa_no_changes_repair_prompt(
                            plan=plan,
                            task_contract=task_contract,
                            worktree=handle.worktree,
                            current_contract=contract.model_dump(mode="json"),
                            project_metadata=project.metadata,
                            project_root=project.root,
                            role_context=role_context.prompt_payload(),
                        ),
                        workflow_id=task_contract.feature_id,
                        task_id=task_id,
                    )
                    if response.model_usage_id is not None:
                        model_usage_ids.append(response.model_usage_id)
                    usage_id = record_role_context_usage(
                        role_context,
                        feature_id=task_contract.feature_id,
                        workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
                        task_id=task_id,
                        profile_name=profile.name,
                        model_usage_id=response.model_usage_id,
                        database_url=database_url,
                    )
                    if usage_id is not None:
                        token_savior_usage_ids.append(usage_id)
                    continue
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_no_changes",
                    blocker_reason="qa.author did not produce any relevant QA file changes",
                    result={
                        "commands": [
                            verification.original.argv for verification in verification_results
                        ],
                        "changed_files": touched,
                    },
                )
            red_verifications = [
                verification
                for verification in verification_results
                if is_red_test_failure(verification)
            ]
            if red_verifications:
                break
            compile_failures = [
                verification
                for verification in verification_results
                if is_authored_test_compile_failure(verification)
            ]
            verification = compile_failures[0] if compile_failures else verification_results[-1]
            repair_outcome = {
                "findings": [
                    {
                        "code": "qa_tests_do_not_compile"
                        if compile_failures
                        else "tests_not_red"
                    }
                ],
                "changed_files": touched,
            }
            if repair_attempts < max_repair_attempts and qa_code_repairable(repair_outcome):
                repair_attempts += 1
                response = provider.invoke(
                    profile=profile,
                    prompt=build_qa_code_repair_prompt(
                        plan=plan,
                        task_contract=task_contract,
                        worktree=handle.worktree,
                        changed_files=touched,
                        verification_command=verification.original.argv,
                        stdout_excerpt=verification.stdout_excerpt,
                        stderr_excerpt=verification.stderr_excerpt,
                        current_contract=contract.model_dump(mode="json"),
                        project_metadata=project.metadata,
                    ),
                    workflow_id=task_contract.feature_id,
                    task_id=task_id,
                )
                if response.model_usage_id is not None:
                    model_usage_ids.append(response.model_usage_id)
                usage_id = record_role_context_usage(
                    role_context,
                    feature_id=task_contract.feature_id,
                    workflow_id=str(task.get("workflow_id") or task_contract.feature_id),
                    task_id=task_id,
                    profile_name=profile.name,
                    model_usage_id=response.model_usage_id,
                    database_url=database_url,
                )
                if usage_id is not None:
                    token_savior_usage_ids.append(usage_id)
                continue
            if compile_failures:
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.qa_tests_do_not_compile",
                    blocker_reason=(
                        "qa.author produced tests with compile/import/syntax errors; "
                        "authored tests must compile before review"
                    ),
                    result={
                        "commands": [
                            item.original.argv for item in verification_results
                        ],
                        "stdout_excerpt": verification.stdout_excerpt,
                        "stderr_excerpt": verification.stderr_excerpt,
                        "changed_files": touched,
                        "repair_attempts": repair_attempts,
                        "quality_repair_attempts": quality_repair_attempts,
                        "contract_repair_attempts": contract_repair_attempts,
                    },
                )
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
                    "repair_attempts": repair_attempts,
                    "quality_repair_attempts": quality_repair_attempts,
                    "contract_repair_attempts": contract_repair_attempts,
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
                "model_usage_ids": [*contract.model_usage_ids, *model_usage_ids],
            }
        )
        contract = add_configured_gate_matrix_coverage(
            contract,
            plan=plan,
            worktree=handle.worktree,
            project_metadata=project.metadata,
            task_contract=task_contract,
        )
        return HandlerResult.done(
            {
                "role": "qa",
                "task_id": task_id,
                "qa_author_contract": contract.model_dump(mode="json"),
                "gate_validation": gate_validation,
                "repair_attempts": repair_attempts,
                "quality_repair_attempts": quality_repair_attempts,
                "contract_repair_attempts": contract_repair_attempts,
                "token_savior_usage_ids": token_savior_usage_ids,
            }
        )


def _qa_verify_worktree(
    task_contract: TaskContract,
    *,
    task_id: str,
    database_url: str | None,
) -> Path | None:
    input_path = _worktree_path_from_payload(task_contract.inputs)
    if input_path is not None:
        return input_path

    for handoff in reversed(list_task_handoffs(task_id, database_url=database_url)):
        handoff_path = _worktree_path_from_payload(handoff.get("contract"))
        if handoff_path is not None:
            return handoff_path

    for dependency_task_id in task_contract.dependencies:
        dependency_row = get_task_contract(dependency_task_id, database_url=database_url)
        if dependency_row is None:
            continue
        dependency_path = _worktree_path_from_payload(dependency_row.get("output_contract"))
        if dependency_path is not None:
            return dependency_path
    try:
        task_rows = list_task_contracts(task_contract.feature_id, database_url=database_url)
    except Exception:
        task_rows = []
    for row in task_rows:
        dependency_path = _worktree_path_from_payload(row.get("output_contract"))
        if dependency_path is not None:
            return dependency_path
    return None


def _usertest_skip_authorized(metadata: dict[str, Any]) -> bool:
    harness = metadata.get("usertest_harness")
    return isinstance(harness, dict) and harness.get("kind") == "none"


def build_qa_usertest_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    project_metadata: dict[str, Any],
    role_context: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "role": "qa.usertest",
            "instructions": [
                "Act as a fresh-context user-test validator.",
                (
                    "Launch or exercise the application, service, library CLI, or documented "
                    "project harness from the provided worktree."
                ),
                (
                    "Do not rely only on static inspection. Execute realistic user-facing "
                    "flows or CLI/API calls that prove the feature behavior."
                ),
                (
                    "For libraries without a UI, write or run a small consumer-style program "
                    "or project command that uses the public API like an external user."
                ),
                (
                    "Record commands run, exit codes, artifacts, and typed validation evidence."
                ),
                (
                    "Do not run broad project regression commands such as ./gradlew test, "
                    "./gradlew check, ./qa/regression.sh, or full benchmark sweeps as a "
                    "substitute for user testing. Use focused consumer-style commands, "
                    "browser/computer-use flows, CLI/API calls, or the project-declared "
                    "feature smoke command only when it supports the user journey."
                ),
                (
                    "Return pass only when the exercised behavior satisfies the acceptance "
                    "criteria through the user-facing surface."
                ),
                "Return only a QAResultContract JSON object.",
            ],
            "worktree": str(worktree),
            "role_context": role_context or {},
            "plan_contract": compact_plan_payload(plan),
            "task_contract": task_contract.model_dump(mode="json"),
            "project_metadata": _safe_usertest_metadata(project_metadata),
            "required_response": {
                "contract_version": "engineering.contracts.v1",
                "feature_id": task_contract.feature_id,
                "task_id": task_contract.inputs.get("task_id") or "task id",
                "verdict": "pass",
                "validator_type": "usertest",
                "commands": [],
                "commands_run": [
                    {
                        "cmd": ["command", "args"],
                        "exit_code": 0,
                        "duration_s": 0.0,
                        "artifact_ids": [],
                    }
                ],
                "validation_evidence": [
                    {
                        "evidence_id": "stable evidence id",
                        "kind": "ui_exercise",
                        "summary": "What was launched or exercised and what passed.",
                        "verdict": "pass",
                        "command_run_ids": [],
                        "artifact_ids": [],
                        "metadata": {"surface": "browser|cli|api|library"},
                    }
                ],
                "evidence": ["short evidence summary"],
                "findings": [],
                "procedures_attestation": {},
            },
        },
        indent=2,
        sort_keys=True,
    )


def normalize_qa_result_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("QAResultContract"), dict):
        return _normalize_qa_result_contract_shape(payload["QAResultContract"])
    if isinstance(payload, dict) and isinstance(payload.get("qa_result_contract"), dict):
        return _normalize_qa_result_contract_shape(payload["qa_result_contract"])
    return _normalize_qa_result_contract_shape(payload)


def _normalize_qa_result_contract_shape(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    commands = normalized.get("commands")
    if isinstance(commands, list):
        normalized["commands"] = [_normalize_command_tokens(command) for command in commands]
    commands_run = normalized.get("commands_run")
    if isinstance(commands_run, list):
        normalized["commands_run"] = [
            _normalize_command_run(command_run) for command_run in commands_run
        ]
    procedures_attestation = normalized.get("procedures_attestation")
    if isinstance(procedures_attestation, dict):
        normalized["procedures_attestation"] = {
            str(key): _normalize_procedure_attestation(value)
            for key, value in procedures_attestation.items()
        }
    findings = normalized.get("findings")
    if isinstance(findings, list):
        normalized["findings"] = [_normalize_finding(finding) for finding in findings]
    return normalized


def _normalize_command_run(command_run: object) -> object:
    if not isinstance(command_run, dict):
        return command_run
    normalized = dict(command_run)
    if "cmd" in normalized:
        normalized["cmd"] = _normalize_command_tokens(normalized["cmd"])
    return normalized


def _normalize_command_tokens(command: object) -> object:
    if isinstance(command, str):
        return shlex.split(command)
    return command


def _normalize_finding(finding: object) -> str:
    if isinstance(finding, str):
        return finding
    if isinstance(finding, dict):
        parts = [
            str(finding[key])
            for key in ("severity", "assertion_id", "summary", "details")
            if finding.get(key)
        ]
        return " | ".join(parts) if parts else json.dumps(finding, sort_keys=True)
    return str(finding)


def _normalize_procedure_attestation(value: object) -> bool | str:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, dict):
        status = value.get("status")
        detail = value.get("notes") or value.get("note") or value.get("summary")
        parts = [str(part) for part in (status, detail) if part]
        return " - ".join(parts) if parts else json.dumps(value, sort_keys=True)
    return str(value)


def _first_broad_usertest_command(contract: QAResultContract) -> list[str] | None:
    commands: list[list[str]] = [*contract.commands]
    for command_run in contract.commands_run:
        raw_command = command_run.get("cmd")
        normalized = _normalize_command_tokens(raw_command)
        if isinstance(normalized, list):
            commands.append([str(part) for part in normalized])
    for command in commands:
        if _is_broad_project_validation_command(command):
            return command
    return None


def _is_broad_project_validation_command(command: list[str]) -> bool:
    if not command:
        return False
    tokens = [str(part) for part in command]
    command_text = " ".join(tokens)
    if "./qa/regression.sh" in command_text or "./qa/smoke.sh" in command_text:
        return True
    executable = tokens[0].split("/")[-1]
    if executable not in {"gradlew", "gradle"}:
        return False
    tasks = [token for token in tokens[1:] if not token.startswith("-")]
    return any(task in {"test", "check"} for task in tasks)


def _qa_verify_command_findings(verification_results: list[Any]) -> list[str]:
    findings: list[str] = []
    for item in verification_results:
        if item.original.exit_code == 0:
            continue
        excerpt = item.stderr_excerpt or item.stdout_excerpt or ""
        excerpt = " ".join(str(excerpt).split())
        command = " ".join(str(part) for part in item.original.argv)
        if excerpt:
            findings.append(
                f"qa.verify command failed: {command} exited "
                f"{item.original.exit_code}: {excerpt[:600]}"
            )
        else:
            findings.append(
                f"qa.verify command failed: {command} exited {item.original.exit_code}"
            )
    return findings


def build_qa_no_changes_repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    current_contract: object,
    project_metadata: dict[str, Any],
    project_root: Path,
    role_context: dict[str, Any] | None,
) -> str:
    payload = json.loads(
        build_qa_author_prompt(
            plan,
            task_contract,
            project_metadata=project_metadata,
            project_root=project_root,
            role_context=role_context,
        )
    )
    payload["role"] = "qa.author.no_changes_repair"
    payload["instructions"] = [
        (
            "Repair the QA author output because the previous response produced "
            "no relevant file changes in the worktree."
        ),
        "Create or edit tests/QA files under allowed_paths; do not edit production source files.",
        (
            "The returned QAAuthorContract must name the concrete tests added and "
            "must correspond to actual files changed in the worktree."
        ),
        *payload.get("instructions", []),
    ]
    payload["worktree"] = str(worktree)
    payload["previous_contract"] = current_contract
    return json.dumps(payload, indent=2, sort_keys=True)


def _safe_usertest_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "usertest_harness",
        "qa",
        "smoke_command",
        "regression_command",
        "relevant_paths",
        "agent_topology",
    ]
    return {key: metadata[key] for key in keys if key in metadata}


def _procedures_attestation(task_contract: TaskContract) -> dict[str, bool | str]:
    if not task_contract.required_procedures:
        return {}
    return {procedure: True for procedure in task_contract.required_procedures}


def _worktree_path_from_payload(payload: Any) -> Path | None:
    if not isinstance(payload, dict):
        return None
    raw_contract = payload.get("qa_author_contract")
    if isinstance(raw_contract, dict) and raw_contract.get("worktree_path"):
        return Path(str(raw_contract["worktree_path"]))
    raw_path = payload.get("worktree_path")
    if raw_path:
        return Path(str(raw_path))
    return None
