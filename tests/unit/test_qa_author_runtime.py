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
    normalize_qa_author_payload,
    path_violations,
    red_proof_verification_commands,
    route_matches_prefix,
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
        ["./gradlew", "--no-daemon", "--console=plain", "test"],
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":runtime-core:test",
            "--tests",
            "com.example.GraphYamlLoaderAcceptanceTest",
        ],
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
