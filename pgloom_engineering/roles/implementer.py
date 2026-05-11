from __future__ import annotations

import difflib
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol

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
from pgloom_engineering.role_payloads import compact_plan_payload, compact_qa_author_payload


class ImplementerHandler:
    def __init__(self, *, provider: ImplementerModelProvider | None = None) -> None:
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
                        or getattr(
                            settings,
                            "implementer_model_context_isolation_enabled",
                            False,
                        )
                    ),
                    add_dir_enabled=bool(
                        getattr(settings, "implementer_model_context_add_dir_enabled", True)
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
        baseline_contents = _changed_file_contents(worktree, project.metadata)
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
        max_repair_attempts = max(
            0,
            int(getattr(settings, "implementer_inline_repair_attempts", 0)),
        )
        while True:
            touched = _implementation_changed_files(worktree, baseline, project.metadata)
            violations = [
                *implementation_path_violations(touched, task_contract),
                *_dirty_forbidden_path_violations(
                    relevant_changed_files(changed_files(worktree), project.metadata),
                    touched=touched,
                    task_contract=task_contract,
                    qa_contract=qa_contract,
                ),
                *_dirty_scope_path_violations(
                    relevant_changed_files(changed_files(worktree), project.metadata),
                    touched=touched,
                    task_contract=task_contract,
                    qa_contract=qa_contract,
                ),
            ]
            if violations:
                violation_diffs = _path_violation_diffs(
                    worktree,
                    baseline_contents,
                    [item["path"] for item in violations if item.get("path")],
                )
                _restore_paths(worktree, baseline_contents, violation_diffs.keys())
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.implementation_path_violation",
                    blocker_reason="implementer touched paths outside its contract",
                    result={
                        "violations": violations,
                        "violation_diffs": violation_diffs,
                        "changed_files": touched,
                        "repair_attempts": repair_attempts,
                    },
                )
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

            reported_blockers = output.blockers if output is not None else []
            if (
                (contract_error or failed_verifications or reported_blockers)
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
                        path_violations=[],
                        failed_verifications=failed_verifications,
                        contract_error=contract_error
                        or _reported_blockers_error(reported_blockers),
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
            if failed_verifications:
                first = failed_verifications[0]
                blocker_reason = _verification_blocker_reason(first, worktree=worktree)
                return HandlerResult(
                    status="blocked",
                    blocker_code="engineering.implementation_verification_failed",
                    blocker_reason=blocker_reason,
                    result={
                        "commands": [item.original.argv for item in verification_results],
                        "stdout_excerpt": first.stdout_excerpt,
                        "stderr_excerpt": first.stderr_excerpt,
                        "artifact_hints": _verification_artifact_hints(
                            first,
                            worktree=worktree,
                        ),
                        "changed_files": touched,
                        "repair_attempts": repair_attempts,
                    },
                )
            if output is None:
                raise AssertionError("TaskResultContract unexpectedly missing after validation")
            commands_run = _commands_run_from_verification_results(verification_results)
            reported_blockers = list(output.blockers)
            output = output.model_copy(
                update={
                    "feature_id": task_contract.feature_id,
                    "task_id": task_id,
                    "changed_files": sorted(set([*output.changed_files, *touched])),
                    "branch": output.branch or qa_contract.branch,
                    "worktree_path": str(worktree),
                    "blockers": [],
                    "deviations": [
                        *output.deviations,
                        *[
                            f"reported_blocker_cleared_by_orchestrator_verification: {item}"
                            for item in reported_blockers
                        ],
                    ],
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
                    "commands_run": [
                        *output.commands_run,
                        *commands_run,
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


class ImplementerModelProvider(Protocol):
    def invoke(self, *, profile: CLIModelProfile, prompt: str, **kwargs: Any) -> Any: ...


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
                (
                    "Treat the TaskContract objective as the scope boundary even when "
                    "allowed_paths are broad. Do not implement variants, modules, or later "
                    "plan slices that are outside this task objective just because their "
                    "paths are allowed."
                ),
                (
                    "Keep source inspection targeted: use rg for symbol discovery and read "
                    "only the smallest relevant file ranges before editing."
                ),
                (
                    "Do not paste full file contents, full diffs, or full command logs into "
                    "the response; summarize and reference paths/commands instead."
                ),
                (
                    "Run exactly the TaskContract verification_commands before returning. "
                    "Do not add or substitute broad project gates such as ./gradlew test, "
                    "./gradlew check, ./qa/smoke.sh, ./qa/regression.sh, or full JMH sweeps "
                    "unless that exact command is listed in the TaskContract."
                ),
                (
                    "Preserve existing storage invariants while implementing new access "
                    "patterns: do not wrap raw slot ids modulo slotCount, do not widen point "
                    "APIs to accept invalid or out-of-range slots, do not accept partial "
                    "payload lengths for fixed-size non-meta stores, and do not infer payload "
                    "length by trimming trailing zero bytes."
                ),
                "Return only a TaskResultContract JSON object.",
            ],
            "worktree": str(worktree),
            "role_context": role_context or {},
            "implementer_context_capsule": build_implementer_context_capsule(
                plan=plan,
                task_contract=task_contract,
                qa_contract=qa_contract,
                role_context=role_context,
            ),
            "source_starter_pack": build_implementer_source_starter_pack(
                worktree=worktree,
                task_contract=task_contract,
                qa_contract=qa_contract,
                role_context=role_context,
            ),
            "plan_contract": compact_plan_payload(plan),
            "task_contract": task_contract.model_dump(mode="json"),
            "qa_author_contract": compact_qa_author_payload(qa_contract),
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
                (
                    "If earlier blockers were caused by stale sandbox or command "
                    "errors that now pass, return blockers=[] and include the "
                    "successful commands in commands_run."
                ),
                (
                    "Use the previous_response_summary for orientation only; do not "
                    "re-read broad source surfaces unless the failing evidence points there."
                ),
                (
                    "Rerun only the TaskContract verification_commands. Do not add broad "
                    "project gates such as ./gradlew test, ./gradlew check, ./qa/smoke.sh, "
                    "./qa/regression.sh, or full JMH sweeps unless that exact command is "
                    "listed in the TaskContract."
                ),
                (
                    "When repairing range, visitor, or read/write behavior, preserve existing "
                    "storage invariants: no modulo wrapping of raw slot ids, no point-API "
                    "acceptance of invalid/out-of-range slots, no partial payload writes for "
                    "fixed-size non-meta stores, and no trailing-zero trimming to infer "
                    "payload length."
                ),
                "Return only a valid TaskResultContract JSON object.",
            ],
            "worktree": str(worktree),
            "role_context": role_context or {},
            "implementer_context_capsule": build_implementer_context_capsule(
                plan=plan,
                task_contract=task_contract,
                qa_contract=qa_contract,
                role_context=role_context,
            ),
            "plan_contract": compact_plan_payload(plan),
            "task_contract": task_contract.model_dump(mode="json"),
            "qa_author_contract": compact_qa_author_payload(qa_contract),
            "changed_files": changed_files,
            "path_violations": path_violations,
            "contract_error": contract_error,
            "failed_verifications": [
                {
                    "command": item.original.argv,
                    "exit_code": item.original.exit_code,
                    "stdout_excerpt": item.stdout_excerpt,
                    "stderr_excerpt": item.stderr_excerpt,
                    "artifact_hints": _verification_artifact_hints(item, worktree=worktree),
                }
                for item in failed_verifications
            ],
            "previous_response_summary": _compact_previous_response(raw_response),
        },
        indent=2,
        sort_keys=True,
    )


def build_implementer_context_capsule(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
    role_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = role_context or {}
    qa_tests = _string_list(qa_contract.tests_added)
    qa_paths = _string_list(qa_contract.paths_touched)
    acceptance = _string_list(task_contract.inputs.get("acceptance_assertion_ids"))
    return {
        "contract": "engineering.implementer_context_capsule.v1",
        "purpose": (
            "Use this compact capsule as the first recall surface before broad "
            "source reads. If more code context is needed, query by symbol/path and "
            "read only the smallest relevant ranges."
        ),
        "slice": {
            "task_type": task_contract.task_type,
            "objective": task_contract.objective,
            "allowed_paths": _string_list(task_contract.allowed_paths),
            "forbidden_paths": _string_list(task_contract.forbidden_paths),
            "scope_boundary": (
                "Only implement behavior named by this task objective and its "
                "acceptance_assertion_ids. Broad allowed_paths are path permissions, "
                "not permission to complete future slices."
            ),
            "acceptance_assertion_ids": acceptance,
            "verification_commands": task_contract.verification_commands,
            "required_procedures": _string_list(task_contract.required_procedures),
        },
        "design_constraints": {
            "public_api": plan.design_contract.public_api,
            "hard_constraints": _string_list(plan.design_contract.hard_constraints),
            "forbidden_alternatives": _string_list(plan.design_contract.forbidden_alternatives),
            "acceptance_tests": _string_list(plan.design_contract.acceptance_tests),
        },
        "qa_handoff": {
            "tests_added": qa_tests,
            "paths_touched": qa_paths,
            "matrix_coverage": qa_contract.matrix_coverage,
            "red_proof_commands": [
                item.get("command")
                for item in qa_contract.red_proof
                if isinstance(item, dict) and item.get("command")
            ],
        },
        "recovery_context": _compact_recovery_context(
            task_contract.inputs.get("replan_context")
        ),
        "recall": {
            "relevant_paths": _string_list(context.get("relevant_paths")),
            "qa_write_paths": _string_list(context.get("qa_write_paths")),
            "memory_digest": _compact_text(context.get("memory_digest"), limit=1800),
            "packed_context": _compact_text(context.get("packed_context"), limit=3200),
            "token_savior": context.get("token_savior") or {},
            "source_queries": _implementer_source_queries(
                task_contract=task_contract,
                qa_tests=[*qa_tests, *qa_paths],
                relevant_paths=_string_list(context.get("relevant_paths")),
            ),
        },
    }


def _compact_recovery_context(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = [
        "mode",
        "blocker_code",
        "blocker_reason",
        "failure_context",
        "blocked_slice_id",
        "same_blocker_recovery_count",
        "summary",
    ]
    compact: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item in (None, "", []):
            continue
        if isinstance(item, str):
            compact[key] = _compact_text(item, limit=1600)
        elif isinstance(item, int | float | bool):
            compact[key] = item
        else:
            compact[key] = _compact_text(str(item), limit=1600)
    return compact


def build_implementer_source_starter_pack(
    *,
    worktree: Path,
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
    role_context: dict[str, Any] | None,
    max_source_files: int = 6,
    max_test_files: int = 4,
    max_file_chars: int = 4200,
    max_total_chars: int = 24000,
) -> dict[str, Any]:
    query = " ".join(
        [
            task_contract.objective,
            " ".join(task_contract.expected_outputs),
            " ".join(_string_list(task_contract.inputs.get("acceptance_assertion_ids"))),
            " ".join(_string_list((role_context or {}).get("relevant_paths"))),
            " ".join(qa_contract.tests_added),
            " ".join(qa_contract.paths_touched),
        ]
    )
    terms = _source_brief_terms(query)
    relevant_paths = _string_list((role_context or {}).get("relevant_paths"))
    source_paths = _ranked_source_brief_paths(
        worktree=worktree,
        roots=[*task_contract.allowed_paths, *relevant_paths],
        terms=terms,
        forbidden_paths=task_contract.forbidden_paths,
        limit=max_source_files,
    )
    test_paths = _ranked_source_brief_paths(
        worktree=worktree,
        roots=[*qa_contract.tests_added, *qa_contract.paths_touched],
        terms=terms,
        forbidden_paths=[],
        limit=max_test_files,
    )
    remaining = max_total_chars
    source_files, remaining = _source_brief_entries(
        worktree,
        source_paths,
        max_file_chars=max_file_chars,
        remaining_chars=remaining,
    )
    test_files, remaining = _source_brief_entries(
        worktree,
        test_paths,
        max_file_chars=max_file_chars,
        remaining_chars=remaining,
    )
    return {
        "contract": "engineering.implementer_source_starter_pack.v1",
        "purpose": (
            "Use these bounded excerpts before shelling out to read files. They are "
            "intended to reduce broad source exploration; read more only when an exact "
            "symbol or compile error requires it."
        ),
        "query_terms": terms[:16],
        "source_files": source_files,
        "read_only_qa_test_files": test_files,
        "omitted": {
            "source_candidate_count": max(0, len(source_paths) - len(source_files)),
            "qa_test_candidate_count": max(0, len(test_paths) - len(test_files)),
            "remaining_chars": remaining,
        },
    }


def normalize_task_result_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("TaskResultContract"), dict):
        return normalize_task_result_payload(payload["TaskResultContract"])
    if isinstance(payload, dict) and isinstance(payload.get("task_result_contract"), dict):
        return normalize_task_result_payload(payload["task_result_contract"])
    if isinstance(payload, dict):
        normalized = dict(payload)
        normalized["checks"] = _normalize_check_items(normalized.get("checks"))
        normalized["commands_run"] = _normalize_check_items(normalized.get("commands_run"))
        for key in (
            "changed_files",
            "artifacts",
            "deviations",
            "blockers",
        ):
            if key in normalized:
                normalized[key] = _normalize_string_items(normalized.get(key))
        return normalized
    return payload


def _normalize_string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string_item(item) for item in value if item is not None]


def _string_item(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _normalize_check_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
        elif item is not None:
            normalized.append({"name": str(item), "status": "reported"})
    return normalized


def _reported_blockers_error(blockers: list[str]) -> str | None:
    if not blockers:
        return None
    return (
        "TaskResultContract.blockers must be empty when orchestrator verification "
        f"passes; reported blockers: {blockers}"
    )


def _verification_blocker_reason(item: Any, *, worktree: Path | None = None) -> str:
    original = getattr(item, "original", None)
    command = " ".join(str(part) for part in getattr(original, "argv", []) or [])
    excerpts = [
        " ".join(str(value).split())
        for value in (
            getattr(item, "stderr_excerpt", ""),
            getattr(item, "stdout_excerpt", ""),
        )
        if str(value).strip()
    ]
    benchmark_diagnostic = _benchmark_smoke_diagnostic(item, worktree=worktree)
    if benchmark_diagnostic:
        excerpts.insert(0, benchmark_diagnostic)
    verification_diagnostic = _verification_failure_diagnostic(item, worktree=worktree)
    if verification_diagnostic:
        excerpts.insert(0, verification_diagnostic)
    details = " | ".join(excerpts)
    reason = "implementer verification commands failed"
    if command:
        reason += f": {command}"
    if details:
        reason += f": {details}"
    return reason[:1200]


def _verification_artifact_hints(item: Any, *, worktree: Path) -> dict[str, Any]:
    original = getattr(item, "original", None)
    argv = [str(part) for part in getattr(original, "argv", []) or []]
    hints: dict[str, Any] = {}
    if any("jmhSmokeCheck" in part for part in argv):
        diagnostic = _benchmark_smoke_diagnostic(item, worktree=worktree)
        if diagnostic:
            hints["benchmark_smoke_diagnostic"] = diagnostic
        jmh_txt = _read_text_if_exists(worktree / "benchmarks/build/jmh.txt")
        if jmh_txt:
            hints["jmh_text_tail"] = _tail_text(jmh_txt, limit=5000)
        benchmarks = _jmh_result_benchmarks(worktree / "benchmarks/build/jmh.json")
        if benchmarks:
            hints["jmh_result_benchmarks"] = benchmarks[:30]
    failure_lines = _verification_failure_lines(item)
    if failure_lines:
        hints["failure_output_lines"] = failure_lines
    test_failures = _gradle_test_report_failures(worktree)
    if test_failures:
        hints["gradle_test_failures"] = test_failures
    return hints


def _verification_failure_diagnostic(item: Any, *, worktree: Path | None) -> str:
    failures = _gradle_test_report_failures(worktree) if worktree is not None else []
    if failures:
        rendered: list[str] = []
        for failure in failures[:5]:
            location = failure.get("test") or failure.get("suite") or failure.get("path")
            message = failure.get("message") or failure.get("type") or "failed"
            rendered.append(f"{location}: {message}")
        if rendered:
            return "gradle_test_failures: " + " | ".join(rendered)
    lines = _verification_failure_lines(item)
    if lines:
        return "verification_failure_lines: " + " | ".join(lines[:8])
    return ""


def _verification_failure_lines(item: Any, *, limit: int = 16) -> list[str]:
    original = getattr(item, "original", None)
    combined = "\n".join(
        part
        for part in [
            str(getattr(original, "stdout", "") or ""),
            str(getattr(original, "stderr", "") or ""),
        ]
        if part
    )
    if not combined:
        return []
    signals = (
        " failed",
        "failed ",
        "failure:",
        "failures:",
        "error:",
        "assertionerror",
        "expected:",
        "expected <",
        "expected but was",
        "comparisonfailure",
        "cannot find symbol",
        "incompatible types",
        "there were failing tests",
    )
    lines: list[str] = []
    for raw_line in combined.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        lowered = line.lower()
        if any(signal in lowered for signal in signals):
            lines.append(line[:500])
    return list(dict.fromkeys(lines))[:limit]


def _gradle_test_report_failures(
    worktree: Path,
    *,
    max_files: int = 40,
    max_failures: int = 12,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    paths = [
        path
        for path in sorted(worktree.rglob("TEST-*.xml"))
        if "/build/test-results/" in f"/{path.relative_to(worktree).as_posix()}"
    ][:max_files]
    for path in paths:
        relative = path.relative_to(worktree).as_posix()
        try:
            root = ET.parse(path).getroot()
        except Exception:
            continue
        suite_name = str(root.attrib.get("name") or "")
        for testcase in root.iter("testcase"):
            failure_node = None
            for child in testcase:
                if child.tag in {"failure", "error"}:
                    failure_node = child
                    break
            if failure_node is None:
                continue
            class_name = str(testcase.attrib.get("classname") or "")
            test_name = str(testcase.attrib.get("name") or "")
            test_id = ".".join(part for part in [class_name, test_name] if part)
            message = str(failure_node.attrib.get("message") or "").strip()
            failure_type = str(failure_node.attrib.get("type") or failure_node.tag)
            text = " ".join(str(failure_node.text or "").split())
            failures.append(
                {
                    "path": relative,
                    "suite": suite_name,
                    "test": test_id,
                    "type": failure_type,
                    "message": _compact_failure_text(message or text, limit=500),
                    "details": _compact_failure_text(text, limit=900),
                }
            )
            if len(failures) >= max_failures:
                return failures
    return failures


def _compact_failure_text(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _benchmark_smoke_diagnostic(item: Any, *, worktree: Path | None) -> str:
    original = getattr(item, "original", None)
    argv = [str(part) for part in getattr(original, "argv", []) or []]
    if not any("jmhSmokeCheck" in part for part in argv):
        return ""
    combined = "\n".join(
        part
        for part in [
            str(getattr(original, "stdout", "") or ""),
            str(getattr(original, "stderr", "") or ""),
        ]
        if part
    )
    threshold_lines = _benchmark_threshold_lines(combined)
    if not threshold_lines and worktree is not None:
        threshold_lines = _benchmark_threshold_lines(
            _read_text_if_exists(worktree / "benchmarks/build/jmh.txt")
        )
    if not threshold_lines:
        return ""
    return "benchmark_smoke_diagnostic: " + " | ".join(threshold_lines[:8])


def _benchmark_threshold_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        lowered = line.lower()
        if not line:
            continue
        if "above threshold" in lowered or "missing smoke benchmark result" in lowered:
            lines.append(line)
            continue
        if (
            "storerangescanbenchmark" in lowered
            and "gc.alloc.rate.norm" in lowered
        ):
            lines.append(line)
    return lines


def _read_text_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _tail_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "[truncated]\n" + text[-limit:]


def _jmh_result_benchmarks(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    benchmarks: list[str] = []
    for item in parsed:
        if isinstance(item, dict) and item.get("benchmark"):
            benchmarks.append(str(item["benchmark"]))
    return sorted(set(benchmarks))


def _commands_run_from_verification_results(results: list[Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in results:
        original = getattr(item, "original", None)
        if original is None:
            continue
        commands.append(
            {
                "cmd": list(getattr(original, "argv", []) or []),
                "exit_code": int(getattr(original, "exit_code", 0) or 0),
                "duration_s": float(getattr(original, "duration_seconds", 0.0) or 0.0),
            }
        )
    return commands


def _compact_previous_response(raw_response: str, *, limit: int = 4000) -> dict[str, Any]:
    parsed: object | None = None
    try:
        parsed = normalize_task_result_payload(extract_json(raw_response))
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return {
            "changed_files": _string_list(parsed.get("changed_files"))[:80],
            "checks": _compact_checks(parsed.get("checks")),
            "commands_run": _compact_checks(parsed.get("commands_run")),
            "blockers": _string_list(parsed.get("blockers"))[:20],
            "deviations": _string_list(parsed.get("deviations"))[:20],
        }
    text = raw_response.strip()
    if len(text) > limit:
        text = text[:limit] + "\n...[truncated]"
    return {"raw_excerpt": text}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _compact_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated for implementer capsule]"


def _implementer_source_queries(
    *,
    task_contract: TaskContract,
    qa_tests: list[str],
    relevant_paths: list[str],
) -> list[str]:
    terms = [
        task_contract.objective,
        " ".join(task_contract.expected_outputs),
        " ".join(_string_list(task_contract.inputs.get("acceptance_assertion_ids"))),
        " ".join(qa_tests),
        " ".join(relevant_paths),
    ]
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", " ".join(terms))
        if word.lower()
        not in {
            "engineering",
            "implement",
            "implementation",
            "feature",
            "contract",
            "expected",
            "outputs",
            "tests",
            "test",
        }
    ]
    unique = list(dict.fromkeys(words))
    queries = [" ".join(unique[:8])] if unique else []
    for path in [*task_contract.allowed_paths, *relevant_paths, *qa_tests]:
        if isinstance(path, str) and path:
            queries.append(path)
    return list(dict.fromkeys(query for query in queries if query))[:12]


def _source_brief_terms(query: str) -> list[str]:
    stopwords = {
        "engineering",
        "implement",
        "implementation",
        "feature",
        "contract",
        "expected",
        "outputs",
        "tests",
        "test",
        "source",
        "public",
        "using",
        "with",
        "without",
    }
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", query)
        if word.lower() not in stopwords
    ]
    return list(dict.fromkeys(words))[:32]


def _ranked_source_brief_paths(
    *,
    worktree: Path,
    roots: list[str],
    terms: list[str],
    forbidden_paths: list[str],
    limit: int,
) -> list[str]:
    candidates: dict[str, int] = {}
    for raw_root in roots:
        root = _source_brief_path(raw_root)
        if not root or _source_brief_forbidden(root, forbidden_paths):
            continue
        full = worktree / root
        paths: list[Path]
        if full.is_file():
            paths = [full]
        elif full.is_dir():
            paths = [
                path
                for path in sorted(full.rglob("*"))
                if path.is_file() and _source_brief_extension(path)
            ][:80]
        else:
            paths = []
        for path in paths:
            rel = path.relative_to(worktree).as_posix()
            if _source_brief_forbidden(rel, forbidden_paths):
                continue
            candidates[rel] = max(candidates.get(rel, 0), _source_brief_score(rel, terms))
    return [
        path
        for path, _score in sorted(
            candidates.items(),
            key=lambda item: (-item[1], len(item[0]), item[0]),
        )[:limit]
    ]


def _source_brief_entries(
    worktree: Path,
    paths: list[str],
    *,
    max_file_chars: int,
    remaining_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    remaining = remaining_chars
    for rel in paths:
        if remaining <= 0:
            break
        full = worktree / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        budget = min(max_file_chars, remaining)
        excerpt = text[:budget]
        truncated = len(text) > len(excerpt)
        entries.append(
            {
                "path": rel,
                "chars": len(text),
                "truncated": truncated,
                "excerpt": excerpt + ("\n...[truncated]" if truncated else ""),
            }
        )
        remaining -= len(excerpt)
    return entries, remaining


def _source_brief_path(value: str) -> str:
    path = value.split("#", 1)[0].split("::", 1)[0].strip()
    return path.strip("/")


def _source_brief_extension(path: Path) -> bool:
    if "/build/" in f"/{path.as_posix()}/":
        return False
    return path.suffix in {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".gradle"}


def _source_brief_forbidden(path: str, forbidden_paths: list[str]) -> bool:
    return any(path_matches(path, forbidden) for forbidden in forbidden_paths)


def _source_brief_score(path: str, terms: list[str]) -> int:
    lowered = path.lower()
    score = sum(5 for term in terms if term and term in lowered)
    if any(part in lowered for part in ("api", "store", "range", "visitor", "benchmark")):
        score += 3
    if lowered.endswith("build.gradle"):
        score += 2
    return score


def _compact_checks(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    checks: list[dict[str, Any]] = []
    for item in value[:40]:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                key: item.get(key)
                for key in ("cmd", "command", "exit_code", "status", "duration_s")
                if key in item
            }
        )
    return checks


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


def _dirty_forbidden_path_violations(
    paths: list[str],
    *,
    touched: list[str],
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
) -> list[dict[str, str]]:
    touched_set = set(touched)
    qa_paths = _qa_authored_paths(qa_contract)
    violations: list[dict[str, str]] = []
    for path in paths:
        if path in touched_set or _path_in_roots(path, qa_paths):
            continue
        if any(path_matches(path, root) for root in task_contract.forbidden_paths):
            violations.append(
                {
                    "path": path,
                    "reason": "preexisting_forbidden_dirty_path",
                }
            )
    return violations


def _dirty_scope_path_violations(
    paths: list[str],
    *,
    touched: list[str],
    task_contract: TaskContract,
    qa_contract: QAAuthorContract,
) -> list[dict[str, str]]:
    del paths, touched, task_contract, qa_contract
    return []


def _task_variant_scope(task_contract: TaskContract) -> set[str]:
    text = " ".join(
        [
            task_contract.objective,
            " ".join(task_contract.expected_outputs),
            " ".join(_string_list(task_contract.inputs.get("acceptance_assertion_ids"))),
            " ".join(_string_list(task_contract.inputs.get("grading_criteria"))),
        ]
    ).lower()
    scope: set[str] = set()
    pairs = {
        "single": "double",
        "double": "single",
        "direct": "mmap",
        "mmap": "direct",
    }
    for include, exclude in pairs.items():
        if include in text and exclude not in text:
            scope.add(include)
    return scope


def _scope_conflict(path: str, scope: set[str]) -> str | None:
    lowered = path.lower()
    conflicts = {
        "single": "double",
        "double": "single",
        "direct": "mmap",
        "mmap": "direct",
    }
    for scoped, conflicting in conflicts.items():
        if scoped in scope and conflicting in lowered:
            return f"{scoped}_only"
    return None


def _qa_authored_paths(qa_contract: QAAuthorContract) -> list[str]:
    paths: list[str] = []
    for item in [*qa_contract.paths_touched, *qa_contract.tests_added]:
        path = str(item).split("#", 1)[0]
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))


def _path_in_roots(path: str, roots: list[str]) -> bool:
    return any(path_matches(path, root) for root in roots)


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
    for row in reversed(
        list_task_contracts(task_contract.feature_id, database_url=database_url)
    ):
        contract = _qa_contract_from_payload(row.get("output_contract"))
        if contract is not None:
            return contract
    return None


def _qa_contract_from_payload(payload: Any) -> QAAuthorContract | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("qa_author_contract")
    if not isinstance(raw, dict):
        if "task_result_contract" in payload or "changed_files" in payload:
            return None
        if not any(
            key in payload
            for key in [
                "tests_added",
                "red_proof",
                "matrix_coverage",
                "paths_touched",
            ]
        ):
            return None
        raw = payload
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


def _changed_file_contents(
    worktree: Path,
    project_metadata: dict[str, Any],
) -> dict[str, bytes | None]:
    return {
        path: (worktree / path).read_bytes() if (worktree / path).is_file() else None
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


def _path_violation_diffs(
    worktree: Path,
    baseline_contents: dict[str, bytes | None],
    paths: list[str],
) -> dict[str, dict[str, Any]]:
    diffs: dict[str, dict[str, Any]] = {}
    for path in sorted(dict.fromkeys(paths)):
        before = baseline_contents.get(path)
        target = worktree / path
        after = target.read_bytes() if target.is_file() else None
        diffs[path] = {
            "before_sha256": _bytes_hash(before),
            "after_sha256": _bytes_hash(after),
            "diff_excerpt": _text_diff_excerpt(before, after, path=path),
        }
    return diffs


def _restore_paths(
    worktree: Path,
    baseline_contents: dict[str, bytes | None],
    paths: Any,
) -> None:
    for path in paths:
        if not isinstance(path, str) or not path:
            continue
        target = worktree / path
        before = baseline_contents.get(path)
        if before is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)


def _bytes_hash(value: bytes | None) -> str | None:
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _text_diff_excerpt(before: bytes | None, after: bytes | None, *, path: str) -> str:
    before_text = (before or b"").decode("utf-8", errors="replace").splitlines()
    after_text = (after or b"").decode("utf-8", errors="replace").splitlines()
    diff = "\n".join(
        difflib.unified_diff(
            before_text,
            after_text,
            fromfile=f"before/{path}",
            tofile=f"after/{path}",
            lineterm="",
        )
    )
    if len(diff) > 4000:
        return diff[:4000] + "\n...[truncated]"
    return diff


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
