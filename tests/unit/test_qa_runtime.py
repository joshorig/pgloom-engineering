from __future__ import annotations

import os
import sys
from pathlib import Path

from pgloom.harness.subprocess import SubprocessResult

from pgloom_engineering.qa_runtime import (
    QAVerificationResult,
    canonical_red_proof,
    discover_route_inventory,
    hydrate_dependencies,
    is_generated_tool_artifact,
    is_red_test_failure,
    prompt_safe_qa_metadata,
    qa_env,
    relevant_changed_files,
    route_inventory_for_prompt,
    run_qa_verification,
    validate_required_qa_gates,
    verification_infra_error,
)


def test_qa_env_uses_project_metadata_and_expands_path() -> None:
    env = qa_env(
        {
            "qa": {
                "env": {"JAVA_HOME": "/tmp/jdk"},
                "path_prepend": ["$JAVA_HOME/bin", "/custom/bin"],
            }
        }
    )

    assert env["JAVA_HOME"] == "/tmp/jdk"
    assert env["PATH"].split(os.pathsep)[:2] == ["/tmp/jdk/bin", "/custom/bin"]


def test_run_qa_verification_returns_canonical_red_proof(tmp_path: Path) -> None:
    tmp_path.joinpath("test_red.py").write_text("def test_red():\n    assert False\n")

    result = run_qa_verification(
        [sys.executable, "-m", "pytest", "test_red.py", "-q"],
        worktree=tmp_path,
        project_metadata={},
        timeout_seconds=30,
    )
    proof = canonical_red_proof(result)

    assert result.original.exit_code == 1
    assert result.infra_error is None
    assert proof[0]["source"] == "orchestrator"
    assert proof[0]["command"] == [sys.executable, "-m", "pytest", "test_red.py", "-q"]
    assert "assert False" in proof[0]["failure_excerpt"]


def test_run_qa_verification_classifies_missing_command_as_infra(tmp_path: Path) -> None:
    result = run_qa_verification(
        ["definitely-missing-command"],
        worktree=tmp_path,
        project_metadata={},
        timeout_seconds=30,
    )

    assert result.original.exit_code == 127
    assert result.infra_error == "no such file or directory"


def test_hydrate_dependencies_uses_project_metadata(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    source_root.joinpath("ui/node_modules").mkdir(parents=True)
    worktree.mkdir()

    hydrate_dependencies(
        source_root,
        worktree,
        {"qa": {"dependency_hydration": ["ui/node_modules"]}},
    )

    assert worktree.joinpath("ui/node_modules").is_symlink()


def test_discover_route_inventory_from_spring_annotations(tmp_path: Path) -> None:
    source = tmp_path / "app-api/src/main/java/example/WidgetsController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "class WidgetsController {",
                '  @GetMapping(value = "/api/widgets", produces = "application/json")',
                "  Object list() { return null; }",
                '  @PostMapping(value = "/api/widgets/promote")',
                "  Object promote() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/widgets"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/widgets (app-api/src/main/java/example/WidgetsController.java)",
        "POST /api/widgets/promote (app-api/src/main/java/example/WidgetsController.java)",
    ]


def test_discover_route_inventory_ignores_non_route_annotation_literals(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app-api/src/main/java/example/RuntimeController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                '@RequestMapping("/api/runtime")',
                "class RuntimeController {",
                '  @GetMapping(path = "/health", produces = "application/json")',
                "  Object health() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/runtime"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/runtime/health (app-api/src/main/java/example/RuntimeController.java)",
    ]


def test_discover_route_inventory_composes_spring_class_and_method_mappings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app-api/src/main/java/example/RuntimeController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                '@RequestMapping("/api/runtime")',
                "class RuntimeController {",
                '  @GetMapping("/health")',
                "  Object health() { return null; }",
                '  @PostMapping(path = "/restart")',
                "  Object restart() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/runtime"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/runtime/health (app-api/src/main/java/example/RuntimeController.java)",
        "POST /api/runtime/restart (app-api/src/main/java/example/RuntimeController.java)",
    ]


def test_discover_route_inventory_composes_relative_spring_method_mappings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app-api/src/main/java/example/RuntimeController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                '@RequestMapping("/api/runtime")',
                "class RuntimeController {",
                '  @GetMapping("health")',
                "  Object health() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/runtime"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/runtime/health (app-api/src/main/java/example/RuntimeController.java)",
    ]


def test_discover_route_inventory_handles_no_arg_spring_method_mappings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app-api/src/main/java/example/RuntimeController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                '@RequestMapping("/api/runtime")',
                "class RuntimeController {",
                "  @GetMapping",
                "  Object runtime() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/runtime"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/runtime (app-api/src/main/java/example/RuntimeController.java)",
    ]


def test_discover_route_inventory_clears_prefix_between_spring_controllers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app-api/src/main/java/example/Controllers.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                '@RequestMapping("/api/runtime")',
                "class RuntimeController {",
                "  @GetMapping",
                "  Object runtime() { return null; }",
                "}",
                "class InternalController {",
                '  @GetMapping("/internal")',
                "  Object internal() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    routes = discover_route_inventory(
        tmp_path,
        {"qa": {"endpoint_roots": ["app-api/src/main/java"]}},
        api_prefixes=["/api/runtime"],
    )

    assert route_inventory_for_prompt(routes) == [
        "GET /api/runtime (app-api/src/main/java/example/Controllers.java)",
    ]


def test_prompt_safe_qa_metadata_preserves_structured_required_gates() -> None:
    metadata = {
        "required_gates": [
            {
                "id": "smoke",
                "command": ["./qa/smoke.sh"],
                "must_cover": ["allocation", "benchmark_smoke"],
            }
        ]
    }

    assert prompt_safe_qa_metadata(metadata)["required_gates"] == metadata["required_gates"]


def test_validate_required_qa_gates_reports_configured_evidence(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/smoke.sh").write_text(
        "./gradlew :benchmarks:jmhSmokeCheck\n"
        "grep gc.alloc.rate.norm build/reports/jmh/results.txt\n",
        encoding="utf-8",
    )

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["./qa/smoke.sh"],
                        "must_cover": ["allocation", "benchmark_smoke"],
                    }
                ]
            }
        },
    )

    assert validation == [
        {
            "gate_id": "smoke",
            "command": ["./qa/smoke.sh"],
            "status": "configured",
            "evidence": [
                "command_file:qa/smoke.sh",
                "covers:allocation",
                "covers:benchmark_smoke",
            ],
            "missing": [],
        }
    ]


def test_validate_required_qa_gates_accepts_shell_wrapper_commands(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/smoke.sh").write_text(
        "./gradlew :benchmarks:jmhSmokeCheck\n",
        encoding="utf-8",
    )

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["bash", "qa/smoke.sh"],
                        "must_cover": ["benchmark_smoke"],
                    }
                ]
            }
        },
    )

    assert validation[0]["status"] == "configured"
    assert validation[0]["evidence"] == [
        "command_file:qa/smoke.sh",
        "covers:benchmark_smoke",
    ]


def test_validate_required_qa_gates_uses_command_args_for_coverage(tmp_path: Path) -> None:
    tmp_path.joinpath("gradlew").write_text("#!/bin/sh\n", encoding="utf-8")

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["./gradlew", ":benchmarks:jmhSmokeCheck"],
                        "must_cover": ["benchmark_smoke"],
                    }
                ]
            }
        },
    )

    assert validation[0]["status"] == "configured"
    assert validation[0]["evidence"] == [
        "command_file:gradlew",
        "covers:benchmark_smoke",
    ]


def test_required_qa_gates_reject_generic_benchmark_and_test_tokens(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/weak.sh").write_text(
        "echo run jmh later\n"
        "echo test placeholder\n",
        encoding="utf-8",
    )

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "regression",
                        "command": ["./qa/weak.sh"],
                        "must_cover": ["benchmark_full", "unit_regression"],
                    }
                ]
            }
        },
    )

    assert validation[0]["status"] == "missing"
    assert validation[0]["missing"] == [
        "coverage:benchmark_full",
        "coverage:unit_regression",
    ]


def test_required_qa_gates_do_not_accept_regression_filename_as_unit_gate(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/regression.sh").write_text(
        "echo only named regression\n",
        encoding="utf-8",
    )

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "regression",
                        "command": ["./qa/regression.sh"],
                        "must_cover": ["unit_regression"],
                    }
                ]
            }
        },
    )

    assert validation[0]["status"] == "missing"
    assert validation[0]["missing"] == ["coverage:unit_regression"]


def test_no_tests_found_is_not_an_infra_error() -> None:
    assert verification_infra_error("collected 0 items\nno tests found", "") is None


def test_file_not_found_assertion_is_not_an_infra_error() -> None:
    stderr = "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/input.json'"

    assert verification_infra_error("", stderr) is None


def test_missing_executable_is_an_infra_error() -> None:
    stderr = "env: definitely-missing-command: No such file or directory"

    assert verification_infra_error("", stderr) == "no such file or directory"


def test_pytest_no_collection_exit_is_not_red_test_failure() -> None:
    result = QAVerificationResult(
        original=SubprocessResult(
            argv=[sys.executable, "-m", "pytest", "tests", "-q"],
            exit_code=5,
            stdout="collected 0 items\nno tests collected\n",
            stderr="",
            duration_seconds=0.1,
            timed_out=False,
            killed=False,
        ),
        stdout_excerpt="collected 0 items\nno tests collected\n",
        stderr_excerpt="",
        infra_error=None,
    )

    assert not is_red_test_failure(result)


def test_pytest_assertion_failure_exit_is_red_test_failure() -> None:
    result = QAVerificationResult(
        original=SubprocessResult(
            argv=[sys.executable, "-m", "pytest", "tests", "-q"],
            exit_code=1,
            stdout="FAILED tests/test_feature.py::test_feature - AssertionError",
            stderr="",
            duration_seconds=0.1,
            timed_out=False,
            killed=False,
        ),
        stdout_excerpt="FAILED tests/test_feature.py::test_feature - AssertionError",
        stderr_excerpt="",
        infra_error=None,
    )

    assert is_red_test_failure(result)


def test_non_pytest_build_error_is_not_red_test_failure() -> None:
    result = QAVerificationResult(
        original=SubprocessResult(
            argv=["./qa/regression.sh"],
            exit_code=1,
            stdout="Compilation failed; cannot find symbol",
            stderr="",
            duration_seconds=0.1,
            timed_out=False,
            killed=False,
        ),
        stdout_excerpt="Compilation failed; cannot find symbol",
        stderr_excerpt="",
        infra_error=None,
    )

    assert not is_red_test_failure(result)


def test_non_pytest_test_failure_signal_is_red_test_failure() -> None:
    result = QAVerificationResult(
        original=SubprocessResult(
            argv=["./qa/regression.sh"],
            exit_code=1,
            stdout="There were failing tests. AssertionError: expected <1> but was <2>",
            stderr="",
            duration_seconds=0.1,
            timed_out=False,
            killed=False,
        ),
        stdout_excerpt="There were failing tests.",
        stderr_excerpt="",
        infra_error=None,
    )

    assert is_red_test_failure(result)


def test_relevant_changed_files_filters_generated_tool_artifacts() -> None:
    assert relevant_changed_files(
        [
            "tests/test_feature.py",
            "playwright-report/index.html",
            "ui/test-results/domain-switch/error-context.md",
            ".gradle/file-system.probe",
            "build/classes/java/test/Foo.class",
            "ui/tests/e2e/domain-switch.spec.ts",
        ]
    ) == ["tests/test_feature.py", "ui/tests/e2e/domain-switch.spec.ts"]
    assert is_generated_tool_artifact("test-results/.last-run.json")


def test_relevant_changed_files_filters_metadata_dependency_hydration_paths() -> None:
    assert relevant_changed_files(
        [
            "frontend/node_modules",
            "frontend/node_modules/.cache/tool/state.json",
            "tests/test_feature.py",
        ],
        {"qa": {"dependency_hydration": ["frontend/node_modules"]}},
    ) == ["tests/test_feature.py"]
