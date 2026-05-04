from __future__ import annotations

import json
import sys
from pathlib import Path

from pgloom_engineering.contracts import (
    DesignContract,
    PlanContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.qa_author_runtime import (
    api_prefixes_from_text,
    build_qa_author_prompt,
    normalize_qa_author_payload,
    path_violations,
    route_matches_prefix,
    verification_command,
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
    assert path_violations(["src/feature.py", "tests/test_feature.py"], task) == [
        {"path": "src/feature.py", "reason": "outside_allowed_paths"}
    ]


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
