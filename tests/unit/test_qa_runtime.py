from __future__ import annotations

import os
import sys
from pathlib import Path

from pgloom_engineering.qa_runtime import (
    canonical_red_proof,
    discover_route_inventory,
    hydrate_dependencies,
    is_generated_tool_artifact,
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


def test_no_tests_found_is_not_an_infra_error() -> None:
    assert verification_infra_error("collected 0 items\nno tests found", "") is None


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
