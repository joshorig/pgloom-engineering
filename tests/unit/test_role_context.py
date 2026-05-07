from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pgloom_engineering import role_context
from pgloom_engineering.contracts import DesignContract, PlanContract, TaskContract
from pgloom_engineering.planner import ProjectContext
from pgloom_engineering.planner.token_savior_context import TokenSaviorContextResult
from pgloom_engineering.projects import ProjectConfig
from pgloom_engineering.role_context import (
    build_role_context,
    record_role_context_usage,
    role_context_query,
)


def test_role_context_uses_token_savior_and_memory_when_enabled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = ProjectConfig(name="demo", root=tmp_path)
    plan = _plan()
    task = _task_contract()
    token_savior = TokenSaviorContextResult(
        context=ProjectContext(
            project_root=tmp_path,
            roadmap_excerpt="packed",
            decisions_excerpt="",
            relevant_paths=["src/"],
            qa_write_paths=["tests/"],
        ),
        input_tokens_original=1000,
        input_tokens_after_savior=100,
        tokens_saved=900,
        reduction_ratio=0.9,
        method="fake",
        packed_context="packed context",
    )
    monkeypatch.setattr(
        role_context,
        "get_settings",
        lambda: SimpleNamespace(
            role_context_token_savior_enabled=True,
            role_context_token_savior_budget_tokens=2500,
            role_context_memory_budget_tokens=800,
            role_context_capsule_cache_enabled=False,
            role_context_capsule_version="test",
        ),
    )
    monkeypatch.setattr(role_context, "build_memory_digest", lambda **kwargs: "memory")
    monkeypatch.setattr(
        role_context,
        "build_token_savior_project_context",
        lambda **kwargs: token_savior,
    )

    context = build_role_context(
        role="qa.author",
        project=project,
        plan=plan,
        task_contract=task,
        workflow_id="feature-1",
    )

    assert context.packed_context == "packed context"
    assert context.memory_digest == "memory"
    assert context.prompt_payload()["token_savior"]["tokens_saved"] == 900


def test_record_role_context_usage_is_best_effort(monkeypatch: Any) -> None:
    context = role_context.RoleContext(
        role="qa.author",
        query="query",
        packed_context="packed",
        token_savior=TokenSaviorContextResult(
            context=ProjectContext(project_root=Path(".")),
            input_tokens_original=100,
            input_tokens_after_savior=20,
            tokens_saved=80,
            reduction_ratio=0.8,
            method="fake",
            packed_context="packed",
        ),
    )
    monkeypatch.setattr(
        role_context,
        "record_token_savior_usage",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    assert (
        record_role_context_usage(
            context,
            feature_id="missing",
            workflow_id="missing",
            task_id="task-1",
            profile_name="qa-author",
            model_usage_id=1,
            database_url=None,
        )
        is None
    )


def test_role_context_query_includes_task_and_acceptance_scope() -> None:
    query = role_context_query(role="implementer", plan=_plan(), task_contract=_task_contract())

    assert "implementer" in query
    assert "Acceptance criterion" in query
    assert "Implement source" in query


def _plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Implement the feature.",
        design_contract=DesignContract(acceptance_tests=["Acceptance criterion"]),
        affected_surfaces=["src/"],
        task_slices=[],
        acceptance_test_matrix=["Acceptance criterion"],
    )


def _task_contract() -> TaskContract:
    return TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="implementer",
        task_type="engineering.implement",
        objective="Implement source.",
        allowed_paths=["src/"],
        forbidden_paths=["tests/"],
        expected_outputs=["TaskResultContract"],
    )
