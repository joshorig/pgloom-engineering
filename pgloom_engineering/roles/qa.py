from __future__ import annotations

import json
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
from pgloom_engineering.path_policy import is_qa_write_path
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import get_project
from pgloom_engineering.qa_runtime import (
    canonical_red_proof,
    command_with_env,
    discover_route_inventory,
    hydrate_dependencies,
    project_qa_metadata,
    prompt_safe_qa_metadata,
    qa_env,
    relevant_changed_files,
    route_inventory_for_prompt,
    run_qa_verification,
    validate_required_qa_gates,
)
from pgloom_engineering.qa_semantic_review import review_semantic_quality


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
                _route_model_command(
                    _command_for_worktree(settings.qa_author_command, handle.worktree),
                    **_qa_model_route(project.metadata, settings),
                ),
                qa_env(project.metadata, project_root=project.root),
            ),
            timeout_seconds=settings.qa_author_invocation_timeout_seconds,
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        response = provider.invoke(
            profile=profile,
            prompt=_author_prompt(
                plan,
                task_contract,
                project_metadata=project.metadata,
                project_root=project.root,
            ),
            workflow_id=task_contract.feature_id,
            task_id=task_id,
        )
        try:
            contract = QAAuthorContract.model_validate(
                _qa_author_payload(extract_json(response.text))
            )
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_author_contract_invalid",
                blocker_reason=str(exc),
                result={"raw_response": response.text},
            )
        touched = relevant_changed_files(changed_files(handle.worktree))
        violations = _path_violations(touched, task_contract)
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
        semantic_findings = _semantic_quality_findings(
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
        verification_command = _verification_command(task_contract)
        verification = run_qa_verification(
            verification_command,
            worktree=handle.worktree,
            project_metadata=project.metadata,
            timeout_seconds=settings.qa_author_invocation_timeout_seconds,
            database_url=database_url,
            workflow_id=task.get("workflow_id"),
            task_id=task_id,
            feature_id=task_contract.feature_id,
        )
        if verification.infra_error is not None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.project_unhealthy",
                blocker_reason=verification.infra_error,
                result={
                    "command": verification_command,
                    "exit_code": verification.original.exit_code,
                    "stdout_excerpt": verification.stdout_excerpt,
                    "stderr_excerpt": verification.stderr_excerpt,
                    "changed_files": touched,
                },
            )
        if verification.original.exit_code == 0:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.qa_tests_not_red",
                blocker_reason="qa.author verification command passed; expected failing tests",
                result={
                    "command": verification_command,
                    "stdout_excerpt": verification.stdout_excerpt,
                    "stderr_excerpt": verification.stderr_excerpt,
                    "changed_files": touched,
                },
            )
        contract = contract.model_copy(
            update={
                "feature_id": task_contract.feature_id,
                "task_id": task_id,
                "red_proof": canonical_red_proof(verification),
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


def _command_for_worktree(command: list[str], worktree: Path) -> list[str]:
    return [part.replace("{worktree}", str(worktree)) for part in command]


def _qa_author_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("QAAuthorContract"), dict):
        return payload["QAAuthorContract"]
    return payload


def _semantic_quality_findings(
    *,
    worktree: Path,
    changed_paths: list[str],
    plan: PlanContract,
    task_contract: TaskContract,
    project_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    files = {
        path: (worktree / path).read_text(encoding="utf-8", errors="replace")
        for path in changed_paths
        if (worktree / path).is_file()
    }
    findings = review_semantic_quality(
        files=files,
        plan_text="\n".join(
            [
                plan.problem_statement,
                *plan.acceptance_test_matrix,
                *plan.risk_register,
            ]
        ),
        task_text="\n".join(
            [
                task_contract.objective,
                *task_contract.expected_outputs,
                *task_contract.allowed_paths,
            ]
        ),
        project_metadata=project_metadata,
    )
    return [finding.asdict() for finding in findings]


def _author_prompt(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
    project_root: Path,
) -> str:
    qa_metadata = project_qa_metadata(project_metadata)
    route_requirements = _route_coverage_requirements(
        plan,
        task_contract,
        project_metadata=project_metadata,
        project_root=project_root,
    )
    payload = {
        "role": "qa.author",
        "instructions": [
            "Write failing tests for the acceptance matrix before implementation.",
            "Only edit allowed QA/test paths.",
            "Use project_qa_metadata to choose canonical test roots, examples, and helpers.",
            "Use qa_context_capsule as the compact source-of-truth for QA context.",
            (
                "If deterministic_test_skeleton has endpoint route cases, use that shape "
                "and tie every required route to controller or HTTP behavior."
            ),
            (
                "For Spring APIs, prefer MockMvc, WebTestClient, or TestRestTemplate "
                "over direct controller calls when endpoint routing semantics matter."
            ),
            "Do not satisfy route coverage with a test that only asserts a hardcoded route list.",
            "Only create qa/fixtures files when generated tests read them by path or resource.",
            (
                "For browser acceptance, prefer focused task-specific specs and satisfy "
                "project_qa_metadata.ui_acceptance when present."
            ),
            (
                "For benchmark acceptance, use the project's benchmark harness; measured "
                "benchmark methods must allocate no garbage after setup."
            ),
            "The orchestrator will run verification and create canonical red_proof.",
            "Return only a QAAuthorContract JSON object.",
            (
                "Do not include command logs, exploration notes, file diffs, or "
                "commentary in the final response."
            ),
        ],
        "project_qa_metadata": prompt_safe_qa_metadata(qa_metadata),
        "route_coverage_requirements": route_requirements,
        "deterministic_test_skeleton": _deterministic_test_skeleton(
            plan=plan,
            route_requirements=route_requirements,
            qa_metadata=qa_metadata,
        ),
        "generated_route_coverage_artifact": _generated_route_coverage_artifact(
            route_requirements
        ),
        "qa_context_capsule": _qa_context_capsule(
            route_requirements=route_requirements,
            qa_metadata=qa_metadata,
            plan=plan,
        ),
        "plan": plan.model_dump(mode="json"),
        "task_contract": task_contract.model_dump(mode="json"),
        "required_schema": {
            "feature_id": task_contract.feature_id,
            "task_id": "task id",
            "tests_added": ["path or test name"],
            "matrix_coverage": {"acceptance criterion": ["test name"]},
            "red_proof": [
                {
                    "test": "test name",
                    "command": ["test", "command"],
                    "exit_code": 1,
                    "output_excerpt": "short failure proving red",
                }
            ],
            "paths_touched": ["tests/example_test.py"],
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _qa_context_capsule(
    *,
    route_requirements: list[dict[str, Any]],
    qa_metadata: dict[str, Any],
    plan: PlanContract,
) -> dict[str, Any]:
    return {
        "contract": "qa_context_capsule.v1",
        "purpose": "Stable project QA context for this task; prefer this over rediscovery.",
        "required_domains": _domains_from_plan(plan),
        "generated_route_coverage_artifact": _generated_route_coverage_artifact(
            route_requirements
        ),
        "preferred_helpers": qa_metadata.get("preferred_helpers"),
        "behavior_coverage_rules": qa_metadata.get("behavior_coverage_rules"),
        "quality_gates": qa_metadata.get("quality_gates"),
    }


def _generated_route_coverage_artifact(
    route_requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract": "generated_route_coverage.v1",
        "producer": "pgloom-engineering.qa_runtime",
        "instructions": [
            "Use these generated route cases as source-of-truth.",
            "Do not infer or rewrite route inventory.",
            (
                "Inventory assertions may support audit only after behavior route cases "
                "invoke the matching routes."
            ),
        ],
        "requirements": route_requirements,
    }


def _deterministic_test_skeleton(
    *,
    plan: PlanContract,
    route_requirements: list[dict[str, Any]],
    qa_metadata: dict[str, Any],
) -> dict[str, Any]:
    skeleton: dict[str, Any] = {
        "purpose": (
            "Use this deterministic scaffold before adding project-specific fixtures; "
            "inventory-only tests do not satisfy behavior coverage."
        ),
        "required_domains": _domains_from_plan(plan),
        "endpoint_behavior_skeleton": [],
    }
    for key in ["preferred_test_skeletons", "preferred_helpers", "behavior_coverage_rules"]:
        value = qa_metadata.get(key)
        if value is not None:
            skeleton[key] = value
    for requirement in route_requirements:
        skeleton["endpoint_behavior_skeleton"].append(
            {
                "api_prefix": requirement.get("api_prefix"),
                "coverage_rule": requirement.get("coverage_rule"),
                "route_cases": [
                    {
                        "method": _route_method(route),
                        "path": _route_path(route),
                        "behavior_requirement": (
                            "Invoke the matching controller/HTTP route for each required "
                            "domain and assert domain-specific state."
                        ),
                    }
                    for route in requirement.get("required_routes", [])
                    if isinstance(route, str)
                ],
            }
        )
    return skeleton


def _domains_from_plan(plan: PlanContract) -> list[str]:
    text = "\n".join([plan.problem_statement, *plan.acceptance_test_matrix]).lower()
    domains = [domain for domain in ["equities", "crypto"] if domain in text]
    return domains or ["default"]


def _route_method(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[0] if parts else "ANY"


def _route_path(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[1] if len(parts) > 1 else route_line


def _route_coverage_requirements(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
    project_root: Path,
) -> list[dict[str, Any]]:
    text = "\n".join(
        [
            plan.problem_statement,
            task_contract.objective,
            *plan.acceptance_test_matrix,
            *plan.affected_surfaces,
            *task_contract.expected_outputs,
        ]
    )
    prefixes = _api_prefixes_from_text(text)
    if not prefixes:
        return []
    routes = route_inventory_for_prompt(
        discover_route_inventory(
            project_root,
            project_metadata,
            api_prefixes=prefixes,
        )
    )
    if not routes:
        return []
    acceptance = "\n".join(plan.acceptance_test_matrix).lower()
    require_all = "every existing" in acceptance or "every " in acceptance
    return [
        {
            "api_prefix": prefix,
            "required_routes": [
                route for route in routes if f" {prefix.rstrip('/')}" in route
            ],
            "coverage_rule": "all_routes" if require_all else "representative_routes",
            "authoring_instruction": (
                "For all_routes, include each route literal or equivalent route tail token "
                "in generated tests and cover each domain/parameter named by acceptance."
            ),
        }
        for prefix in prefixes
    ]


def _api_prefixes_from_text(text: str) -> list[str]:
    prefixes: set[str] = set()
    for raw in text.replace("*", " ").replace(",", " ").split():
        token = raw.strip("`'\"()[]{}.,;:")
        if not token.startswith("/api/"):
            continue
        parts = [part for part in token.split("/") if part and part != "..."]
        prefixes.add("/" + "/".join(parts[:2]) if len(parts) >= 2 else token.rstrip("/"))
    return sorted(prefixes)


def _verification_command(task_contract: TaskContract) -> list[str]:
    if task_contract.verification_commands:
        return task_contract.verification_commands[0]
    return ["pytest"]


def _path_violations(paths: list[str], task_contract: TaskContract) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in paths:
        allowed = any(_path_matches(path, root) for root in task_contract.allowed_paths)
        forbidden = any(_path_matches(path, root) for root in task_contract.forbidden_paths)
        qa_path = is_qa_write_path(path, task_contract.allowed_paths)
        if not allowed or forbidden or not qa_path:
            violations.append(
                {
                    "path": path,
                    "reason": "outside_allowed_paths"
                    if not allowed
                    else "forbidden_path"
                    if forbidden
                    else "not_a_qa_write_path",
                }
            )
    return violations


def _path_matches(path: str, root: str) -> bool:
    normalized_root = root.rstrip("/")
    return path == normalized_root or path.startswith(f"{normalized_root}/")


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


def _qa_model_route(
    project_metadata: dict[str, Any],
    settings: Any,
) -> dict[str, str]:
    qa_metadata = project_qa_metadata(project_metadata)
    route = qa_metadata.get("model_routing")
    default = route.get("default") if isinstance(route, dict) else None
    if not isinstance(default, dict):
        default = {}
    return {
        "claude_model": str(default.get("claude_model") or settings.qa_author_claude_model),
        "codex_model": str(default.get("model") or settings.qa_author_codex_model),
        "codex_reasoning": str(
            default.get("reasoning") or settings.qa_author_codex_reasoning
        ),
    }
