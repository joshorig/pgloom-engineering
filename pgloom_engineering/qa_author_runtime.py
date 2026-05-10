from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom_engineering.contracts import (
    CONTRACT_VERSION,
    PlanContract,
    QAAuthorContract,
    TaskContract,
)
from pgloom_engineering.path_policy import is_qa_write_path
from pgloom_engineering.qa_runtime import (
    discover_route_inventory,
    project_qa_metadata,
    prompt_safe_qa_metadata,
    route_inventory_for_prompt,
    validate_required_qa_gates,
)
from pgloom_engineering.qa_semantic_review import review_semantic_quality
from pgloom_engineering.role_payloads import compact_plan_payload

MAX_REPAIR_FILE_CHARS = 12000
MAX_REPAIR_TOTAL_FILE_CHARS = 36000
MAX_REPAIR_RESPONSE_CHARS = 12000


def command_for_worktree(command: list[str], worktree: Path) -> list[str]:
    return [part.replace("{worktree}", str(worktree)) for part in command]


def isolate_codex_worktree_context(
    command: list[str],
    *,
    worktree: Path,
    context_root: Path,
    enabled: bool,
    add_dir_enabled: bool = True,
) -> list[str]:
    if not enabled or not command or Path(command[0]).name != "codex":
        return command
    isolated = list(command)
    context_root_text = str(context_root.resolve())
    if "-C" in isolated:
        index = isolated.index("-C")
        if index + 1 < len(isolated):
            isolated[index + 1] = context_root_text
    elif "--cd" in isolated:
        index = isolated.index("--cd")
        if index + 1 < len(isolated):
            isolated[index + 1] = context_root_text
    else:
        isolated.extend(["-C", context_root_text])

    if add_dir_enabled:
        worktree_text = str(worktree.resolve())
        add_dir_values = {
            isolated[index + 1]
            for index, item in enumerate(isolated[:-1])
            if item == "--add-dir"
        }
        if worktree_text not in add_dir_values:
            insert_at = len(isolated)
            if isolated and isolated[-1] == "-":
                insert_at = len(isolated) - 1
            isolated[insert_at:insert_at] = ["--add-dir", worktree_text]
    return isolated


def normalize_qa_author_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("QAAuthorContract"), dict):
        return payload["QAAuthorContract"]
    return payload


def infer_tests_added_from_paths(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if _looks_like_authored_test_or_benchmark(path)
    ]


def _looks_like_authored_test_or_benchmark(path: str) -> bool:
    lowered = path.lower()
    name = Path(path).name.lower()
    if "fixture" in name or "fixtures" in lowered:
        return False
    if "/src/test/" in lowered or lowered.startswith("tests/"):
        return name.endswith(("_test.py", "test.py", "test.java", "test.kt")) or (
            "test" in name and name.endswith((".java", ".kt", ".py"))
        )
    if "src/jmh/" in lowered or "benchmarks/src/" in lowered:
        return name.endswith(("benchmark.java", "bench.java", "benchmark.kt"))
    return False


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


def add_configured_gate_matrix_coverage(
    contract: QAAuthorContract,
    *,
    plan: PlanContract,
    worktree: Path,
    project_metadata: dict[str, Any],
    task_contract: TaskContract | None = None,
) -> QAAuthorContract:
    configured_gates = [
        item
        for item in validate_required_qa_gates(worktree, project_metadata)
        if item.get("status") == "configured"
    ]
    if not configured_gates and task_contract is None:
        return contract
    matrix = dict(contract.matrix_coverage)
    for criterion in plan.acceptance_test_matrix:
        if matrix.get(criterion) or not _criterion_is_configured_gate(criterion):
            continue
        evidence = [
            str(command[0])
            for item in configured_gates
            if isinstance(command := item.get("command"), list) and command
        ]
        if not evidence and task_contract is not None:
            evidence = [
                " ".join(command)
                for command in verification_commands(task_contract)
                if _command_is_gate(command)
            ]
        if evidence:
            matrix[criterion] = evidence
    if matrix == contract.matrix_coverage:
        return contract
    return contract.model_copy(update={"matrix_coverage": matrix})


def _criterion_is_configured_gate(criterion: str) -> bool:
    lowered = criterion.lower()
    return (
        "qa gate" in lowered
        or "qa gates" in lowered
        or "qa/smoke" in lowered
        or "qa/regression" in lowered
        or "allocation gate" in lowered
        or "configured" in lowered and ("gate" in lowered or "gates" in lowered)
    )


def _command_is_gate(command: list[str]) -> bool:
    text = " ".join(command).lower()
    return "qa/smoke" in text or "qa/regression" in text or "gradlew" in text


def build_qa_author_prompt(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
    project_root: Path,
    role_context: dict[str, Any] | None = None,
) -> str:
    qa_metadata = project_qa_metadata(project_metadata)
    route_requirements = route_coverage_requirements(
        plan,
        task_contract,
        project_metadata=project_metadata,
        project_root=project_root,
    )
    benchmark_requirements = benchmark_requirements_for_task(plan, task_contract, qa_metadata)
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
            (
                "If project_qa_metadata requires an HTTP harness for endpoint acceptance, "
                "direct controller construction or controller.method(...) calls are forbidden "
                "for route/query/status acceptance and will be rejected by deterministic review."
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
            (
                "Do not use reflection proxies, InvocationHandler, boxed callbacks, or "
                "other allocating indirection inside measured benchmark operations."
            ),
            (
                "When a new benchmark is required by an allocation gate, update only "
                "authorized test support files from project_qa_metadata so the benchmark "
                "is actually executed by the smoke/regression gate."
            ),
            (
                "For prefix, filter, route, or query behavior, write behavior tests with "
                "matching and non-matching cases; overload or route inventory checks alone "
                "do not satisfy acceptance."
            ),
            (
                "For range-scan or public API acceptance, compile tests and benchmarks "
                "against the typed public API directly. Do not use Class.forName, "
                "java.lang.reflect.Modifier, Method.invoke, InvocationHandler, Proxy, "
                "MethodHandle adapters, LambdaMetafactory, class metadata assertions, or "
                "annotation-presence checks to avoid compile-time API checks."
            ),
            (
                "Before final submission, run the narrowest compile/test command for every "
                "authored test file when the tool environment permits it. Returned tests should "
                "compile cleanly unless this task defines a new public API; in that case a "
                "missing-symbol compile failure for the expected API type or method is valid "
                "red proof. Syntax, import, fixture, dependency, and incompatible-signature "
                "compile errors are never valid red proof."
            ),
            (
                "Self-validate authored tests before handing them to review. If validation "
                "shows compile/import/syntax errors unrelated to a missing expected public API, "
                "repair the tests before returning the QAAuthorContract."
            ),
            (
                "If deterministic_test_skeleton has benchmark cases, parameterize generated "
                "benchmark coverage over every required variant."
            ),
            "The orchestrator will run verification and create canonical red_proof.",
            (
                "matrix_coverage must include every exact string from "
                "plan.acceptance_test_matrix as a key, with each value naming the concrete "
                "authored test, benchmark, or configured QA gate that covers that criterion."
            ),
            "Return only a QAAuthorContract JSON object.",
            (
                "Do not include command logs, exploration notes, file diffs, or "
                "commentary in the final response."
            ),
        ],
        "project_qa_metadata": prompt_safe_qa_metadata(qa_metadata),
        "project_authorized_test_support_paths": _metadata_test_support_paths(qa_metadata),
        "role_context": role_context or {},
        "route_coverage_requirements": route_requirements,
        "benchmark_requirements": benchmark_requirements,
        "deterministic_test_skeleton": deterministic_test_skeleton(
            plan=plan,
            route_requirements=route_requirements,
            benchmark_requirements=benchmark_requirements,
            qa_metadata=qa_metadata,
        ),
        "generated_route_coverage_artifact": generated_route_coverage_artifact(route_requirements),
        "qa_context_capsule": build_qa_context_capsule(
            route_requirements=route_requirements,
            benchmark_requirements=benchmark_requirements,
            qa_metadata=qa_metadata,
            plan=plan,
        ),
        "plan": compact_plan_payload(plan),
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
    benchmark_requirements: list[dict[str, Any]] | None = None,
    qa_metadata: dict[str, Any],
    plan: PlanContract,
) -> dict[str, Any]:
    return {
        "contract": "qa_context_capsule.v1",
        "purpose": "Stable project QA context for this task; prefer this over rediscovery.",
        "required_domains": domains_from_plan(plan),
        "generated_route_coverage_artifact": generated_route_coverage_artifact(route_requirements),
        "benchmark_requirements": benchmark_requirements or [],
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
    benchmark_requirements: list[dict[str, Any]] | None = None,
    qa_metadata: dict[str, Any],
) -> dict[str, Any]:
    skeleton: dict[str, Any] = {
        "purpose": (
            "Use this deterministic scaffold before adding project-specific fixtures; "
            "inventory-only tests do not satisfy behavior coverage."
        ),
        "required_domains": domains_from_plan(plan),
        "endpoint_behavior_skeleton": [],
        "benchmark_behavior_skeleton": benchmark_requirements or [],
    }
    for key in ["preferred_test_skeletons", "preferred_helpers", "behavior_coverage_rules"]:
        value = qa_metadata.get(key)
        if value is not None:
            skeleton[key] = value
    conventions = qa_metadata.get("semantic_conventions")
    endpoint_acceptance = None
    if isinstance(conventions, dict):
        endpoint_acceptance = conventions.get("endpoint_acceptance")
    if endpoint_acceptance is None:
        endpoint_acceptance = qa_metadata.get("endpoint_acceptance")
    if isinstance(endpoint_acceptance, dict) and endpoint_acceptance.get("require_http_harness"):
        skeleton["spring_endpoint_harness_required"] = {
            "allowed_harnesses": ["MockMvc", "WebTestClient", "TestRestTemplate"],
            "standalone_pattern": (
                "For focused controller tests, build MockMvc with "
                "MockMvcBuilders.standaloneSetup(controller).setControllerAdvice(...).build(), "
                "then call mockMvc.perform(get(path).param(\"domain\", value)) or matching "
                "HTTP verbs. Do not call controller.method(...) directly for route/query/status "
                "acceptance."
            ),
            "rejects": [
                "new SomeController(...); controller.someRoute(domain)",
                "ResponseEntity assertions from direct controller method calls",
            ],
            "patch_template": {
                "imports": [
                    "org.springframework.test.web.servlet.MockMvc",
                    "org.springframework.test.web.servlet.setup.MockMvcBuilders",
                    "static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*",
                    "static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*",
                ],
                "setup": (
                    "MockMvc mockMvc = MockMvcBuilders.standaloneSetup(controller)"
                    ".setControllerAdvice(controllerAdvice).build();"
                ),
                "route_case_pattern": (
                    "mockMvc.perform(get(path).param(\"domain\", domain))"
                    ".andExpect(status().isOk()).andExpect(jsonPath(jsonPathExpression)"
                    ".value(expectedValue));"
                ),
                "forbidden_rewrites": [
                    "Replacing HTTP-route assertions with controller.method(...) calls",
                    "Asserting ResponseEntity returned by direct controller invocation",
                ],
            },
            "test_support_dependency_guidance": (
                "If the selected harness dependency is missing and the project metadata lists "
                "authorized test support paths, add only test-scoped dependencies in those "
                "support files; do not fall back to direct controller method calls."
            ),
        }
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
                            "Invoke the matching HTTP route through MockMvc, WebTestClient, "
                            "TestRestTemplate, or the project-approved HTTP harness for each "
                            "required domain and assert domain-specific identifiers/config/"
                            "partition/service state."
                        ),
                    }
                    for route in requirement.get("required_routes", [])
                    if isinstance(route, str)
                ],
                "anti_pattern": (
                    "Do not create a test that only asserts this route_cases list; each "
                    "case must drive product behavior."
                ),
            }
        )
    return skeleton


def benchmark_requirements_for_task(
    plan: PlanContract,
    task_contract: TaskContract,
    qa_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    text = "\n".join(
        [
            plan.problem_statement,
            task_contract.objective,
            *plan.acceptance_test_matrix,
            *task_contract.expected_outputs,
            *task_contract.allowed_paths,
        ]
    ).lower()
    if not any(token in text for token in ["benchmark", "jmh", "latency", "p99"]):
        return []
    variants = benchmark_variants_from_text(text, qa_metadata)
    roots = [
        str(root)
        for root in qa_metadata.get("benchmark_roots", [])
        if isinstance(root, str)
    ]
    return [
        {
            "workflow": "benchmark_acceptance",
            "framework": "jmh" if "jmh" in text else "project_benchmark_harness",
            "benchmark_roots": roots,
            "required_variants": variants,
            "authoring_requirements": [
                "create benchmark fixture state before measured iterations",
                "measured benchmark methods perform only the operation under test",
                (
                    "do not allocate object graphs, collections, temp files, or stores "
                    "in @Benchmark methods"
                ),
                "parameterize benchmark coverage over every required variant",
            ],
        }
    ]


def benchmark_variants_from_text(text: str, qa_metadata: dict[str, Any]) -> list[str]:
    configured = _configured_benchmark_variants(qa_metadata)
    if configured:
        return configured
    known = ["single", "double", "direct", "mmap"]
    return [variant for variant in known if variant in text]


def _configured_benchmark_variants(qa_metadata: dict[str, Any]) -> list[str]:
    raw = qa_metadata.get("benchmark_variants")
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str)]
    conventions = qa_metadata.get("semantic_conventions")
    if not isinstance(conventions, dict):
        return []
    benchmark = conventions.get("restore_benchmark")
    if not isinstance(benchmark, dict):
        return []
    variants = benchmark.get("variants")
    if isinstance(variants, list):
        return [str(item) for item in variants if isinstance(item, str)]
    return []


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


def route_matches_prefix(route_line: str, prefix: str) -> bool:
    path = route_path(route_line)
    normalized_prefix = prefix.rstrip("/")
    return path == normalized_prefix or path.startswith(f"{normalized_prefix}/")


def route_coverage_requirements(
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
    project_root: Path,
) -> list[dict[str, Any]]:
    qa_metadata = project_qa_metadata(project_metadata)
    configured_requirements = qa_metadata.get("route_coverage_requirements")
    if isinstance(configured_requirements, list):
        return [
            requirement
            for requirement in configured_requirements
            if isinstance(requirement, dict)
        ]
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
            "required_routes": [
                route for route in routes if route_matches_prefix(route, prefix)
            ],
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
        token = raw.strip("`'\"()[].,;:")
        if not token.startswith("/api/"):
            continue
        parts = [part for part in token.split("/") if part and part != "..."]
        if len(parts) >= 2:
            prefixes.add("/" + "/".join(parts))
        else:
            prefixes.add(token.rstrip("/"))
    return sorted(prefixes)


def verification_command(task_contract: TaskContract) -> list[str]:
    return verification_commands(task_contract)[0]


def verification_commands(task_contract: TaskContract) -> list[list[str]]:
    if task_contract.verification_commands:
        return task_contract.verification_commands
    return [["pytest"]]


def red_proof_verification_commands(
    task_contract: TaskContract,
    changed_paths: list[str],
    *,
    selected_command: list[str] | None = None,
) -> list[list[str]]:
    commands = (
        [selected_command]
        if selected_command is not None
        else verification_commands(task_contract)
    )
    result = _module_local_gradle_test_commands(changed_paths)
    for command in commands:
        if command not in result:
            result.append(command)
    return result


def qa_code_repairable(outcome: dict[str, Any]) -> bool:
    findings = outcome.get("findings", [])
    if not isinstance(findings, list):
        return False
    codes = {finding.get("code") for finding in findings if isinstance(finding, dict)}
    reasons = {
        reason
        for finding in findings
        if isinstance(finding, dict)
        if (reason := finding.get("reason")) is not None
    }
    allowed_codes = {
        "tests_not_red",
        "qa_tests_do_not_compile",
        "invalid_qa_author_contract",
        "missing_matrix_coverage",
        "missing_tests_added",
        "missing_red_proof",
    }
    return (
        bool(outcome.get("changed_files"))
        and bool(codes & {"tests_not_red", "qa_tests_do_not_compile"})
        and codes <= allowed_codes
        and not reasons
    )


def build_qa_code_repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    changed_files: list[str],
    verification_command: list[str],
    stdout_excerpt: str,
    stderr_excerpt: str,
    current_contract: object,
    project_metadata: dict[str, Any] | None = None,
) -> str:
    qa_metadata = project_qa_metadata(project_metadata or {})
    test_support_files = _metadata_test_support_paths(qa_metadata)
    repair_files = _dedupe_paths([*changed_files, *test_support_files])
    test_contents = repair_file_contents(worktree, repair_files)
    return json.dumps(
        {
            "role": "qa.author.red_repair",
            "instructions": [
                (
                    "Edit only the files listed in changed_files and "
                    "authorized_test_support_files."
                ),
                "Do not edit production source files.",
                (
                    "Authorized test support files may be changed only for test-scoped "
                    "dependency or test-runner wiring needed by the authored QA tests."
                ),
                (
                    "Make the smallest targeted repair that lets the selected "
                    "verification command run."
                ),
                (
                    "The selected verification command must fail because the authored acceptance "
                    "test exposes missing product behavior. For new public API features, a "
                    "missing-symbol compile failure for the expected API type or method counts "
                    "as red proof; syntax errors, import errors, missing dependencies, "
                    "incompatible signatures, and sandbox/tool failures do not."
                ),
                (
                    "Run the narrowest available compile/test command for the changed test files "
                    "before returning. Fix authored test compile/import/syntax errors first, "
                    "except for missing expected public API symbols on new-API tasks."
                ),
                (
                    "Keep acceptance coverage intact. If you remove a test, replace its matrix "
                    "coverage with an equivalent behavior assertion in another authored test."
                ),
                (
                    "If endpoint acceptance requires MockMvc, WebTestClient, or TestRestTemplate "
                    "and the compile failure is a missing harness dependency, add the test-scoped "
                    "dependency in an authorized test support file. Do not replace route-harness "
                    "assertions with direct controller method calls."
                ),
                "Return only a valid QAAuthorContract JSON object for the repaired files.",
            ],
            "feature_id": task_contract.feature_id,
            "task_id": task_contract.inputs.get("task_id"),
            "acceptance_test_matrix": plan.acceptance_test_matrix,
            "selected_verification_command": verification_command,
            "verification_stdout_excerpt": stdout_excerpt,
            "verification_stderr_excerpt": stderr_excerpt,
            "changed_files": changed_files,
            "authorized_test_support_files": test_support_files,
            "repair_files": repair_files,
            "file_contents": test_contents,
            "current_contract": current_contract,
        },
        indent=2,
        sort_keys=True,
    )


def qa_quality_repairable(quality_review: dict[str, Any]) -> bool:
    findings = quality_review.get("blocking_findings")
    if not isinstance(findings, list) or not findings:
        return False
    allowed = {
        "qa_review_playwright_mocked_only",
        "qa_review_playwright_missing_request_shape",
        "qa_review_broad_existing_ui_spec_modified",
        "qa_review_benchmark_allocates_after_setup",
        "qa_review_benchmark_variant_gap",
        "qa_review_unconsumed_fixture",
        "qa_semantic_brittle_array_assertion",
        "qa_semantic_direct_spring_controller_call",
        "qa_semantic_brittle_payload_assertions",
        "qa_semantic_journal_cursor_mismatch",
        "qa_semantic_jmh_exhaustible_target_pool",
        "qa_semantic_jmh_reflective_invocation",
        "qa_semantic_jmh_restore_not_cold",
        "qa_semantic_jmh_restore_target_reuse",
        "qa_semantic_range_test_reflective_api",
        "qa_semantic_range_prefix_behavior_missing",
        "qa_semantic_build_file_string_assertion",
    }
    codes = {finding.get("code") for finding in findings if isinstance(finding, dict)}
    return bool(codes) and codes <= allowed


def build_qa_quality_repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    changed_files: list[str],
    quality_review: dict[str, Any],
    current_contract: object,
    project_metadata: dict[str, Any] | None = None,
) -> str:
    qa_metadata = project_qa_metadata(project_metadata or {})
    test_support_files = _metadata_test_support_paths(qa_metadata)
    repair_files = _dedupe_paths(
        [*qa_quality_repair_file_set(quality_review, changed_files), *test_support_files]
    )
    file_contents = repair_file_contents(worktree, repair_files)
    return json.dumps(
        {
            "role": "qa.author.quality_repair",
            "instructions": [
                "Edit only the files listed in repair_files unless removing an "
                "unconsumed fixture is explicitly required.",
                "Do not edit production source files.",
                (
                    "Authorized test support files may be changed only for test-scoped "
                    "dependency or test-runner wiring needed by the authored QA tests."
                ),
                "Keep existing acceptance coverage; make the smallest targeted repair.",
                (
                    "For UI findings, prefer a focused task-specific spec. If API routes "
                    "are mocked, assert request URL/query shape and visible state."
                ),
                (
                    "For benchmark findings, move all object graphs, temp files, fixture "
                    "creation, collections, and store construction into trial setup. "
                    "Do not use JMH invocation or iteration setup for zero-garbage contracts."
                ),
                (
                    "For semantic findings, repair the exact behavioral weakness. Use HTTP "
                    "test harnesses for endpoint route contracts. For direct Spring controller "
                    "call findings, replace controller construction and controller.method(...) "
                    "calls with MockMvc, WebTestClient, or TestRestTemplate route invocations "
                    "that prove query parsing, route binding, status, and JSON payload behavior. "
                    "If the harness dependency is missing, add it as a test-scoped dependency "
                    "in an authorized test support file instead of reverting to direct "
                    "controller calls. "
                    "Assert last acknowledged journal cursors after failed writes, use "
                    "assertArrayEquals for byte arrays, use structured JSON field/path "
                    "assertions for payload contracts, and use a non-allocating cold benchmark "
                    "strategy that cannot exhaust a finite one-shot target pool during JMH "
                    "measurement. For range-scan API tests, import and compile against "
                    "the typed public API directly; do not use Class.forName, "
                    "java.lang.reflect.Modifier, Method.invoke, InvocationHandler, Proxy, "
                    "class metadata assertions, or annotation-presence checks to avoid "
                    "compile-time API checks."
                ),
                (
                    "For JMH reflective invocation findings, remove reflection, Proxy, "
                    "MethodHandle adapter, and LambdaMetafactory invocation paths from the "
                    "benchmark harness. Import the typed feature API directly and call it "
                    "from the @Benchmark method or a typed setup-created helper so JMH "
                    "executes the feature and not an adapter."
                ),
                (
                    "For build/script string assertion findings, remove the generated test "
                    "that reads build files or QA scripts. Deterministic orchestration validates "
                    "QA gate wiring; model-authored tests must prove product behavior."
                ),
                "Run the narrowest compile/test command for repaired files before returning.",
                "Return only a valid QAAuthorContract JSON object for the repaired files.",
            ],
            "feature_id": task_contract.feature_id,
            "task_id": task_contract.inputs.get("task_id"),
            "acceptance_test_matrix": plan.acceptance_test_matrix,
            "quality_findings": quality_review.get("blocking_findings"),
            "repair_files": repair_files,
            "authorized_test_support_files": test_support_files,
            "file_contents": file_contents,
            "current_contract": current_contract,
        },
        indent=2,
        sort_keys=True,
    )


def qa_quality_repair_file_set(
    quality_review: dict[str, Any],
    changed_files: list[str],
) -> list[str]:
    files: set[str] = set()
    for finding in quality_review.get("blocking_findings", []):
        if not isinstance(finding, dict):
            continue
        value = finding.get("file")
        if isinstance(value, str):
            files.add(value.removeprefix("changed-files/"))
        for key in ["files", "fixture_files", "benchmark_files"]:
            value = finding.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        files.add(item.removeprefix("changed-files/"))
    if files:
        return sorted(files)
    return sorted(changed_files)


def _dedupe_paths(paths: list[str]) -> list[str]:
    return [path for path in dict.fromkeys(paths) if path]


def build_qa_contract_repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    changed_files: list[str],
    raw_response: str,
    validation_error: str,
) -> str:
    test_contents = repair_file_contents(worktree, changed_files)
    return json.dumps(
        {
            "role": "qa.author.contract_repair",
            "instructions": [
                "Do not edit files.",
                "Return only a valid QAAuthorContract JSON object.",
                f"contract_version must be the string {CONTRACT_VERSION!r}.",
                f"feature_id must be {task_contract.feature_id!r}.",
                f"task_id must be {task_contract.inputs.get('task_id')!r}.",
                "tests_added must be a list of test file paths or test identifiers.",
                (
                    "matrix_coverage must map each acceptance criterion string to one "
                    "or more authored test identifiers."
                ),
                "red_proof may be empty; the orchestrator will replace it after verification.",
                "paths_touched must list the authored QA/test files.",
                "Do not include repair_files, file_contents, role, instructions, or notes.",
            ],
            "feature_id": task_contract.feature_id,
            "task_id": task_contract.inputs.get("task_id"),
            "acceptance_test_matrix": plan.acceptance_test_matrix,
            "changed_files": changed_files,
            "file_contents": test_contents,
            "validation_error": validation_error,
            "raw_response": truncate_text(raw_response, MAX_REPAIR_RESPONSE_CHARS),
        },
        indent=2,
        sort_keys=True,
    )


def repair_file_contents(worktree: Path, paths: list[str]) -> dict[str, str]:
    contents: dict[str, str] = {}
    remaining = MAX_REPAIR_TOTAL_FILE_CHARS
    for path in paths:
        file_path = worktree / path
        if remaining <= 0 or not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        limit = min(MAX_REPAIR_FILE_CHARS, remaining)
        contents[path] = truncate_text(text, limit)
        remaining -= len(contents[path])
    return contents


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 64:
        return text[:limit]
    head = max(1, (limit - 64) // 2)
    tail = max(1, limit - 64 - head)
    omitted = len(text) - head - tail
    return (
        text[:head]
        + f"\n... [truncated {omitted} chars for prompt budget] ...\n"
        + text[-tail:]
    )


def _module_local_gradle_test_commands(changed_paths: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    for path in changed_paths:
        parsed = _gradle_test_path(path)
        if parsed is None:
            continue
        module, class_name = parsed
        task = "test" if not module else f":{module.replace('/', ':')}:test"
        commands.append(
            [
                "./gradlew",
                "--no-daemon",
                "--console=plain",
                task,
                "--tests",
                class_name,
            ]
        )
    return _dedupe_commands(commands)


def _gradle_test_path(path: str) -> tuple[str, str] | None:
    for source_set, suffix in [
        ("src/test/java/", ".java"),
        ("src/test/kotlin/", ".kt"),
    ]:
        if source_set not in path or not path.endswith(suffix):
            continue
        module, relative = path.split(source_set, 1)
        module = module.rstrip("/")
        class_name = relative.removesuffix(suffix).replace("/", ".")
        if not class_name:
            return None
        return module, class_name
    return None


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    result: list[list[str]] = []
    for command in commands:
        if command not in result:
            result.append(command)
    return result


def path_violations(
    paths: list[str],
    task_contract: TaskContract,
    project_metadata: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    metadata = project_qa_metadata(project_metadata or {})
    qa_write_paths = _metadata_qa_write_paths(metadata)
    extra_paths = _metadata_authorized_extra_paths(metadata, task_contract)
    allowed_paths = [*task_contract.allowed_paths, *extra_paths]
    test_support_paths = extra_paths
    violations: list[dict[str, str]] = []
    for path in paths:
        support_path = any(path_matches(path, root) for root in test_support_paths)
        allowed = any(path_matches(path, root) for root in allowed_paths)
        forbidden = (
            any(path_matches(path, root) for root in task_contract.forbidden_paths)
            and not support_path
        )
        qa_path = is_qa_write_path(path, qa_write_paths)
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


def _metadata_qa_write_paths(qa_metadata: dict[str, Any]) -> list[str]:
    paths: list[str] = ["tests/", "qa/fixtures/"]
    for key in ["test_roots", "browser_test_roots", "benchmark_roots", "test_support_paths"]:
        raw = qa_metadata.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                normalized = item.rstrip("/") + "/"
                if normalized not in paths:
                    paths.append(normalized)
    return paths


def _metadata_test_support_paths(qa_metadata: dict[str, Any]) -> list[str]:
    raw = qa_metadata.get("test_support_paths")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _metadata_authorized_extra_paths(
    qa_metadata: dict[str, Any],
    task_contract: TaskContract,
) -> list[str]:
    paths = list(_metadata_test_support_paths(qa_metadata))
    task_text = " ".join(
        [
            task_contract.objective,
            " ".join(task_contract.expected_outputs),
            " ".join(" ".join(command) for command in task_contract.verification_commands),
        ]
    ).lower()
    if any(token in task_text for token in ("benchmark", "jmh", "allocation")):
        raw = qa_metadata.get("benchmark_roots")
        if isinstance(raw, list):
            paths.extend(item for item in raw if isinstance(item, str) and item.strip())
    return _dedupe_paths(paths)


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
    *,
    tier: str = "default",
) -> dict[str, str]:
    qa_metadata = project_qa_metadata(project_metadata)
    route = qa_metadata.get("model_routing")
    selected = route.get(tier) if isinstance(route, dict) else None
    if not isinstance(selected, dict) and tier != "default" and isinstance(route, dict):
        selected = route.get("default")
    if not isinstance(selected, dict):
        selected = {}
    return {
        "claude_model": str(selected.get("claude_model") or settings.qa_author_claude_model),
        "codex_model": str(selected.get("model") or settings.qa_author_codex_model),
        "codex_reasoning": str(selected.get("reasoning") or settings.qa_author_codex_reasoning),
    }
