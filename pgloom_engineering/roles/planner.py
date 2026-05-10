from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom.context import count_tokens
from pgloom.db.postgres import connect
from pgloom.harness.result import HandlerResult
from pgloom.memory import MemoryEntry
from pgloom.memory_postgres import PostgresMemoryStore
from pgloom.tasks import enqueue_task

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import (
    create_plan_contract,
    record_handoff,
    record_recovery_action,
    upsert_task_contract,
)
from pgloom_engineering.contracts import (
    FeatureGoalContract,
    PlanContract,
    RecoveryDecisionContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.features import attach_task
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.path_policy import discover_qa_write_paths
from pgloom_engineering.planner import CouncilConfig, PlannerCouncil, ProjectContext
from pgloom_engineering.planner.context_capsule import (
    capsule_from_token_savior,
    capsule_query_hash,
    current_git_head,
    get_context_capsule,
    token_savior_from_capsule,
    upsert_context_capsule,
)
from pgloom_engineering.planner.exceptions import PlannerCouncilExhausted
from pgloom_engineering.planner.substance import planner_qa_policy_summary
from pgloom_engineering.planner.token_savior_context import (
    TokenSaviorContextResult,
    build_token_savior_project_context,
)
from pgloom_engineering.projects import ProjectConfig, get_project, role_enabled
from pgloom_engineering.token_savior import TokenSaviorUsage, record_token_savior_usage


class PlannerHandler:
    def __init__(self, *, council: PlannerCouncil | None = None) -> None:
        self._council = council

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        if payload.get("feature_goal_contract") and not payload.get("plan_contract"):
            return self._handle_council(task, payload)
        raw_contract = payload.get("plan_contract")
        if not raw_contract:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_missing",
                blocker_reason="planner task requires a multi-agent council PlanContract",
                result={
                    "role": "planner",
                    "task_id": task.get("id"),
                    "requires_multi_agent_council": True,
                },
            )
        try:
            contract = PlanContract.model_validate(raw_contract)
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_invalid",
                blocker_reason=str(exc),
            )
        return self._persist_and_decompose(task, payload, contract)

    def _handle_council(self, task: dict[str, Any], payload: dict[str, Any]) -> HandlerResult:
        database_url = payload.get("database_url")
        try:
            feature_goal = FeatureGoalContract.model_validate(payload["feature_goal_contract"])
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.feature_goal_contract_invalid",
                blocker_reason=str(exc),
            )
        project_context, token_savior = _build_project_context(
            payload,
            feature_goal,
            database_url,
            workflow_id=str(task.get("workflow_id") or ""),
        )
        council = self._council or _build_council(
            database_url=database_url,
            payload=payload,
            feature_goal=feature_goal,
        )
        try:
            outcome = council.run(
                feature_goal=feature_goal,
                project_context=project_context,
                workflow_id=task.get("workflow_id"),
                task_id=task.get("id"),
            )
        except PlannerCouncilExhausted as exc:
            _record_token_savior_for_planner_calls(
                feature_id=str(task.get("workflow_id")),
                workflow_id=task.get("workflow_id"),
                task_id=task.get("id"),
                token_savior=token_savior,
                database_url=database_url,
            )
            iterations = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in exc.iterations
            ]
            invalid_proposals = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in getattr(exc, "invalid_proposals", [])
            ]
            record_recovery_action(
                RecoveryDecisionContract(
                    feature_id=str(task.get("workflow_id")),
                    task_id=task.get("id"),
                    blocker_code="engineering.planner_council_exhausted",
                    action="replan",
                    rationale="Planner council exhausted before producing an accepted plan.",
                    attempt=len(iterations) or 1,
                    max_attempts=get_settings().planner_max_iterations,
                ),
                status="open",
                outcome=json.dumps(
                    {"iterations": iterations, "invalid_proposals": invalid_proposals},
                    default=str,
                ),
                database_url=database_url,
            )
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.planner_council_exhausted",
                blocker_reason="planner council exhausted",
                result={"iterations": iterations, "invalid_proposals": invalid_proposals},
            )
        _record_token_savior_for_planner_calls(
            feature_id=str(task.get("workflow_id")),
            workflow_id=task.get("workflow_id"),
            task_id=task.get("id"),
            token_savior=token_savior,
            database_url=database_url,
        )
        _write_accepted_plan_memory(
            contract=outcome.final,
            workflow_id=str(task.get("workflow_id") or outcome.final.feature_id),
            database_url=database_url,
        )
        return self._persist_and_decompose(task, payload, outcome.final)

    def _persist_and_decompose(
        self,
        task: dict[str, Any],
        payload: dict[str, Any],
        contract: PlanContract,
    ) -> HandlerResult:
        database_url = payload.get("database_url")
        contract = _canonicalize_plan_feature_id(contract, task)
        contract = _apply_replan_supersession(contract, payload)
        contract = _apply_corrective_slice_scope(contract, payload)
        project = _project_from_payload(payload, contract.project, database_url)
        qa_write_paths = (
            _project_metadata_qa_write_paths(project.metadata, project.root)
            if project is not None
            else None
        )
        project_metadata = project.metadata if project is not None else {}
        plan_row = create_plan_contract(
            contract,
            planner_task_id=task.get("id"),
            database_url=database_url,
            qa_write_paths=qa_write_paths,
        )
        if plan_row["status"] != "valid":
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.plan_contract_invalid",
                blocker_reason="plan contract failed validation",
                result={
                    "plan_contract_id": plan_row["id"],
                    "errors": plan_row["validation_errors"],
                },
            )

        created: dict[str, str] = {}
        deferred: list[dict[str, str]] = []
        for task_slice in contract.task_slices:
            if project is not None and not role_enabled(project, task_slice.role):
                deferred.append(
                    {
                        "slice_id": task_slice.slice_id,
                        "role": task_slice.role,
                        "reason": (
                            "role gated to disabled in "
                            "engineering_projects.metadata.role_gates"
                        ),
                    }
                )
                record_recovery_action(
                    RecoveryDecisionContract(
                        feature_id=contract.feature_id,
                        task_id=task.get("id"),
                        blocker_code="engineering.role_gate_disabled",
                        action="block_execution",
                        rationale=(
                            "role gated to disabled in "
                            "engineering_projects.metadata.role_gates"
                        ),
                        attempt=1,
                        max_attempts=1,
                    ),
                    status="deferred",
                    outcome=json.dumps(
                        {
                            "slice_id": task_slice.slice_id,
                            "role": task_slice.role,
                            "project": contract.project,
                        },
                        sort_keys=True,
                    ),
                    database_url=database_url,
                )
                continue
            depends_on = [created[dep] for dep in task_slice.depends_on if dep in created]
            child = enqueue_task(
                workflow_id=contract.feature_id,
                domain="engineering",
                task_type=task_slice.task_type,
                slot=_slot_for_slice(task_slice.role, task_slice.task_type),
                payload={
                    "feature_id": contract.feature_id,
                    "plan_contract_id": plan_row["id"],
                    "plan_contract_hash": plan_row["contract_hash"],
                    "task_slice_id": task_slice.slice_id,
                    "milestone_id": task_slice.milestone_id,
                    "project": payload.get("project") or contract.project,
                    "allow_unregistered_project": payload.get("allow_unregistered_project", False),
                    "requires_multi_agent_review": True,
                },
                depends_on=depends_on,
                database_url=database_url,
            )
            created[task_slice.slice_id] = child["id"]
            attach_task(
                contract.feature_id,
                child["id"],
                role=task_slice.role,
                database_url=database_url,
            )
            task_contract = TaskContract(
                feature_id=contract.feature_id,
                plan_contract_id=plan_row["id"],
                role=task_slice.role,
                task_type=task_slice.task_type,
                objective=task_slice.objective,
                inputs={
                    "plan_contract_id": plan_row["id"],
                    "task_id": child["id"],
                    "task_slice_id": task_slice.slice_id,
                    "milestone_id": task_slice.milestone_id,
                    "acceptance_assertion_ids": task_slice.acceptance_assertion_ids,
                    "grading_criteria": task_slice.grading_criteria,
                    "validation_strategy": task_slice.validation_strategy,
                    "context_budget": task_slice.context_budget,
                    "model_route_hint": task_slice.model_route_hint,
                },
                allowed_paths=task_slice.allowed_paths,
                forbidden_paths=task_slice.forbidden_paths,
                dependencies=depends_on,
                expected_outputs=task_slice.expected_outputs,
                verification_commands=_feature_scoped_verification_commands(
                    task_slice.verification_commands,
                    plan=contract,
                    task_objective=task_slice.objective,
                    project_metadata=project_metadata,
                ),
                required_procedures=task_slice.required_procedures,
                handoff_requirements=["produce TaskResultContract"],
            )
            upsert_task_contract(child["id"], task_contract, database_url=database_url)
            record_handoff(
                feature_id=contract.feature_id,
                from_task_id=task.get("id"),
                to_task_id=child["id"],
                handoff_type="plan_to_task",
                contract=task_contract.model_dump(mode="json"),
                database_url=database_url,
            )

        return HandlerResult.done(
            {
                "role": "planner",
                "task_id": task.get("id"),
                "plan_contract_id": plan_row["id"],
                "child_task_ids": list(created.values()),
                "deferred_slices": deferred,
                "planning": "multi_agent",
            }
        )


def _slot_for_slice(role: str, task_type: str) -> str:
    if task_type == "engineering.qa.verify.scrutiny":
        return "qa-scrutiny"
    if task_type == "engineering.qa.verify.usertest":
        return "qa-usertest"
    if role == "qa":
        return "qa-engineer"
    return role


def _feature_scoped_verification_commands(
    commands: list[list[str]],
    *,
    plan: PlanContract,
    task_objective: str,
    project_metadata: dict[str, Any],
) -> list[list[str]]:
    qa = project_metadata.get("qa") if isinstance(project_metadata, dict) else None
    rules = qa.get("feature_smoke_commands") if isinstance(qa, dict) else None
    if not isinstance(rules, list) or not rules:
        return commands

    feature_text = " ".join(
        [
            plan.problem_statement,
            task_objective,
            " ".join(plan.acceptance_assertions),
            " ".join(plan.acceptance_test_matrix),
            " ".join(plan.design_contract.acceptance_tests),
        ]
    ).lower()
    scoped: list[list[str]] = []
    for command in commands:
        replacement = _feature_smoke_replacement(command, rules, feature_text)
        scoped.extend(replacement or [command])
    return _dedupe_commands(scoped)


def _feature_smoke_replacement(
    command: list[str],
    rules: list[Any],
    feature_text: str,
) -> list[list[str]]:
    command_text = " ".join(command)
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        match_terms = [
            str(term).lower()
            for term in rule.get("match_terms", [])
            if isinstance(term, str)
        ]
        if match_terms and not any(term in feature_text for term in match_terms):
            continue
        replaces = [str(item) for item in rule.get("replaces", []) if isinstance(item, str)]
        if replaces and not any(item in command_text for item in replaces):
            continue
        raw_commands = rule.get("commands")
        if not isinstance(raw_commands, list):
            continue
        parsed = [
            [str(part) for part in item]
            for item in raw_commands
            if isinstance(item, list) and item
        ]
        if parsed:
            return parsed
    return []


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped


def _canonicalize_plan_feature_id(
    contract: PlanContract,
    task: dict[str, Any],
) -> PlanContract:
    workflow_id = str(task.get("workflow_id") or "")
    if not workflow_id or contract.feature_id == workflow_id:
        return contract
    return contract.model_copy(update={"feature_id": workflow_id})


def _apply_replan_supersession(
    contract: PlanContract,
    payload: dict[str, Any],
) -> PlanContract:
    if contract.supersedes_plan_id and contract.supersession_rationale:
        return contract
    context = payload.get("replan_context")
    if not isinstance(context, dict) or context.get("mode") != "corrective_slice":
        return contract
    active_plan_id = context.get("active_plan_contract_id")
    if not active_plan_id:
        return contract
    blocker_code = str(context.get("blocker_code") or "engineering.blocked")
    blocker_reason = str(context.get("blocker_reason") or "blocked corrective slice")
    return contract.model_copy(
        update={
            "supersedes_plan_id": str(active_plan_id),
            "supersession_rationale": (
                "Corrective-slice recovery supersedes the prior active plan after "
                f"{blocker_code}: {blocker_reason}"
            ),
        }
    )


def _apply_corrective_slice_scope(
    contract: PlanContract,
    payload: dict[str, Any],
) -> PlanContract:
    context = payload.get("replan_context")
    if not isinstance(context, dict) or context.get("mode") != "corrective_slice":
        return contract
    blocker_code = str(context.get("blocker_code") or "")
    if blocker_code not in {
        "engineering.implementation_verification_failed",
        "engineering.implementation_path_violation",
        "engineering.implementer_contract_invalid",
        "engineering.invalid_handler_output",
        "engineering.qa_handoff_missing",
        "engineering.qa_semantic_quality_failed",
        "engineering.qa_usertest_contract_invalid",
        "engineering.review_rejected",
        "engineering.qa_verify_failed",
        "engineering.qa_usertest_failed",
    }:
        return contract
    allowed_task_types = _corrective_allowed_task_types(context)
    kept = [
        task_slice
        for task_slice in contract.task_slices
        if task_slice.task_type in allowed_task_types
    ]
    if not kept:
        return contract

    kept = _narrow_corrective_slice_chain(kept, context)
    kept_ids = {task_slice.slice_id for task_slice in kept}
    dependency_by_type = {
        task_slice.task_type: task_slice.slice_id for task_slice in kept
    }
    scoped_slices = [
        task_slice.model_copy(
            update={
                "depends_on": _corrective_dependencies(
                    task_slice,
                    dependency_by_type=dependency_by_type,
                    kept_ids=kept_ids,
                )
            }
        )
        for task_slice in kept
    ]
    scoped_milestones = [
        milestone.model_copy(
            update={
                "slice_ids": [
                    slice_id for slice_id in milestone.slice_ids if slice_id in kept_ids
                ]
            }
        )
        for milestone in contract.milestones
    ]
    return contract.model_copy(
        update={
            "task_slices": scoped_slices,
            "milestones": scoped_milestones,
        }
    )


def _narrow_corrective_slice_chain(
    task_slices: list[TaskSliceContract],
    context: dict[str, Any],
) -> list[TaskSliceContract]:
    primary_types = ["engineering.implement"]
    blocker_code = str(context.get("blocker_code") or "")
    if blocker_code in {
        "engineering.qa_semantic_quality_failed",
        "engineering.qa_handoff_missing",
    } or _corrective_context_mentions_qa_owned_paths(context) or any(
        task_slice.task_type == "engineering.qa.author" for task_slice in task_slices
    ):
        primary_types.insert(0, "engineering.qa.author")
    selected_ids: set[str] = set()
    narrowed: list[TaskSliceContract] = []
    for task_type in primary_types:
        candidates = [
            task_slice for task_slice in task_slices if task_slice.task_type == task_type
        ]
        if not candidates:
            continue
        selected = _best_corrective_slice(candidates, context)
        narrowed.append(selected)
        selected_ids.add(selected.slice_id)
    for task_slice in task_slices:
        if task_slice.slice_id in selected_ids:
            continue
        if task_slice.task_type in {
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }:
            narrowed.append(task_slice)
            selected_ids.add(task_slice.slice_id)
    return narrowed


def _best_corrective_slice(
    candidates: list[TaskSliceContract],
    context: dict[str, Any],
) -> TaskSliceContract:
    context_terms = _corrective_context_terms(context)
    if not context_terms:
        return candidates[0]
    return max(
        candidates,
        key=lambda task_slice: (
            len(context_terms & _corrective_slice_terms(task_slice)),
            -candidates.index(task_slice),
        ),
    )


def _corrective_context_terms(context: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(context.get(key) or "")
        for key in ("blocker_code", "blocker_reason", "failure_context", "summary")
    )
    return _corrective_terms(text)


def _corrective_slice_terms(task_slice: TaskSliceContract) -> set[str]:
    text = " ".join(
        [
            task_slice.slice_id,
            task_slice.objective,
            " ".join(task_slice.allowed_paths),
            " ".join(task_slice.expected_outputs),
            " ".join(task_slice.acceptance_assertion_ids),
        ]
    )
    return _corrective_terms(text)


def _corrective_terms(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    stop_words = {
        "and",
        "the",
        "for",
        "with",
        "that",
        "this",
        "into",
        "from",
        "must",
        "these",
        "after",
        "before",
        "slice",
        "slices",
        "repair",
        "corrective",
        "engineering",
    }
    return {
        token
        for token in normalized.split()
        if len(token) >= 4 and token not in stop_words
    }


def _corrective_dependencies(
    task_slice: TaskSliceContract,
    *,
    dependency_by_type: dict[str, str],
    kept_ids: set[str],
) -> list[str]:
    task_type = task_slice.task_type
    if task_type == "engineering.qa.author":
        return []
    if task_type == "engineering.implement":
        qa_author_id = dependency_by_type.get("engineering.qa.author")
        return [qa_author_id] if qa_author_id else []
    if task_type == "engineering.review":
        upstream = dependency_by_type.get(
            "engineering.implement"
        ) or dependency_by_type.get("engineering.qa.author")
        return [upstream] if upstream else []
    if task_type == "engineering.qa.verify.scrutiny":
        upstream = dependency_by_type.get("engineering.review") or dependency_by_type.get(
            "engineering.implement"
        ) or dependency_by_type.get("engineering.qa.author")
        return [upstream] if upstream else []
    if task_type == "engineering.qa.verify.usertest":
        upstream = dependency_by_type.get(
            "engineering.qa.verify.scrutiny"
        ) or dependency_by_type.get("engineering.review") or dependency_by_type.get(
            "engineering.implement"
        ) or dependency_by_type.get("engineering.qa.author")
        return [upstream] if upstream else []
    return [dependency for dependency in task_slice.depends_on if dependency in kept_ids]


def _corrective_allowed_task_types(context: dict[str, Any]) -> set[str]:
    if str(context.get("blocker_code") or "") == "engineering.qa_semantic_quality_failed":
        return {
            "engineering.qa.author",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }
    if str(context.get("blocker_code") or "") == "engineering.qa_handoff_missing":
        return {
            "engineering.qa.author",
            "engineering.implement",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }
    allowed = {
        "engineering.implement",
        "engineering.review",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }
    blocker_code = str(context.get("blocker_code") or "")
    qa_owned = _corrective_context_mentions_qa_owned_paths(context)
    if blocker_code == "engineering.review_rejected" and qa_owned:
        if not _corrective_context_mentions_production_defect(context):
            return {
                "engineering.qa.author",
                "engineering.review",
                "engineering.qa.verify.scrutiny",
                "engineering.qa.verify.usertest",
            }
        return {
            "engineering.qa.author",
            "engineering.implement",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }
    if blocker_code not in {
        "engineering.implementation_verification_failed",
        "engineering.implementation_path_violation",
    }:
        return allowed
    if qa_owned:
        return {
            "engineering.qa.author",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }
    return allowed


def _corrective_context_mentions_qa_owned_paths(context: dict[str, Any]) -> bool:
    context_text = " ".join(
        str(context.get(key) or "")
        for key in ("blocker_reason", "failure_context", "summary")
    ).lower()
    qa_owned_signals = [
        "qa-authored",
        "qa author",
        "benchmarks/src/jmh",
        "benchmarks/build.gradle",
        "conformance-tests/src/test",
        "core/src/test",
        "store/src/test",
        "missing smoke benchmark result",
        "benchmark-smoke",
        "wrongmethodtypeexception",
        "classnotfoundexception",
        "forbidden benchmark",
        "forbidden qa",
    ]
    return any(signal in context_text for signal in qa_owned_signals)


def _corrective_context_mentions_production_defect(context: dict[str, Any]) -> bool:
    context_text = " ".join(
        str(context.get(key) or "")
        for key in ("blocker_reason", "failure_context", "summary")
    ).lower()
    production_signals = [
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
    return any(signal in context_text for signal in production_signals)


def _build_council(
    *,
    database_url: str | None,
    payload: dict[str, Any] | None = None,
    feature_goal: FeatureGoalContract | None = None,
) -> PlannerCouncil:
    settings = get_settings()
    profile_commands = dict(settings.planner_profile_commands)
    panelist_count = settings.planner_panelist_count
    if payload is not None and feature_goal is not None:
        project = _project_from_payload(payload, feature_goal.project, database_url)
        if project is not None:
            profile_commands.update(_project_profile_commands(project))
        panelist_count = _adaptive_panelist_count(feature_goal, settings.planner_panelist_count)
    profile_commands = _role_routed_profile_commands(profile_commands, settings.planner_command)
    config = CouncilConfig(
        panelist_count=panelist_count,
        iter_1_panelist_count=settings.planner_iter_1_panelist_count,
        iter_2_panelist_count=settings.planner_iter_2_panelist_count,
        max_iterations=settings.planner_max_iterations,
        panelist_profile=settings.planner_panelist_profile,
        critic_profile=settings.planner_critic_profile,
        consolidator_profile=settings.planner_consolidator_profile,
        timeout_seconds_per_invocation=settings.planner_invocation_timeout_seconds,
        command=settings.planner_command,
        profile_commands=profile_commands,
        consolidator_scoped_inputs_enabled=settings.planner_consolidator_scoped_inputs_enabled,
        production_grade_preempts_critic=settings.planner_production_grade_preempts_critic,
        production_grade_critic_sample_rate=settings.planner_production_grade_critic_sample_rate,
    )
    return PlannerCouncil(
        config=config,
        provider=EngineeringCLIModelProvider(database_url=database_url),
    )


def _role_routed_profile_commands(
    profile_commands: dict[str, list[str]],
    default_command: list[str],
) -> dict[str, list[str]]:
    settings = get_settings()
    routed = dict(profile_commands)
    role_specs = {
        settings.planner_panelist_profile: (
            settings.planner_claude_panelist_model,
            settings.planner_codex_panelist_model,
            settings.planner_codex_panelist_reasoning,
        ),
        settings.planner_consolidator_profile: (
            settings.planner_claude_consolidator_model,
            settings.planner_codex_consolidator_model,
            settings.planner_codex_consolidator_reasoning,
        ),
        settings.planner_critic_profile: (
            settings.planner_claude_critic_model,
            settings.planner_codex_critic_model,
            settings.planner_codex_critic_reasoning,
        ),
    }
    for profile_name, (claude_model, codex_model, codex_reasoning) in role_specs.items():
        command = routed.get(profile_name, default_command)
        routed[profile_name] = _route_model_command(
            command,
            claude_model=claude_model,
            codex_model=codex_model,
            codex_reasoning=codex_reasoning,
        )
    return routed


def _route_model_command(
    command: list[str],
    *,
    claude_model: str,
    codex_model: str,
    codex_reasoning: str,
) -> list[str]:
    if not command:
        return command
    routed = list(command)
    binary = Path(routed[0]).name
    if binary == "claude":
        return _replace_flag_value(routed, "--model", claude_model)
    if binary == "codex":
        routed = _replace_flag_value(routed, "-m", codex_model)
        routed = _replace_or_append_reasoning(routed, codex_reasoning)
    return routed


def _replace_flag_value(command: list[str], flag: str, value: str) -> list[str]:
    routed = list(command)
    if flag in routed:
        index = routed.index(flag)
        if index + 1 < len(routed):
            routed[index + 1] = value
            return routed
    return [*routed, flag, value]


def _replace_or_append_reasoning(command: list[str], reasoning: str) -> list[str]:
    routed = list(command)
    setting = f'model_reasoning_effort="{reasoning}"'
    for index, item in enumerate(routed):
        if item.startswith("model_reasoning_effort="):
            routed[index] = setting
            return routed
        if item == "-c" and index + 1 < len(routed) and routed[index + 1].startswith(
            "model_reasoning_effort="
        ):
            routed[index + 1] = setting
            return routed
    return [*routed, "-c", setting]


def _build_project_context(
    payload: dict[str, Any],
    feature_goal: FeatureGoalContract,
    database_url: str | None,
    workflow_id: str = "",
) -> tuple[ProjectContext, TokenSaviorContextResult | None]:
    project = _project_from_payload(payload, feature_goal.project, database_url)
    root = project.root if project is not None else Path(".")
    metadata = project.metadata if project is not None else {}
    settings = get_settings()
    memory_digest = _build_memory_digest(
        project_name=feature_goal.project,
        project_root=root,
        query=_token_savior_query(feature_goal),
        workflow_id=workflow_id,
        database_url=database_url,
        budget_tokens=settings.planner_memory_budget_tokens,
    )
    if settings.planner_token_savior_enabled:
        query = _token_savior_query(feature_goal)
        git_head = current_git_head(root)
        query_hash = capsule_query_hash(
            query,
            budget_tokens=settings.planner_token_savior_budget_tokens,
            memory_digest=memory_digest,
        )
        if settings.planner_context_capsule_cache_enabled and project is not None:
            cached = get_context_capsule(
                project=project.name,
                git_head=git_head,
                query_hash=query_hash,
                capsule_version=settings.planner_context_capsule_version,
                database_url=database_url,
            )
            if cached is not None:
                token_savior = token_savior_from_capsule(cached)
                return _merge_project_context_metadata(
                    token_savior.context,
                    metadata,
                ), token_savior
        token_savior = build_token_savior_project_context(
            project_root=root,
            query=query,
            budget_tokens=settings.planner_token_savior_budget_tokens,
            memory_digest=memory_digest,
        )
        context = _merge_project_context_metadata(token_savior.context, metadata)
        if settings.planner_context_capsule_cache_enabled and project is not None:
            capsule = capsule_from_token_savior(
                project=project.name,
                git_head=git_head,
                query_hash=query_hash,
                capsule_version=settings.planner_context_capsule_version,
                result=token_savior.model_copy(update={"context": context}),
                metadata={"cache": "miss", "memory_digest_tokens": _approx_tokens(memory_digest)},
            )
            upsert_context_capsule(capsule, database_url=database_url)
        return context, token_savior
    return ProjectContext(
        project_root=root,
        roadmap_excerpt="\n\n".join(
            part
            for part in [str(metadata.get("roadmap_excerpt") or ""), memory_digest]
            if part
        ),
        decisions_excerpt=str(metadata.get("decisions_excerpt") or ""),
        qa_smoke_path=root.joinpath("qa/smoke.sh"),
        qa_regression_path=root.joinpath("qa/regression.sh"),
        relevant_paths=list(metadata.get("relevant_paths") or []),
        qa_write_paths=_project_metadata_qa_write_paths(metadata, root),
        qa_policy_summary=planner_qa_policy_summary(metadata),
    ), None


def _token_savior_query(feature_goal: FeatureGoalContract) -> str:
    parts = [
        feature_goal.goal,
        *feature_goal.requirements,
        *feature_goal.acceptance_criteria,
    ]
    return " ".join(part for part in parts if part).strip() or feature_goal.project


def _project_profile_commands(project: ProjectConfig) -> dict[str, list[str]]:
    raw = project.metadata.get("planning")
    if not isinstance(raw, dict):
        raw = project.metadata.get("planner")
    if not isinstance(raw, dict):
        return {}
    commands = raw.get("profile_commands")
    if not isinstance(commands, dict):
        return {}
    result: dict[str, list[str]] = {}
    for profile_name, command in commands.items():
        if isinstance(profile_name, str) and _is_command_list(command):
            result[profile_name] = list(command)
    return result


def _merge_project_context_metadata(
    context: ProjectContext,
    metadata: dict[str, Any],
) -> ProjectContext:
    return context.model_copy(
        update={
            "relevant_paths": list(
                dict.fromkeys(
                    [
                        *context.relevant_paths,
                        *list(metadata.get("relevant_paths") or []),
                    ]
                )
            ),
            "qa_write_paths": list(
                dict.fromkeys(
                    [
                        *context.qa_write_paths,
                        *_project_metadata_qa_write_paths(metadata, context.project_root),
                    ]
                )
            ),
            "qa_policy_summary": {
                **context.qa_policy_summary,
                **planner_qa_policy_summary(metadata),
            },
        }
    )


def _project_metadata_qa_write_paths(
    metadata: dict[str, Any],
    project_root: Path,
) -> list[str]:
    paths = [str(item) for item in metadata.get("qa_write_paths") or [] if isinstance(item, str)]
    qa = metadata.get("qa")
    if isinstance(qa, dict):
        for key in [
            "test_roots",
            "browser_test_roots",
            "benchmark_roots",
            "test_support_paths",
        ]:
            raw = qa.get(key)
            if isinstance(raw, list):
                paths.extend(str(item) for item in raw if isinstance(item, str))
    if not paths:
        paths = discover_qa_write_paths(project_root)
    return list(dict.fromkeys(path.rstrip("/") + "/" for path in paths if path.strip()))


def _adaptive_panelist_count(
    feature_goal: FeatureGoalContract,
    default_count: int,
) -> int:
    settings = get_settings()
    text = _token_savior_query(feature_goal).lower()
    high_risk_terms = [
        "snapshot",
        "restore",
        "migration",
        "schema",
        "security",
        "concurrency",
        "replication",
        "distributed",
        "persistence",
        "payment",
    ]
    small_terms = [
        "diagnostic",
        "config",
        "docs",
        "readme",
        "single-surface",
        "range",
        "export",
        "visualizer",
    ]
    if any(term in text for term in high_risk_terms):
        return max(2, settings.planner_high_risk_panelist_count)
    if any(term in text for term in small_terms):
        return max(2, settings.planner_small_feature_panelist_count)
    return max(2, default_count)


def _build_memory_digest(
    *,
    project_name: str,
    project_root: Path,
    query: str,
    workflow_id: str,
    database_url: str | None,
    budget_tokens: int,
) -> str:
    sections = [
        _token_savior_memory_digest(project_root, query),
        _pgloom_memory_digest(project_name, query, workflow_id, database_url),
    ]
    digest = "\n\n".join(section for section in sections if section.strip())
    if _approx_tokens(digest) <= budget_tokens:
        return digest
    return digest[: budget_tokens * 4] + "\n...[memory digest truncated]"


def _token_savior_memory_digest(project_root: Path, query: str) -> str:
    token_savior_src = Path("/Volumes/devssd/repos/oss/token-savior/src")
    if token_savior_src.exists():
        import sys

        sys.path.insert(0, str(token_savior_src))
    try:
        memory_db = __import__("token_savior.memory_db", fromlist=["memory_db"])
        observations = memory_db.get_recent_index(
            str(project_root),
            limit=8,
            type_filter=["guardrail", "ruled_out", "convention", "warning", "decision"],
        )
        summaries = memory_db.session_summary_search(
            str(project_root),
            _memory_search_query(query),
            limit=4,
        )
    except Exception:
        return ""
    lines: list[str] = []
    if observations:
        lines.append("# Token Savior memory observations")
        for obs in observations:
            title = obs.get("title") or ""
            obs_type = obs.get("type") or "note"
            symbol = obs.get("symbol") or ""
            lines.append(f"- [{obs_type}] {title} {f'({symbol})' if symbol else ''}".strip())
    if summaries:
        lines.append("# Token Savior session summaries")
        for summary in summaries:
            completed = summary.get("completed") or summary.get("excerpt") or ""
            if completed:
                lines.append(f"- {str(completed)[:240]}")
    return "\n".join(lines)


def _pgloom_memory_digest(
    project_name: str,
    query: str,
    workflow_id: str,
    database_url: str | None,
) -> str:
    try:
        store = PostgresMemoryStore(database_url=database_url)
        project_scope = f"project:{project_name}"
        rows = [
            *store.search(workflow_id or None, query, limit=5),
            *store.search(project_scope, query, limit=8),
            *store.search(None, f"{project_name} {query}", limit=5),
        ]
    except Exception:
        return ""
    seen: set[tuple[str, str]] = set()
    lines = ["# pgloom memory"]
    for row in rows:
        key = (row.workflow_id, row.key)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {row.key}: {row.value[:240]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _write_accepted_plan_memory(
    *,
    contract: PlanContract,
    workflow_id: str,
    database_url: str | None,
) -> None:
    try:
        store = PostgresMemoryStore(database_url=database_url)
        project_scope = f"project:{contract.project}"
        metadata = {
            "project": contract.project,
            "feature_id": contract.feature_id,
            "source": "pgloom-engineering.planner",
        }
        store.put(
            MemoryEntry(
                workflow_id=workflow_id,
                key=f"feature:{contract.feature_id}:accepted_plan_summary",
                value=_accepted_plan_summary(contract),
                metadata=metadata,
            )
        )
        store.put(
            MemoryEntry(
                workflow_id=project_scope,
                key=f"project:{contract.project}:qa_commands",
                value=_qa_command_memory(contract),
                metadata=metadata,
            )
        )
        store.put(
            MemoryEntry(
                workflow_id=project_scope,
                key=f"project:{contract.project}:risk_patterns",
                value=_risk_memory(contract),
                metadata=metadata,
            )
        )
        store.put(
            MemoryEntry(
                workflow_id=project_scope,
                key=f"project:{contract.project}:planning_guardrails",
                value=_guardrail_memory(contract),
                metadata=metadata,
            )
        )
    except Exception:
        return


def _accepted_plan_summary(contract: PlanContract) -> str:
    slices = [
        f"{item.slice_id}:{item.role}:{item.task_type}:{','.join(item.allowed_paths)}"
        for item in contract.task_slices
    ]
    return "\n".join(
        [
            f"feature_id: {contract.feature_id}",
            f"problem: {contract.problem_statement}",
            f"affected_surfaces: {', '.join(contract.affected_surfaces)}",
            "task_slices:",
            *[f"- {item}" for item in slices],
            "acceptance:",
            *[f"- {item}" for item in contract.acceptance_test_matrix[:8]],
        ]
    )


def _qa_command_memory(contract: PlanContract) -> str:
    commands: list[str] = []
    for task_slice in contract.task_slices:
        if task_slice.role != "qa":
            continue
        for command in task_slice.verification_commands:
            rendered = " ".join(command)
            if rendered and rendered not in commands:
                commands.append(rendered)
    return "\n".join(f"- {command}" for command in commands) or "No QA commands recorded."


def _risk_memory(contract: PlanContract) -> str:
    return "\n".join(f"- {item}" for item in contract.risk_register) or "No risks recorded."


def _guardrail_memory(contract: PlanContract) -> str:
    qa_paths = sorted(
        {
            path
            for task_slice in contract.task_slices
            if task_slice.role == "qa"
            for path in task_slice.allowed_paths
        }
    )
    implementer_paths = sorted(
        {
            path
            for task_slice in contract.task_slices
            if task_slice.role == "implementer"
            for path in task_slice.allowed_paths
        }
    )
    return "\n".join(
        [
            "Use two QA phases: engineering.qa.author before implementers and "
            "engineering.qa.verify.scrutiny plus engineering.qa.verify.usertest "
            "after reviewers.",
            "QA write paths stay restricted to tests/ and qa/fixtures/.",
            "Implementer slices must not claim QA write paths.",
            f"recent_qa_paths: {', '.join(qa_paths) or 'none'}",
            f"recent_implementer_paths: {', '.join(implementer_paths) or 'none'}",
        ]
    )


def _memory_search_query(query: str) -> str:
    import re

    terms = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", query)
    return " ".join(terms[:8]) or "planning"


def _approx_tokens(text: str) -> int:
    return count_tokens(text)


def _is_command_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _record_token_savior_for_planner_calls(
    *,
    feature_id: str,
    workflow_id: str | None,
    task_id: str | None,
    token_savior: TokenSaviorContextResult | None,
    database_url: str | None,
) -> None:
    if token_savior is None or token_savior.input_tokens_original == 0:
        return
    profile_names = {
        get_settings().planner_panelist_profile,
        get_settings().planner_critic_profile,
        get_settings().planner_consolidator_profile,
    }
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select id, profile_name
            from model_usage
            where workflow_id = %s
              and task_id = %s
              and profile_name = any(%s)
            order by id
            """,
            (workflow_id, task_id, sorted(profile_names)),
        ).fetchall()
    for row in rows:
        record_token_savior_usage(
            TokenSaviorUsage(
                feature_id=feature_id,
                workflow_id=workflow_id,
                task_id=task_id,
                model_usage_id=int(row["id"]),
                profile_name=str(row["profile_name"]),
                input_tokens_original=token_savior.input_tokens_original,
                input_tokens_after_savior=token_savior.input_tokens_after_savior,
                tokens_saved=token_savior.tokens_saved,
                reduction_ratio=token_savior.reduction_ratio,
                metadata={
                    "method": token_savior.method,
                    "scope": "planner_project_context",
                    "role": _role_from_profile(str(row["profile_name"])),
                },
            ),
            database_url=database_url,
        )


def _role_from_profile(profile_name: str) -> str:
    settings = get_settings()
    if profile_name == settings.planner_panelist_profile:
        return "panelist"
    if profile_name == settings.planner_consolidator_profile:
        return "consolidator"
    if profile_name == settings.planner_critic_profile:
        return "critic"
    return profile_name


def _project_from_payload(
    payload: dict[str, Any],
    fallback_name: str,
    database_url: str | None,
) -> ProjectConfig | None:
    raw_project = payload.get("project")
    if isinstance(raw_project, dict):
        return ProjectConfig.model_validate(raw_project)
    if isinstance(raw_project, str):
        return get_project(raw_project, database_url=database_url)
    return get_project(fallback_name, database_url=database_url)
