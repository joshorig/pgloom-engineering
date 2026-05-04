from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom_engineering.contracts import PlanContract, TaskContract
from pgloom_engineering.path_policy import is_qa_write_path
from pgloom_engineering.qa_runtime import (
    discover_route_inventory,
    project_qa_metadata,
    prompt_safe_qa_metadata,
    route_inventory_for_prompt,
)
from pgloom_engineering.qa_semantic_review import review_semantic_quality


def command_for_worktree(command: list[str], worktree: Path) -> list[str]:
    return [part.replace("{worktree}", str(worktree)) for part in command]


def normalize_qa_author_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("QAAuthorContract"), dict):
        return payload["QAAuthorContract"]
    return payload


def semantic_quality_findings(
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


def build_qa_author_prompt(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
    project_root: Path,
) -> str:
    qa_metadata = project_qa_metadata(project_metadata)
    route_requirements = route_coverage_requirements(
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
        "deterministic_test_skeleton": deterministic_test_skeleton(
            plan=plan,
            route_requirements=route_requirements,
            qa_metadata=qa_metadata,
        ),
        "generated_route_coverage_artifact": generated_route_coverage_artifact(route_requirements),
        "qa_context_capsule": build_qa_context_capsule(
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


def build_qa_context_capsule(
    *,
    route_requirements: list[dict[str, Any]],
    qa_metadata: dict[str, Any],
    plan: PlanContract,
) -> dict[str, Any]:
    return {
        "contract": "qa_context_capsule.v1",
        "purpose": "Stable project QA context for this task; prefer this over rediscovery.",
        "required_domains": domains_from_plan(plan),
        "generated_route_coverage_artifact": generated_route_coverage_artifact(route_requirements),
        "preferred_helpers": qa_metadata.get("preferred_helpers"),
        "behavior_coverage_rules": qa_metadata.get("behavior_coverage_rules"),
        "quality_gates": qa_metadata.get("quality_gates"),
    }


def generated_route_coverage_artifact(
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


def deterministic_test_skeleton(
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
        "required_domains": domains_from_plan(plan),
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
                        "method": route_method(route),
                        "path": route_path(route),
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


def domains_from_plan(plan: PlanContract) -> list[str]:
    text = "\n".join([plan.problem_statement, *plan.acceptance_test_matrix]).lower()
    domains = [domain for domain in ["equities", "crypto"] if domain in text]
    return domains or ["default"]


def route_method(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[0] if parts else "ANY"


def route_path(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[1] if len(parts) > 1 else route_line


def route_coverage_requirements(
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
    prefixes = api_prefixes_from_text(text)
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
            "required_routes": [route for route in routes if f" {prefix.rstrip('/')}" in route],
            "coverage_rule": "all_routes" if require_all else "representative_routes",
            "authoring_instruction": (
                "For all_routes, include each route literal or equivalent route tail token "
                "in generated tests and cover each domain/parameter named by acceptance."
            ),
        }
        for prefix in prefixes
    ]


def api_prefixes_from_text(text: str) -> list[str]:
    prefixes: set[str] = set()
    for raw in text.replace("*", " ").replace(",", " ").split():
        token = raw.strip("`'\"()[]{}.,;:")
        if not token.startswith("/api/"):
            continue
        parts = [part for part in token.split("/") if part and part != "..."]
        prefixes.add("/" + "/".join(parts[:2]) if len(parts) >= 2 else token.rstrip("/"))
    return sorted(prefixes)


def verification_command(task_contract: TaskContract) -> list[str]:
    if task_contract.verification_commands:
        return task_contract.verification_commands[0]
    return ["pytest"]


def path_violations(paths: list[str], task_contract: TaskContract) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in paths:
        allowed = any(path_matches(path, root) for root in task_contract.allowed_paths)
        forbidden = any(path_matches(path, root) for root in task_contract.forbidden_paths)
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


def path_matches(path: str, root: str) -> bool:
    normalized_root = root.rstrip("/")
    return path == normalized_root or path.startswith(f"{normalized_root}/")


def route_model_command(
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
        return replace_flag_value(routed, "--model", claude_model)
    if binary == "codex":
        routed = replace_flag_value(routed, "-m", codex_model)
        routed = replace_or_append_reasoning(routed, codex_reasoning)
    return routed


def replace_flag_value(command: list[str], flag: str, value: str) -> list[str]:
    routed = list(command)
    if flag in routed:
        index = routed.index(flag)
        if index + 1 < len(routed):
            routed[index + 1] = value
            return routed
    return [*routed, flag, value]


def replace_or_append_reasoning(command: list[str], reasoning: str) -> list[str]:
    routed = list(command)
    setting = f'model_reasoning_effort="{reasoning}"'
    for index, item in enumerate(routed):
        if item.startswith("model_reasoning_effort="):
            routed[index] = setting
            return routed
        if (
            item == "-c"
            and index + 1 < len(routed)
            and routed[index + 1].startswith("model_reasoning_effort=")
        ):
            routed[index + 1] = setting
            return routed
    return [*routed, "-c", setting]


def qa_model_route(
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
        "codex_reasoning": str(default.get("reasoning") or settings.qa_author_codex_reasoning),
    }
