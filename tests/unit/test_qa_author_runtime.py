from __future__ import annotations

import json
import sys
from pathlib import Path

from pgloom_engineering.contracts import (
    DesignContract,
    PlanContract,
    QAAuthorContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.qa_author_runtime import (
    add_configured_gate_matrix_coverage,
    api_prefixes_from_text,
    benchmark_requirements_for_task,
    build_qa_author_prompt,
    build_qa_code_repair_prompt,
    build_qa_quality_repair_prompt,
    infer_tests_added_from_paths,
    isolate_codex_worktree_context,
    normalize_qa_author_payload,
    path_violations,
    red_proof_verification_commands,
    repair_file_contents,
    route_matches_prefix,
    truncate_text,
    verification_command,
    verification_commands,
)


def test_qa_author_runtime_builds_shared_prompt_shape() -> None:
    plan = _plan()
    task = _task_contract()

    payload = json.loads(
        build_qa_author_prompt(
            plan,
            task,
            project_metadata={"qa": {"test_roots": ["tests/"]}},
            project_root=Path("."),
        )
    )

    assert payload["role"] == "qa.author"
    assert payload["plan"]["feature_id"] == "feature-1"
    assert payload["task_contract"]["task_type"] == "engineering.qa.author"
    assert payload["qa_context_capsule"]["contract"] == "qa_context_capsule.v1"
    assert "deterministic_test_skeleton" in payload
    assert any(
        "missing-symbol compile failure" in instruction
        for instruction in payload["instructions"]
    )
    assert any(
        "typed public API directly" in instruction
        and "Class.forName" in instruction
        and "LambdaMetafactory" in instruction
        for instruction in payload["instructions"]
    )


def test_qa_author_prompt_includes_shared_role_context() -> None:
    payload = json.loads(
        build_qa_author_prompt(
            _plan(),
            _task_contract(),
            project_metadata={},
            project_root=Path("."),
            role_context={
                "contract": "engineering.role_context.v1",
                "packed_context": "packed",
            },
        )
    )

    assert payload["role_context"]["contract"] == "engineering.role_context.v1"
    assert payload["role_context"]["packed_context"] == "packed"


def test_qa_author_prompt_skeleton_requires_spring_http_harness() -> None:
    payload = json.loads(
        build_qa_author_prompt(
            _plan(),
            _task_contract(),
            project_metadata={
                "qa": {
                    "semantic_conventions": {
                        "endpoint_acceptance": {"require_http_harness": True}
                    }
                }
            },
            project_root=Path("."),
        )
    )

    harness = payload["deterministic_test_skeleton"]["spring_endpoint_harness_required"]
    assert "MockMvc" in harness["allowed_harnesses"]
    assert "MockMvcBuilders.standaloneSetup" in harness["standalone_pattern"]
    assert "controller.method" in harness["standalone_pattern"]
    assert "MockMvcBuilders" in harness["patch_template"]["imports"][1]
    assert "mockMvc.perform" in harness["patch_template"]["route_case_pattern"]
    assert "direct controller invocation" in harness["patch_template"]["forbidden_rewrites"][1]


def test_qa_author_runtime_normalizes_wrapped_contract_payload() -> None:
    wrapped = {"QAAuthorContract": {"feature_id": "feature-1"}}

    assert normalize_qa_author_payload(wrapped) == {"feature_id": "feature-1"}
    assert normalize_qa_author_payload({"feature_id": "feature-1"}) == {"feature_id": "feature-1"}


def test_qa_author_runtime_path_and_command_helpers_are_shared() -> None:
    task = _task_contract()

    assert verification_command(task) == [sys.executable, "-m", "pytest", "tests", "-q"]
    assert verification_commands(task) == [[sys.executable, "-m", "pytest", "tests", "-q"]]
    assert path_violations(["src/feature.py", "tests/test_feature.py"], task) == [
        {"path": "src/feature.py", "reason": "outside_allowed_paths"}
    ]


def test_path_violations_rejects_non_qa_paths_even_when_allowed() -> None:
    task = _task_contract().model_copy(update={"allowed_paths": ["src/"]})

    assert path_violations(["src/feature.py"], task) == [
        {"path": "src/feature.py", "reason": "not_a_qa_write_path"}
    ]


def test_path_violations_accepts_project_benchmark_roots_when_allowed() -> None:
    task = _task_contract().model_copy(
        update={"allowed_paths": ["benchmarks/src/jmh/java/"]}
    )

    assert (
        path_violations(
            ["benchmarks/src/jmh/java/com/example/RestoreBenchmark.java"],
            task,
            {"qa": {"benchmark_roots": ["benchmarks/src/jmh/java"]}},
        )
        == []
    )


def test_path_violations_accepts_benchmark_root_when_task_requires_jmh() -> None:
    task = _task_contract().model_copy(
        update={
            "objective": "Write failing JMH benchmark coverage.",
            "expected_outputs": ["benchmarks/src/jmh/java/com/example/RestoreBenchmark.java"],
        }
    )

    assert (
        path_violations(
            ["benchmarks/src/jmh/java/com/example/RestoreBenchmark.java"],
            task,
            {"qa": {"benchmark_roots": ["benchmarks/src/jmh/java"]}},
        )
        == []
    )


def test_path_violations_accepts_project_test_support_paths() -> None:
    task = _task_contract()

    assert (
        path_violations(
            ["app-api/build.gradle", "tests/test_feature.py"],
            task,
            {"qa": {"test_support_paths": ["app-api/build.gradle"]}},
        )
        == []
    )


def test_path_violations_accepts_project_test_support_path_under_forbidden_root() -> None:
    task = _task_contract().model_copy(update={"forbidden_paths": ["app-api/"]})

    assert (
        path_violations(
            ["app-api/build.gradle"],
            task,
            {"qa": {"test_support_paths": ["app-api/build.gradle"]}},
        )
        == []
    )


def test_code_repair_prompt_allows_authorized_test_support_files(tmp_path: Path) -> None:
    tmp_path.joinpath("tests").mkdir()
    tmp_path.joinpath("tests/test_feature.py").write_text("def test_feature(): pass\n")
    tmp_path.joinpath("app-api").mkdir()
    tmp_path.joinpath("app-api/build.gradle").write_text("dependencies {}\n")

    payload = json.loads(
        build_qa_code_repair_prompt(
            plan=_plan(),
            task_contract=_task_contract(),
            worktree=tmp_path,
            changed_files=["tests/test_feature.py"],
            verification_command=["./gradlew", ":app-api:test"],
            stdout_excerpt="compileTestJava FAILED",
            stderr_excerpt="package org.springframework.test.web.servlet does not exist",
            current_contract={"tests_added": ["tests/test_feature.py"]},
            project_metadata={"qa": {"test_support_paths": ["app-api/build.gradle"]}},
        )
    )

    assert payload["authorized_test_support_files"] == ["app-api/build.gradle"]
    assert payload["repair_files"] == ["tests/test_feature.py", "app-api/build.gradle"]
    assert "dependencies {}" in payload["file_contents"]["app-api/build.gradle"]
    assert any(
        "Do not replace route-harness assertions" in item
        for item in payload["instructions"]
    )


def test_quality_repair_prompt_can_repair_harness_dependencies(tmp_path: Path) -> None:
    tmp_path.joinpath("tests").mkdir()
    tmp_path.joinpath("tests/EndpointTest.java").write_text("controller.runtime();\n")
    tmp_path.joinpath("app-api").mkdir()
    tmp_path.joinpath("app-api/build.gradle").write_text("dependencies {}\n")

    payload = json.loads(
        build_qa_quality_repair_prompt(
            plan=_plan(),
            task_contract=_task_contract(),
            worktree=tmp_path,
            changed_files=["tests/EndpointTest.java"],
            quality_review={
                "blocking_findings": [
                    {
                        "code": "qa_semantic_direct_spring_controller_call",
                        "file": "tests/EndpointTest.java",
                    }
                ]
            },
            current_contract={"tests_added": ["tests/EndpointTest.java"]},
            project_metadata={"qa": {"test_support_paths": ["app-api/build.gradle"]}},
        )
    )

    assert payload["repair_files"] == ["tests/EndpointTest.java", "app-api/build.gradle"]
    assert any("test-scoped dependency" in item for item in payload["instructions"])


def test_benchmark_requirements_include_project_variants_and_prompt_skeleton() -> None:
    plan = _plan().model_copy(
        update={
            "problem_statement": "Add restore latency JMH benchmark coverage.",
            "acceptance_test_matrix": [
                "Restore latency covers SINGLE and DOUBLE direct/mmap stores."
            ],
        }
    )
    task = _task_contract().model_copy(
        update={
            "objective": "Write failing JMH benchmark for restore latency.",
            "allowed_paths": ["benchmarks/src/jmh/java/"],
        }
    )
    metadata = {
        "benchmark_roots": ["benchmarks/src/jmh/java"],
        "benchmark_variants": ["single", "double", "direct", "mmap"],
    }

    assert benchmark_requirements_for_task(plan, task, metadata)[0]["required_variants"] == [
        "single",
        "double",
        "direct",
        "mmap",
    ]
    payload = json.loads(
        build_qa_author_prompt(
            plan,
            task,
            project_metadata={"qa": metadata},
            project_root=Path("."),
        )
    )

    benchmark_skeleton = payload["deterministic_test_skeleton"][
        "benchmark_behavior_skeleton"
    ][0]
    assert benchmark_skeleton["benchmark_roots"] == ["benchmarks/src/jmh/java"]
    assert benchmark_skeleton["required_variants"] == [
        "single",
        "double",
        "direct",
        "mmap",
    ]


def test_qa_author_prompt_uses_explicit_route_coverage_requirements_as_source_of_truth(
    tmp_path: Path,
) -> None:
    plan = _plan().model_copy(
        update={
            "problem_statement": "Cover /api/config routes for equities and crypto.",
            "acceptance_test_matrix": [
                "Every existing /api/config/* route is exercised for equities and crypto."
            ],
        }
    )
    task = _task_contract().model_copy(
        update={"objective": "Write failing endpoint tests for /api/config routes."}
    )
    route_requirements = [
        {
            "api_prefix": "/api/config",
            "coverage_rule": "representative_routes",
            "authoring_instruction": (
                "For all_routes, include each route literal or equivalent route tail token "
                "in generated tests and cover each domain/parameter named by acceptance."
            ),
            "required_routes": [
                "GET /api/config/access (app-api/src/main/java/example/ConfigController.java)",
                "PUT /api/config/runtime (app-api/src/main/java/example/ConfigController.java)",
            ],
        }
    ]

    payload = json.loads(
        build_qa_author_prompt(
            plan,
            task,
            project_metadata={"qa": {"route_coverage_requirements": route_requirements}},
            project_root=tmp_path,
        )
    )

    assert payload["route_coverage_requirements"] == route_requirements
    assert payload["generated_route_coverage_artifact"]["requirements"] == route_requirements
    assert (
        payload["qa_context_capsule"]["generated_route_coverage_artifact"]["requirements"]
        == route_requirements
    )


def test_qa_author_prompt_endpoint_skeleton_requires_behavior_route_cases_with_anti_pattern(
    tmp_path: Path,
) -> None:
    plan = _plan().model_copy(
        update={
            "problem_statement": "Cover /api/diagnostics routes for equities and crypto.",
            "acceptance_test_matrix": [
                "Every existing /api/diagnostics/* route is exercised for equities and crypto."
            ],
        }
    )
    task = _task_contract().model_copy(
        update={"objective": "Write failing endpoint tests for /api/diagnostics routes."}
    )
    route_requirements = [
        {
            "api_prefix": "/api/diagnostics",
            "coverage_rule": "all_routes",
            "authoring_instruction": (
                "For all_routes, include each route literal or equivalent route tail token "
                "in generated tests and cover each domain/parameter named by acceptance."
            ),
            "required_routes": [
                (
                    "GET /api/diagnostics/overview "
                    "(app-api/src/main/java/example/DiagnosticsController.java)"
                ),
                (
                    "GET /api/diagnostics/services "
                    "(app-api/src/main/java/example/DiagnosticsController.java)"
                ),
            ],
        }
    ]

    payload = json.loads(
        build_qa_author_prompt(
            plan,
            task,
            project_metadata={
                "qa": {
                    "route_coverage_requirements": route_requirements,
                    "behavior_coverage_rules": [
                        (
                            "Every required route must appear in a route case that invokes "
                            "a controller/HTTP call."
                        )
                    ],
                }
            },
            project_root=tmp_path,
        )
    )

    assert payload["deterministic_test_skeleton"]["required_domains"] == [
        "equities",
        "crypto",
    ]
    assert payload["deterministic_test_skeleton"]["endpoint_behavior_skeleton"] == [
        {
            "api_prefix": "/api/diagnostics",
            "coverage_rule": "all_routes",
            "route_cases": [
                {
                    "method": "GET",
                    "path": "/api/diagnostics/overview",
                    "behavior_requirement": (
                        "Invoke the matching HTTP route through MockMvc, WebTestClient, "
                        "TestRestTemplate, or the project-approved HTTP harness for each "
                        "required domain and assert domain-specific identifiers/config/"
                        "partition/service state."
                    ),
                },
                {
                    "method": "GET",
                    "path": "/api/diagnostics/services",
                    "behavior_requirement": (
                        "Invoke the matching HTTP route through MockMvc, WebTestClient, "
                        "TestRestTemplate, or the project-approved HTTP harness for each "
                        "required domain and assert domain-specific identifiers/config/"
                        "partition/service state."
                    ),
                },
            ],
            "anti_pattern": (
                "Do not create a test that only asserts this route_cases list; each case "
                "must drive product behavior."
            ),
        }
    ]


def test_red_proof_verification_commands_add_module_local_gradle_test() -> None:
    task = _task_contract().model_copy(
        update={
            "verification_commands": [["./gradlew", "--no-daemon", "--console=plain", "test"]]
        }
    )

    assert red_proof_verification_commands(
        task,
        ["runtime-core/src/test/java/com/example/GraphYamlLoaderAcceptanceTest.java"],
        selected_command=["./gradlew", "--no-daemon", "--console=plain", "test"],
    ) == [
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":runtime-core:test",
            "--tests",
            "com.example.GraphYamlLoaderAcceptanceTest",
        ],
        ["./gradlew", "--no-daemon", "--console=plain", "test"],
    ]


def test_red_proof_verification_commands_add_root_gradle_test() -> None:
    task = _task_contract().model_copy(update={"verification_commands": [["./qa/smoke.sh"]]})

    assert red_proof_verification_commands(
        task,
        ["src/test/java/com/example/RootAcceptanceTest.java"],
        selected_command=["./qa/smoke.sh"],
    ) == [
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            "test",
            "--tests",
            "com.example.RootAcceptanceTest",
        ],
        ["./qa/smoke.sh"],
    ]


def test_red_proof_verification_commands_add_nested_gradle_module_test() -> None:
    task = _task_contract().model_copy(update={"verification_commands": [["./qa/smoke.sh"]]})

    assert red_proof_verification_commands(
        task,
        ["services/api/src/test/java/com/example/ApiAcceptanceTest.java"],
        selected_command=["./qa/smoke.sh"],
    ) == [
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":services:api:test",
            "--tests",
            "com.example.ApiAcceptanceTest",
        ],
        ["./qa/smoke.sh"],
    ]


def test_red_proof_verification_commands_add_kotlin_gradle_test() -> None:
    task = _task_contract().model_copy(update={"verification_commands": [["./qa/smoke.sh"]]})

    assert red_proof_verification_commands(
        task,
        [
            "app/src/test/kotlin/com/example/AppRouteTest.kt",
            "app/src/test/kotlin/com/example/AppRouteTest.kt",
        ],
        selected_command=["./qa/smoke.sh"],
    ) == [
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":app:test",
            "--tests",
            "com.example.AppRouteTest",
        ],
        ["./qa/smoke.sh"],
    ]


def test_configured_gate_matrix_coverage_is_deterministic(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/smoke.sh").write_text("./gradlew test\n", encoding="utf-8")
    plan = _plan().model_copy(
        update={
            "acceptance_test_matrix": [
                "Configured QA gate coverage: qa/smoke.sh passes.",
            ]
        }
    )
    contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="task-1",
        tests_added=["tests/test_feature.py"],
    )

    augmented = add_configured_gate_matrix_coverage(
        contract,
        plan=plan,
        worktree=tmp_path,
        project_metadata={
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["./qa/smoke.sh"],
                        "must_cover": ["unit_regression"],
                    }
                ]
            }
        },
    )

    assert augmented.matrix_coverage == {
        "Configured QA gate coverage: qa/smoke.sh passes.": ["./qa/smoke.sh"]
    }


def test_infer_tests_added_from_paths_keeps_real_tests_and_benchmarks() -> None:
    assert infer_tests_added_from_paths(
        [
            "benchmarks/build.gradle",
            "benchmarks/src/jmh/java/com/example/RangeScanVisitorBenchmark.java",
            "conformance-tests/src/test/java/com/example/RangeScanConformanceFixtures.java",
            "conformance-tests/src/test/java/com/example/RangeScanConformanceTest.java",
            "conformance-tests/src/test/java/com/example/RangeScanConsumerJourneyTest.java",
            "core/src/test/java/com/example/RangeScanApiTest.java",
        ]
    ) == [
        "benchmarks/src/jmh/java/com/example/RangeScanVisitorBenchmark.java",
        "conformance-tests/src/test/java/com/example/RangeScanConformanceTest.java",
        "conformance-tests/src/test/java/com/example/RangeScanConsumerJourneyTest.java",
        "core/src/test/java/com/example/RangeScanApiTest.java",
    ]


def test_gate_matrix_coverage_can_use_task_verification_commands(tmp_path: Path) -> None:
    plan = _plan().model_copy(
        update={
            "acceptance_test_matrix": [
                "Configured QA gates pass: ./qa/smoke.sh and Gradle test suite.",
            ]
        }
    )
    task = _task_contract().model_copy(
        update={
            "verification_commands": [
                ["./gradlew", "--no-daemon", "--console=plain", "test"],
                ["./qa/smoke.sh"],
            ]
        }
    )
    contract = QAAuthorContract(
        feature_id="feature-1",
        task_id="task-1",
        tests_added=["tests/test_feature.py"],
    )

    augmented = add_configured_gate_matrix_coverage(
        contract,
        plan=plan,
        worktree=tmp_path,
        project_metadata={},
        task_contract=task,
    )

    assert augmented.matrix_coverage == {
        "Configured QA gates pass: ./qa/smoke.sh and Gradle test suite.": [
            "./gradlew --no-daemon --console=plain test",
            "./qa/smoke.sh",
        ]
    }


def test_api_prefixes_preserve_full_versioned_route_prefix() -> None:
    assert api_prefixes_from_text("Cover /api/v1/orders and /api/v2/orders/{id}.") == [
        "/api/v1/orders",
        "/api/v2/orders/{id}",
    ]


def test_route_prefix_matching_uses_path_boundary() -> None:
    assert route_matches_prefix("GET /api/orders (OrdersController.java)", "/api/orders")
    assert route_matches_prefix("GET /api/orders/123 (OrdersController.java)", "/api/orders")
    assert not route_matches_prefix(
        "GET /api/orders-admin (OrdersAdminController.java)",
        "/api/orders",
    )


def test_repair_file_contents_truncates_large_generated_tests(tmp_path: Path) -> None:
    tmp_path.joinpath("tests").mkdir()
    tmp_path.joinpath("tests/test_large.py").write_text(
        "head\n" + ("x" * 40000) + "\ntail\n",
        encoding="utf-8",
    )

    contents = repair_file_contents(tmp_path, ["tests/test_large.py"])

    text = contents["tests/test_large.py"]
    assert len(text) <= 12000
    assert "head" in text
    assert "tail" in text
    assert "truncated" in text


def test_truncate_text_preserves_short_text() -> None:
    assert truncate_text("short", 100) == "short"


def test_isolate_codex_worktree_context_uses_add_dir_for_target_worktree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "target"
    context_root = tmp_path / "orchestrator"
    command = [
        "codex",
        "exec",
        "-m",
        "gpt-5.4",
        "-C",
        str(worktree),
        "--json",
        "-",
    ]

    isolated = isolate_codex_worktree_context(
        command,
        worktree=worktree,
        context_root=context_root,
        enabled=True,
    )

    assert isolated[isolated.index("-C") + 1] == str(context_root.resolve())
    assert isolated[isolated.index("--add-dir") + 1] == str(worktree.resolve())
    assert isolated.index("--add-dir") < isolated.index("-")


def _plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Add acceptance coverage.",
        design_contract=DesignContract(acceptance_tests=["acceptance criterion"]),
        affected_surfaces=["src/", "tests/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write failing acceptance tests.",
                allowed_paths=["tests/"],
                forbidden_paths=["src/private/"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[[sys.executable, "-m", "pytest", "tests", "-q"]],
            )
        ],
        acceptance_test_matrix=["acceptance criterion"],
    )


def _task_contract() -> TaskContract:
    return TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="qa",
        task_type="engineering.qa.author",
        objective="Write failing acceptance tests.",
        inputs={"task_id": "task-1", "task_slice_id": "qa-author"},
        allowed_paths=["tests/"],
        forbidden_paths=["src/private/"],
        expected_outputs=["QAAuthorContract"],
        verification_commands=[[sys.executable, "-m", "pytest", "tests", "-q"]],
    )
