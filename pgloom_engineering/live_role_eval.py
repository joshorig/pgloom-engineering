from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pgloom.db.migrations import migrate as migrate_pgloom
from pgloom.db.postgres import connect
from pgloom.states import TaskState
from pgloom.tasks import enqueue_task, transition_task
from pgloom.workflows import create_workflow

from pgloom_engineering.contract_store import (
    create_plan_contract,
    record_handoff,
    upsert_task_contract,
)
from pgloom_engineering.contracts import (
    DesignContract,
    FeatureGoalContract,
    MilestoneContract,
    PlanContract,
    QAAuthorContract,
    TaskContract,
    TaskResultContract,
    TaskSliceContract,
)
from pgloom_engineering.db.migrations import migrate as migrate_engineering
from pgloom_engineering.features import attach_task, create_feature, get_feature_aggregate
from pgloom_engineering.planner.production_grade import evaluate_production_grade
from pgloom_engineering.projects import (
    ProjectConfig,
    get_project,
    import_projects_file,
    register_project,
)
from pgloom_engineering.worker import run_once
from pgloom_engineering.workflow_driver import _maybe_replan_blocked_feature

LIVE_ROLE_ORDER = [
    "planner",
    "qa-author",
    "implementer",
    "reviewer",
    "qa-scrutiny",
    "qa-usertest",
    "qa-to-end",
    "worker-orchestration",
    "orchestration",
]


@dataclass
class LiveRoleEvalResult:
    case_id: str
    role: str
    feature_id: str
    output_dir: Path
    status: str
    elapsed_seconds: float
    worker_results: list[dict[str, object]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    output_evidence: dict[str, Any] = field(default_factory=dict)
    production_grade_review: dict[str, Any] = field(default_factory=dict)
    aggregate: dict[str, Any] | None = None

    def asdict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "feature_id": self.feature_id,
            "output_dir": str(self.output_dir),
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "worker_results": self.worker_results,
            "checks": self.checks,
            "output_evidence": self.output_evidence,
            "production_grade_review": self.production_grade_review,
            "aggregate": self.aggregate,
        }


def run_live_role_eval(
    case: dict[str, Any],
    *,
    role: str,
    output_dir: Path,
    database_url: str | None = None,
    backend: str = "codex",
    model: str = "gpt-5.4",
    reasoning: str = "medium",
    max_steps: int = 20,
) -> LiveRoleEvalResult:
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    migrate_pgloom(database_url)
    migrate_engineering(database_url)
    case_id = str(case.get("id") or role)
    repo = _repo_for_case(case, role=role, output_dir=output_dir)
    feature_id = _seed_case(
        case=case,
        case_id=case_id,
        role=role,
        repo=repo,
        database_url=database_url,
        metadata=dict(case.get("project_metadata") or {}),
    )
    commands = _role_commands(
        backend=backend,
        model=str(case.get("model") or model),
        reasoning=str(case.get("reasoning") or reasoning),
        role_routes=case.get("role_routes") if isinstance(case.get("role_routes"), dict) else None,
    )
    worker_results: list[dict[str, object]] = []
    slots = _slots_for_case(case, role)
    with _patched_env(commands):
        for index in range(max_steps):
            aggregate = get_feature_aggregate(feature_id, database_url=database_url)
            if aggregate is not None:
                replan = _maybe_replan_blocked_feature(feature_id, aggregate, database_url)
                if replan is not None:
                    worker_results.append(replan)
                    continue
            progressed = False
            for slot in slots:
                worker_result = run_once(
                    slot=slot,
                    worker_id=f"live-role-eval-{case_id}-{slot}-{index}",
                    database_url=database_url,
                    feature_id=feature_id,
                )
                if worker_result.get("claimed"):
                    progressed = True
                    worker_results.append(worker_result)
            if not progressed:
                break
    aggregate = get_feature_aggregate(feature_id, database_url=database_url)
    if aggregate is None:
        raise RuntimeError(f"feature aggregate missing for live eval: {feature_id}")
    output_evidence = _collect_output_evidence(
        repo=_evidence_repo(repo, aggregate),
        output_dir=output_dir,
        aggregate=aggregate,
    )
    production_grade_review = _production_grade_review(
        aggregate=aggregate,
        output_evidence=output_evidence,
    )
    checks = _score(
        role=role,
        aggregate=aggregate,
        output_evidence=output_evidence,
        enabled_roles=_enabled_roles(case),
    )
    status = "pass" if all(item["passed"] for item in checks) else "fail"
    eval_result = LiveRoleEvalResult(
        case_id=case_id,
        role=role,
        feature_id=feature_id,
        output_dir=output_dir,
        status=status,
        elapsed_seconds=round(time.monotonic() - started, 3),
        worker_results=worker_results,
        checks=checks,
        output_evidence=output_evidence,
        production_grade_review=production_grade_review,
        aggregate=aggregate,
    )
    _write_result(eval_result)
    return eval_result


def selected_cases(
    suite: dict[str, Any],
    roles: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selected = set(roles or [])
    cases: list[dict[str, Any]] = []
    for case in suite.get("cases", []):
        if not isinstance(case, dict):
            continue
        role = str(case.get("role") or "")
        if selected and role not in selected:
            continue
        cases.append(case)
    return cases


def dry_run_result(
    case: dict[str, Any],
    *,
    output_dir: Path,
    backend: str,
    model: str,
    reasoning: str,
) -> dict[str, Any]:
    role = str(case["role"])
    return {
        "case_id": case.get("id"),
        "role": role,
        "output_dir": str(output_dir),
        "status": "dry_run",
        "slots": _slots_for_case(case, role),
        "model_commands": _role_commands(
            backend=backend,
            model=str(case.get("model") or model),
            reasoning=str(case.get("reasoning") or reasoning),
            role_routes=case.get("role_routes")
            if isinstance(case.get("role_routes"), dict)
            else None,
        ),
    }


def _repo_for_case(case: dict[str, Any], *, role: str, output_dir: Path) -> Path:
    project_root = case.get("project_root")
    if isinstance(project_root, str) and project_root:
        return Path(project_root)
    workspace = output_dir / "workspace"
    repo = workspace / "repo"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    return _fixture_repo(repo, include_test=role != "orchestration")


def _feature_goal_for_case(
    case: dict[str, Any],
    *,
    fallback_project: str,
) -> FeatureGoalContract:
    raw_path = case.get("feature_goal")
    if isinstance(raw_path, str) and raw_path:
        return FeatureGoalContract.model_validate(
            json.loads(Path(raw_path).read_text(encoding="utf-8"))
        )
    raw_contract = case.get("feature_goal_contract")
    if isinstance(raw_contract, dict):
        return FeatureGoalContract.model_validate(raw_contract)
    return FeatureGoalContract(
        project=fallback_project,
        goal="Implement src.calc.increment so it returns the input integer plus one.",
        requirements=[
            "Keep the implementation scoped to src/calc.py.",
            "Add or preserve focused pytest coverage under tests/.",
            "Verification must run python -m pytest tests/test_increment.py -q.",
        ],
        acceptance_criteria=[
            "increment(1) returns 2.",
            "The focused pytest command passes.",
        ],
    )


def _seed_case(
    *,
    case: dict[str, Any],
    case_id: str,
    role: str,
    repo: Path,
    database_url: str | None,
    metadata: dict[str, Any],
) -> str:
    if role == "orchestration":
        return _seed_full_orchestration_case(
            case=case,
            case_id=case_id,
            repo=repo,
            database_url=database_url,
            metadata=metadata,
        )
    if role == "qa-to-end":
        return _seed_post_plan_case(
            case=case,
            case_id=case_id,
            repo=repo,
            database_url=database_url,
            metadata=metadata,
        )
    project_name = f"live-eval-{case_id}"
    metadata = {
        "role_gates": {
            "planner": "enabled",
            "qa": "enabled",
            "implementer": "enabled",
            "reviewer": "enabled",
        },
        "relevant_paths": ["src/", "tests/"],
        "qa": {"allowed_test_roots": ["tests/"]},
        **metadata,
    }
    register_project(
        ProjectConfig(
            name=project_name,
            root=repo,
            base_branch="main",
            metadata=metadata,
        ),
        replace=True,
        database_url=database_url,
    )
    workflow = create_workflow(
        domain="engineering",
        name=f"live-role-eval-{case_id}",
        database_url=database_url,
    )
    create_feature(
        workflow_id=workflow["id"],
        project=project_name,
        branch="main",
        metadata={"live_eval_case": case_id, "live_eval_role": role},
        database_url=database_url,
    )
    plan_row = create_plan_contract(
        _plan_contract(feature_id=str(workflow["id"]), project=project_name),
        database_url=database_url,
    )
    task_ids: dict[str, str] = {}
    if role in {"implementer", "worker-orchestration"}:
        task_ids["qa-author"] = _seed_qa_author_output(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan_row["id"]),
            repo=repo,
            database_url=database_url,
        )
        task_ids["implementer"] = _enqueue_task(
            workflow_id=str(workflow["id"]),
            role="implementer",
            task_type="engineering.implement",
            slot="implementer",
            plan_id=str(plan_row["id"]),
            dependencies=[task_ids["qa-author"]],
            verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
            database_url=database_url,
        )
    if role in {"reviewer", "worker-orchestration"}:
        impl_id = task_ids.get("implementer") or _seed_task_result_output(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan_row["id"]),
            repo=repo,
            database_url=database_url,
        )
        task_ids["reviewer"] = _enqueue_task(
            workflow_id=str(workflow["id"]),
            role="reviewer",
            task_type="engineering.review",
            slot="reviewer",
            plan_id=str(plan_row["id"]),
            dependencies=[impl_id],
            database_url=database_url,
        )
        if impl_id:
            _record_task_result_handoff_to_reviewer(
                workflow_id=str(workflow["id"]),
                impl_id=impl_id,
                reviewer_id=task_ids["reviewer"],
                database_url=database_url,
            )
    if role in {"qa-scrutiny", "qa-usertest", "worker-orchestration"}:
        impl_id = task_ids.get("implementer") or _seed_task_result_output(
            workflow_id=str(workflow["id"]),
            plan_id=str(plan_row["id"]),
            repo=repo,
            database_url=database_url,
        )
        validator = "scrutiny" if role != "qa-usertest" else "usertest"
        if role == "worker-orchestration":
            _enqueue_validator(
                workflow_id=str(workflow["id"]),
                plan_id=str(plan_row["id"]),
                impl_id=impl_id,
                validator="scrutiny",
                database_url=database_url,
            )
            _enqueue_validator(
                workflow_id=str(workflow["id"]),
                plan_id=str(plan_row["id"]),
                impl_id=impl_id,
                validator="usertest",
                database_url=database_url,
            )
        else:
            _enqueue_validator(
                workflow_id=str(workflow["id"]),
                plan_id=str(plan_row["id"]),
                impl_id=impl_id,
                validator=validator,
                database_url=database_url,
            )
    return str(workflow["id"])


def _seed_full_orchestration_case(
    *,
    case: dict[str, Any],
    case_id: str,
    repo: Path,
    database_url: str | None,
    metadata: dict[str, Any],
) -> str:
    feature_goal = _feature_goal_for_case(case, fallback_project=f"live-eval-{case_id}")
    project_name = feature_goal.project
    projects_file = case.get("projects_file")
    if isinstance(projects_file, str) and projects_file:
        import_projects_file(Path(projects_file), replace=True, database_url=database_url)
    existing_project = get_project(project_name, database_url=database_url)
    metadata = {
        "role_gates": {
            "planner": "enabled",
            "qa": "enabled",
            "implementer": "enabled",
            "reviewer": "enabled",
        },
        "roadmap_excerpt": (
            "Small Python utility package. Source lives in src/, tests in tests/. "
            "Use pytest for focused verification."
        ),
        "relevant_paths": ["src/", "tests/"],
        "qa_write_paths": ["tests/"],
        "qa": {
            "allowed_test_roots": ["tests/"],
            "test_roots": ["tests/"],
            "source_roots": ["src/"],
            "example_tests": ["tests/"],
        },
        **(existing_project.metadata if existing_project is not None else {}),
        **metadata,
    }
    project = existing_project or register_project(
        ProjectConfig(name=project_name, root=repo, base_branch="main", metadata=metadata),
        replace=True,
        database_url=database_url,
    )
    workflow = create_workflow(
        domain="engineering",
        name=f"live-role-eval-{case_id}",
        database_url=database_url,
    )
    create_feature(
        workflow_id=workflow["id"],
        project=project_name,
        branch="main",
        metadata={
            "live_eval_case": case_id,
            "live_eval_role": "orchestration",
            "feature_goal_contract": feature_goal.model_dump(mode="json"),
        },
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=str(workflow["id"]),
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload={
            "database_url": database_url,
            "feature_id": str(workflow["id"]),
            "feature_goal_contract": feature_goal.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
        },
        database_url=database_url,
    )
    attach_task(str(workflow["id"]), str(planner["id"]), role="planner", database_url=database_url)
    return str(workflow["id"])


def _seed_post_plan_case(
    *,
    case: dict[str, Any],
    case_id: str,
    repo: Path,
    database_url: str | None,
    metadata: dict[str, Any],
) -> str:
    feature_goal = _feature_goal_for_case(case, fallback_project=f"live-eval-{case_id}")
    project_name = feature_goal.project
    projects_file = case.get("projects_file")
    if isinstance(projects_file, str) and projects_file:
        import_projects_file(Path(projects_file), replace=True, database_url=database_url)
    existing_project = get_project(project_name, database_url=database_url)
    if existing_project is None:
        project = register_project(
            ProjectConfig(name=project_name, root=repo, base_branch="main", metadata=metadata),
            replace=True,
            database_url=database_url,
        )
    else:
        project = existing_project
    workflow = create_workflow(
        domain="engineering",
        name=f"live-role-eval-{case_id}",
        database_url=database_url,
    )
    create_feature(
        workflow_id=workflow["id"],
        project=project_name,
        branch="main",
        metadata={
            "live_eval_case": case_id,
            "live_eval_role": "qa-to-end",
            "feature_goal_contract": feature_goal.model_dump(mode="json"),
        },
        database_url=database_url,
    )
    plan = _plan_from_case(case).model_copy(
        update={"feature_id": str(workflow["id"]), "project": project_name}
    )
    plan_row = create_plan_contract(plan, database_url=database_url)
    if plan_row["status"] != "valid":
        raise RuntimeError(f"seed plan is invalid: {plan_row['validation_errors']}")
    _decompose_seed_plan(
        plan=plan,
        plan_row=plan_row,
        project=project,
        workflow_id=str(workflow["id"]),
        database_url=database_url,
    )
    return str(workflow["id"])


def _plan_from_case(case: dict[str, Any]) -> PlanContract:
    if isinstance(case.get("plan_contract"), dict):
        return PlanContract.model_validate(case["plan_contract"])
    outcome_path = case.get("from_plan_outcome")
    if isinstance(outcome_path, str) and outcome_path:
        outcome = json.loads(Path(outcome_path).read_text(encoding="utf-8"))
        contract = (
            outcome.get("aggregate", {})
            .get("active_plan_contract", {})
            .get("contract")
        )
        if isinstance(contract, dict):
            return PlanContract.model_validate(contract)
    raise ValueError("qa-to-end case requires plan_contract or from_plan_outcome")


def _decompose_seed_plan(
    *,
    plan: PlanContract,
    plan_row: dict[str, Any],
    project: ProjectConfig,
    workflow_id: str,
    database_url: str | None,
) -> None:
    created: dict[str, str] = {}
    for task_slice in plan.task_slices:
        depends_on = [created[dep] for dep in task_slice.depends_on if dep in created]
        task = enqueue_task(
            workflow_id=workflow_id,
            domain="engineering",
            task_type=task_slice.task_type,
            slot=_slot_for_task_type(task_slice.task_type),
            payload={
                "feature_id": workflow_id,
                "plan_contract_id": plan_row["id"],
                "plan_contract_hash": plan_row["contract_hash"],
                "task_slice_id": task_slice.slice_id,
                "milestone_id": task_slice.milestone_id,
                "project": project.model_dump(mode="json"),
                "allow_unregistered_project": False,
                "requires_multi_agent_review": True,
            },
            depends_on=depends_on,
            database_url=database_url,
        )
        task_id = str(task["id"])
        created[task_slice.slice_id] = task_id
        attach_task(workflow_id, task_id, role=task_slice.role, database_url=database_url)
        task_contract = TaskContract(
            feature_id=workflow_id,
            plan_contract_id=str(plan_row["id"]),
            role=task_slice.role,
            task_type=task_slice.task_type,
            objective=task_slice.objective,
            inputs={
                "plan_contract_id": plan_row["id"],
                "task_id": task_id,
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
            verification_commands=task_slice.verification_commands,
            required_procedures=task_slice.required_procedures,
            handoff_requirements=["produce TaskResultContract"],
        )
        status = "completed" if task_slice.task_type == "engineering.design" else "active"
        upsert_task_contract(
            task_id,
            task_contract,
            output_contract={"seeded": "design-complete"} if status == "completed" else None,
            status=status,
            database_url=database_url,
        )
        record_handoff(
            feature_id=workflow_id,
            from_task_id=None,
            to_task_id=task_id,
            handoff_type="plan_to_task",
            contract=task_contract.model_dump(mode="json"),
            database_url=database_url,
        )
        if task_slice.task_type == "engineering.design":
            transition_task(task_id, TaskState.DONE, database_url=database_url)


def _enqueue_validator(
    *,
    workflow_id: str,
    plan_id: str,
    impl_id: str,
    validator: str,
    database_url: str | None,
) -> str:
    task_type = f"engineering.qa.verify.{validator}"
    command = ["python", "-m", "pytest", "tests/test_increment.py", "-q"]
    if validator == "usertest":
        command = ["python", "-c", "from src.calc import increment; assert increment(1) == 2"]
    return _enqueue_task(
        workflow_id=workflow_id,
        role="qa",
        task_type=task_type,
        slot=f"qa-{validator}",
        plan_id=plan_id,
        dependencies=[impl_id],
        verification_commands=[command],
        expected_outputs=["QAResultContract"],
        database_url=database_url,
    )


def _enqueue_task(
    *,
    workflow_id: str,
    role: str,
    task_type: str,
    slot: str,
    plan_id: str,
    dependencies: list[str],
    database_url: str | None,
    verification_commands: list[list[str]] | None = None,
    expected_outputs: list[str] | None = None,
) -> str:
    task = enqueue_task(
        workflow_id=workflow_id,
        domain="engineering",
        task_type=task_type,
        slot=slot,
        payload={"feature_id": workflow_id, "database_url": database_url},
        # Contract dependencies preserve role context. The eval driver controls
        # slot order directly so dependent role tasks remain claimable after
        # seeded predecessor tasks have already completed.
        depends_on=[],
        database_url=database_url,
    )
    contract = TaskContract(
        feature_id=workflow_id,
        plan_contract_id=plan_id,
        role=role,
        task_type=task_type,
        objective=f"Live eval {task_type}.",
        inputs={
            "task_id": str(task["id"]),
            "task_slice_id": _slice_id_for_task_type(task_type),
            "milestone_id": "m1",
        },
        allowed_paths=["src/", "tests/"],
        forbidden_paths=[".git/"],
        dependencies=dependencies,
        expected_outputs=expected_outputs or ["TaskResultContract"],
        verification_commands=verification_commands or [],
        required_procedures=["inspect_contract", "run_focused_verification"],
    )
    upsert_task_contract(str(task["id"]), contract, database_url=database_url)
    attach_task(workflow_id, str(task["id"]), role=role, database_url=database_url)
    return str(task["id"])


def _seed_qa_author_output(
    *,
    workflow_id: str,
    plan_id: str,
    repo: Path,
    database_url: str | None,
) -> str:
    task_id = _enqueue_task(
        workflow_id=workflow_id,
        role="qa",
        task_type="engineering.qa.author",
        slot="qa-engineer",
        plan_id=plan_id,
        dependencies=[],
        expected_outputs=["QAAuthorContract"],
        database_url=database_url,
    )
    contract = QAAuthorContract(
        feature_id=workflow_id,
        task_id=task_id,
        tests_added=["tests/test_increment.py::test_increment_adds_one"],
        matrix_coverage={"increment returns n + 1": ["tests/test_increment.py"]},
        red_proof=[
            {
                "test": "tests/test_increment.py::test_increment_adds_one",
                "command": ["python", "-m", "pytest", "tests/test_increment.py", "-q"],
                "exit_code": 1,
                "output_excerpt": "assert 1 == 2",
            }
        ],
        paths_touched=["tests/test_increment.py"],
        branch="main",
        worktree_path=str(repo),
    )
    row = upsert_task_contract(
        task_id,
        TaskContract.model_validate(get_task_contract_payload(task_id, database_url)),
        output_contract={"qa_author_contract": contract.model_dump(mode="json")},
        status="completed",
        database_url=database_url,
    )
    del row
    transition_task(task_id, TaskState.DONE, database_url=database_url)
    record_handoff(
        feature_id=workflow_id,
        from_task_id=task_id,
        to_task_id=None,
        handoff_type="qa_author_contract",
        contract={"qa_author_contract": contract.model_dump(mode="json")},
        database_url=database_url,
    )
    return task_id


def _seed_task_result_output(
    *,
    workflow_id: str,
    plan_id: str,
    repo: Path,
    database_url: str | None,
) -> str:
    _make_increment_green(repo)
    task_id = _enqueue_task(
        workflow_id=workflow_id,
        role="implementer",
        task_type="engineering.implement",
        slot="implementer",
        plan_id=plan_id,
        dependencies=[],
        verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
        database_url=database_url,
    )
    contract = TaskResultContract(
        feature_id=workflow_id,
        task_id=task_id,
        changed_files=["src/calc.py"],
        branch="main",
        worktree_path=str(repo),
        checks=[
            {
                "command": ["python", "-m", "pytest", "tests/test_increment.py", "-q"],
                "exit_code": 0,
                "status": "passed",
            }
        ],
    )
    upsert_task_contract(
        task_id,
        TaskContract.model_validate(get_task_contract_payload(task_id, database_url)),
        output_contract=contract.model_dump(mode="json"),
        status="completed",
        database_url=database_url,
    )
    record_handoff(
        feature_id=workflow_id,
        from_task_id=task_id,
        to_task_id=None,
        handoff_type="task_result",
        contract=contract.model_dump(mode="json"),
        database_url=database_url,
    )
    transition_task(task_id, TaskState.DONE, database_url=database_url)
    return task_id


def _record_task_result_handoff_to_reviewer(
    *,
    workflow_id: str,
    impl_id: str,
    reviewer_id: str,
    database_url: str | None,
) -> None:
    with connect(database_url) as conn:
        row = conn.execute(
            "select output_contract from engineering_task_contracts where task_id = %s",
            (impl_id,),
        ).fetchone()
    if row is None or not isinstance(row["output_contract"], dict):
        return
    record_handoff(
        feature_id=workflow_id,
        from_task_id=impl_id,
        to_task_id=reviewer_id,
        handoff_type="task_result",
        contract=dict(row["output_contract"]),
        database_url=database_url,
    )


def get_task_contract_payload(task_id: str, database_url: str | None) -> dict[str, Any]:
    with connect(database_url) as conn:
        row = conn.execute(
            "select input_contract from engineering_task_contracts where task_id = %s",
            (task_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"missing task contract: {task_id}")
    return dict(row["input_contract"])


def _plan_contract(*, feature_id: str, project: str) -> PlanContract:
    slices = [
        TaskSliceContract(
            slice_id="qa-author",
            role="qa",
            task_type="engineering.qa.author",
            objective="Author acceptance test for increment behavior.",
            allowed_paths=["tests/"],
            forbidden_paths=["src/"],
            expected_outputs=["QAAuthorContract"],
            verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
            acceptance_assertion_ids=["increment returns n + 1"],
            milestone_id="m1",
        ),
        TaskSliceContract(
            slice_id="implementer",
            role="implementer",
            task_type="engineering.implement",
            objective="Implement increment behavior.",
            allowed_paths=["src/"],
            forbidden_paths=["tests/"],
            depends_on=["qa-author"],
            expected_outputs=["TaskResultContract"],
            verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
            acceptance_assertion_ids=["increment returns n + 1"],
            milestone_id="m1",
        ),
        TaskSliceContract(
            slice_id="reviewer",
            role="reviewer",
            task_type="engineering.review",
            objective="Review increment implementation.",
            allowed_paths=["src/", "tests/"],
            forbidden_paths=[".git/"],
            depends_on=["implementer"],
            expected_outputs=["ReviewVerdictContract"],
            verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
            acceptance_assertion_ids=["increment returns n + 1"],
            milestone_id="m1",
        ),
        TaskSliceContract(
            slice_id="qa-scrutiny",
            role="qa",
            task_type="engineering.qa.verify.scrutiny",
            objective="Run focused tests.",
            allowed_paths=["tests/"],
            forbidden_paths=[".git/"],
            depends_on=["reviewer"],
            expected_outputs=["QAResultContract"],
            verification_commands=[["python", "-m", "pytest", "tests/test_increment.py", "-q"]],
            acceptance_assertion_ids=["increment returns n + 1"],
            milestone_id="m1",
        ),
        TaskSliceContract(
            slice_id="qa-usertest",
            role="qa",
            task_type="engineering.qa.verify.usertest",
            objective="Exercise the package through a CLI-style user path.",
            allowed_paths=["tests/"],
            forbidden_paths=[".git/"],
            depends_on=["qa-scrutiny"],
            expected_outputs=["QAResultContract"],
            verification_commands=[
                ["python", "-c", "from src.calc import increment; assert increment(1) == 2"]
            ],
            acceptance_assertion_ids=["increment returns n + 1"],
            milestone_id="m1",
        ),
    ]
    return PlanContract(
        feature_id=feature_id,
        project=project,
        problem_statement="Live eval autonomous role execution on a repeatable fixture.",
        design_contract=DesignContract(
            public_api="src.calc.increment",
            ownership_boundaries="Only src/ production code and tests/ validation files.",
            acceptance_tests=["increment returns n + 1"],
        ),
        affected_surfaces=["src/", "tests/"],
        task_slices=slices,
        acceptance_test_matrix=["increment returns n + 1"],
        acceptance_assertions=["increment returns n + 1"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Increment behavior",
                slice_ids=[item.slice_id for item in slices],
                acceptance_assertions=["increment returns n + 1"],
                validation_contract={"scrutiny": True, "usertest": True},
            )
        ],
    )


def _fixture_repo(repo: Path, *, include_test: bool = True) -> Path:
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/__init__.py").write_text("", encoding="utf-8")
    (repo / "src/calc.py").write_text(
        "def increment(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    if include_test:
        (repo / "tests/test_increment.py").write_text(
            "\n".join(
                [
                    "from src.calc import increment",
                    "",
                    "",
                    "def test_increment_adds_one():",
                    "    assert increment(1) == 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "eval",
            "GIT_AUTHOR_EMAIL": "eval@example.test",
            "GIT_COMMITTER_NAME": "eval",
            "GIT_COMMITTER_EMAIL": "eval@example.test",
        },
    )
    return repo


def _make_increment_green(repo: Path) -> None:
    (repo / "src/calc.py").write_text(
        "def increment(value: int) -> int:\n    return value + 1\n",
        encoding="utf-8",
    )


def _slots_for_role(role: str) -> list[str]:
    if role == "planner":
        return ["planner"]
    if role == "qa-author":
        return ["qa-engineer"]
    if role == "implementer":
        return ["implementer"]
    if role == "reviewer":
        return ["reviewer"]
    if role == "qa-scrutiny":
        return ["qa-scrutiny"]
    if role == "qa-usertest":
        return ["qa-usertest"]
    if role == "worker-orchestration":
        return ["implementer", "reviewer", "qa-scrutiny", "qa-usertest"]
    if role == "qa-to-end":
        return ["qa-engineer", "implementer", "reviewer", "qa-scrutiny", "qa-usertest", "planner"]
    if role == "orchestration":
        return [
            "planner",
            "designer",
            "qa-engineer",
            "implementer",
            "reviewer",
            "qa-scrutiny",
            "qa-usertest",
        ]
    raise ValueError(f"unsupported live role: {role}")


def _slots_for_case(case: dict[str, Any], role: str) -> list[str]:
    enabled = _enabled_roles(case)
    if not enabled:
        return _slots_for_role(role)
    slots = [_slot_for_enabled_role(item) for item in enabled]
    return list(dict.fromkeys(slots))


def _enabled_roles(case: dict[str, Any]) -> list[str]:
    raw = case.get("enabled_roles")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def _slot_for_enabled_role(role: str) -> str:
    normalized = role.replace("-", "_")
    return {
        "planner": "planner",
        "design": "designer",
        "designer": "designer",
        "qa": "qa-engineer",
        "qa_author": "qa-engineer",
        "qa_engineer": "qa-engineer",
        "implementer": "implementer",
        "implementation": "implementer",
        "reviewer": "reviewer",
        "review": "reviewer",
        "qa_scrutiny": "qa-scrutiny",
        "scrutiny": "qa-scrutiny",
        "qa_usertest": "qa-usertest",
        "usertest": "qa-usertest",
        "recovery": "planner",
    }[normalized]
    raise ValueError(f"unsupported live eval role: {role}")


def _role_commands(
    *,
    backend: str,
    model: str,
    reasoning: str,
    role_routes: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    routes = role_routes or {}

    def route(role: str, *, planner: bool = False) -> list[str]:
        route_config: dict[str, Any] = {}
        raw_route = routes.get(role)
        if isinstance(raw_route, dict):
            route_config = raw_route
        role_backend = str(route_config.get("backend") or backend)
        role_model = str(route_config.get("model") or model)
        role_reasoning = str(route_config.get("reasoning") or reasoning)
        return _role_command(
            backend=role_backend,
            model=role_model,
            reasoning=role_reasoning,
            planner=planner,
        )

    return {
        "planner": route("planner", planner=True),
        "implementer": route("implementer"),
        "reviewer": route("reviewer"),
        "qa_author": route("qa_author"),
        "qa_validation": route("qa_validation"),
    }


def _role_command(*, backend: str, model: str, reasoning: str, planner: bool) -> list[str]:
    if backend == "claude":
        return ["claude", "-p", "--model", model, "--output-format", "json"]
    elif backend == "codex":
        command = [
            "codex",
            "--ask-for-approval",
            "never",
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning}"',
            "-s",
            "danger-full-access",
            "--ephemeral",
            "--json",
            "-",
        ]
        if not planner:
            command[8:8] = ["-C", "{worktree}"]
        return command
    else:
        raise ValueError(f"unsupported backend: {backend}")


@contextmanager
def _patched_env(commands: dict[str, list[str]]) -> Any:
    role_context_root = Path(".local/role-context-root")
    role_context_root.mkdir(parents=True, exist_ok=True)
    planner_model = _model_from_command(commands["planner"]) or "gpt-5.4"
    planner_reasoning = _reasoning_from_command(commands["planner"]) or "medium"
    implementer_model = _model_from_command(commands["implementer"]) or "gpt-5.4"
    reviewer_model = _model_from_command(commands["reviewer"]) or "gpt-5.4"
    qa_author_model = _model_from_command(commands["qa_author"]) or "gpt-5.4"
    qa_validation_command = commands.get("qa_validation", commands["qa_author"])
    qa_validation_model = _model_from_command(qa_validation_command) or "gpt-5.4"
    patch = {
        "PGLOOM_ENGINEERING_PLANNER_COMMAND": json.dumps(commands["planner"]),
        "PGLOOM_ENGINEERING_PLANNER_CLAUDE_PANELIST_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CLAUDE_CONSOLIDATOR_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CLAUDE_CRITIC_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_PANELIST_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_CONSOLIDATOR_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_CRITIC_MODEL": planner_model,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_PANELIST_REASONING": planner_reasoning,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_CONSOLIDATOR_REASONING": planner_reasoning,
        "PGLOOM_ENGINEERING_PLANNER_CODEX_CRITIC_REASONING": planner_reasoning,
        "PGLOOM_ENGINEERING_PLANNER_PANELIST_COUNT": "2",
        "PGLOOM_ENGINEERING_PLANNER_ITER_1_PANELIST_COUNT": "2",
        "PGLOOM_ENGINEERING_PLANNER_ITER_2_PANELIST_COUNT": "1",
        "PGLOOM_ENGINEERING_PLANNER_MAX_ITERATIONS": "2",
        "PGLOOM_ENGINEERING_PLANNER_INVOCATION_TIMEOUT_SECONDS": "1200",
        "PGLOOM_ENGINEERING_IMPLEMENTER_COMMAND": json.dumps(commands["implementer"]),
        "PGLOOM_ENGINEERING_IMPLEMENTER_CODEX_MODEL": implementer_model,
        "PGLOOM_ENGINEERING_IMPLEMENTER_INVOCATION_TIMEOUT_SECONDS": "1200",
        "PGLOOM_ENGINEERING_REVIEWER_COMMAND": json.dumps(commands["reviewer"]),
        "PGLOOM_ENGINEERING_REVIEWER_CODEX_MODEL": reviewer_model,
        "PGLOOM_ENGINEERING_REVIEWER_INVOCATION_TIMEOUT_SECONDS": "1200",
        "PGLOOM_ENGINEERING_QA_AUTHOR_COMMAND": json.dumps(commands["qa_author"]),
        "PGLOOM_ENGINEERING_QA_AUTHOR_CODEX_MODEL": qa_author_model,
        "PGLOOM_ENGINEERING_QA_AUTHOR_INVOCATION_TIMEOUT_SECONDS": "1200",
        "PGLOOM_ENGINEERING_QA_VALIDATION_COMMAND": json.dumps(qa_validation_command),
        "PGLOOM_ENGINEERING_QA_VALIDATION_CODEX_MODEL": qa_validation_model,
        "PGLOOM_ENGINEERING_QA_VALIDATION_INVOCATION_TIMEOUT_SECONDS": "1200",
        "PGLOOM_ENGINEERING_ROLE_CONTEXT_TOKEN_SAVIOR_ENABLED": "true",
        "PGLOOM_ENGINEERING_ROLE_MODEL_CONTEXT_ISOLATION_ENABLED": "false",
        "PGLOOM_ENGINEERING_QA_AUTHOR_MODEL_CONTEXT_ISOLATION_ENABLED": "false",
        "PGLOOM_ENGINEERING_QA_AUTHOR_MODEL_CONTEXT_ADD_DIR_ENABLED": "false",
        "PGLOOM_ENGINEERING_QA_VALIDATION_MODEL_CONTEXT_ISOLATION_ENABLED": "false",
        "PGLOOM_ENGINEERING_QA_VALIDATION_MODEL_CONTEXT_ADD_DIR_ENABLED": "false",
        "PGLOOM_ENGINEERING_IMPLEMENTER_MODEL_CONTEXT_ISOLATION_ENABLED": "true",
        "PGLOOM_ENGINEERING_IMPLEMENTER_MODEL_CONTEXT_ADD_DIR_ENABLED": "false",
        "PGLOOM_ENGINEERING_REVIEWER_MODEL_CONTEXT_ISOLATION_ENABLED": "true",
        "PGLOOM_ENGINEERING_REVIEWER_MODEL_CONTEXT_ADD_DIR_ENABLED": "false",
        "PGLOOM_ENGINEERING_ROLE_MODEL_CONTEXT_ROOT": str(role_context_root),
        "PGLOOM_ENGINEERING_RTK_FILTER_ENABLED": "true",
    }
    old = {key: os.environ.get(key) for key in patch}
    os.environ.update(patch)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _model_from_command(command: list[str]) -> str | None:
    if "-m" in command:
        index = command.index("-m")
        if index + 1 < len(command):
            return command[index + 1]
    if "--model" in command:
        index = command.index("--model")
        if index + 1 < len(command):
            return command[index + 1]
    return None


def _reasoning_from_command(command: list[str]) -> str | None:
    for index, item in enumerate(command):
        candidate = ""
        if item.startswith("model_reasoning_effort="):
            candidate = item
        elif item == "-c" and index + 1 < len(command):
            candidate = command[index + 1]
        if not candidate.startswith("model_reasoning_effort="):
            continue
        return candidate.partition("=")[2].strip().strip('"')
    return None


def _slice_id_for_task_type(task_type: str) -> str:
    return {
        "engineering.qa.author": "qa-author",
        "engineering.implement": "implementer",
        "engineering.review": "reviewer",
        "engineering.qa.verify.scrutiny": "qa-scrutiny",
        "engineering.qa.verify.usertest": "qa-usertest",
    }[task_type]


def _slot_for_task_type(task_type: str) -> str:
    return {
        "engineering.design": "designer",
        "engineering.qa.author": "qa-engineer",
        "engineering.implement": "implementer",
        "engineering.review": "reviewer",
        "engineering.qa.verify.scrutiny": "qa-scrutiny",
        "engineering.qa.verify.usertest": "qa-usertest",
    }.get(task_type, "planner")


def _collect_output_evidence(
    *,
    repo: Path,
    output_dir: Path,
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    status = _git(repo, "status", "--short")
    diff = _git(repo, "diff", "--")
    files = _repo_file_snapshots(repo, status=status)
    artifacts = _artifact_manifest(aggregate)
    telemetry = _telemetry_summary(aggregate)
    evidence = {
        "repo": str(repo),
        "git_status": status,
        "git_diff_path": str(output_dir / "worktree.diff"),
        "file_snapshots_path": str(output_dir / "file-snapshots.json"),
        "artifact_manifest_path": str(output_dir / "artifacts.json"),
        "telemetry_summary_path": str(output_dir / "telemetry-summary.json"),
        "changed_files": sorted(files),
        "artifacts": artifacts,
        "telemetry": telemetry,
    }
    (output_dir / "worktree.status.txt").write_text(status, encoding="utf-8")
    (output_dir / "worktree.diff").write_text(diff, encoding="utf-8")
    (output_dir / "file-snapshots.json").write_text(
        json.dumps(files, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "artifacts.json").write_text(
        json.dumps(artifacts, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (output_dir / "telemetry-summary.json").write_text(
        json.dumps(telemetry, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return evidence


def _evidence_repo(default_repo: Path, aggregate: dict[str, Any]) -> Path:
    paths: list[Path] = []
    for row in reversed(aggregate.get("task_contracts") or []):
        if not isinstance(row, dict):
            continue
        output = row.get("output_contract")
        if not isinstance(output, dict):
            continue
        payloads = [output]
        raw_qa = output.get("qa_author_contract")
        if isinstance(raw_qa, dict):
            payloads.append(raw_qa)
        raw_result = output.get("task_result_contract")
        if isinstance(raw_result, dict):
            payloads.append(raw_result)
        for candidate in payloads:
            raw_path = candidate.get("worktree_path")
            if isinstance(raw_path, str) and raw_path:
                path = Path(raw_path)
                if path.exists():
                    paths.append(path)
    for path in paths:
        if _git(path, "status", "--short").strip() or _git(path, "diff", "--").strip():
            return path
    if paths:
        return paths[0]
    return default_repo


def _repo_file_snapshots(repo: Path, *, status: str) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for relative in _status_paths(status):
        path = repo / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        snapshots[relative] = {
            "size_bytes": path.stat().st_size,
            "excerpt": text[:4000],
        }
    return snapshots


def _status_paths(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        if raw_path and raw_path != ".gradle-user-home/":
            paths.append(raw_path.rstrip("/"))
    return sorted(dict.fromkeys(paths))


def _artifact_manifest(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for row in aggregate.get("artifacts") or []:
        if not isinstance(row, dict):
            continue
        uri = str(row.get("uri") or "")
        excerpt = ""
        if uri and Path(uri).is_file():
            excerpt = Path(uri).read_text(encoding="utf-8", errors="replace")[:4000]
        manifest.append(
            {
                "id": row.get("id"),
                "task_id": row.get("task_id"),
                "artifact_type": row.get("artifact_type"),
                "uri": uri,
                "size_bytes": row.get("size_bytes"),
                "metadata": row.get("metadata"),
                "excerpt": excerpt,
            }
        )
    return manifest


def _telemetry_summary(aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_usage": aggregate.get("model_usage", {}).get("summary", {}),
        "token_savior": aggregate.get("token_savior", {}).get("summary", {}),
        "worker_run_summary": aggregate.get("worker_run_summary", {}),
        "worker_runs": [
            {
                "task_id": row.get("task_id"),
                "role": row.get("role"),
                "phase": row.get("phase"),
                "validator_type": row.get("validator_type"),
                "status": row.get("status"),
                "cost_usd": row.get("cost_usd"),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "reasoning_tokens": row.get("reasoning_tokens"),
                "cached_input_tokens": row.get("cached_input_tokens"),
                "cache_creation_tokens": row.get("cache_creation_tokens"),
                "token_savior_saved_tokens": row.get("token_savior_saved_tokens"),
                "rtk_saved_tokens": row.get("rtk_saved_tokens"),
                "running_seconds": row.get("running_seconds"),
                "blocker_code": row.get("blocker_code"),
                "metadata": row.get("metadata"),
                "model_usage": (row.get("metadata") or {}).get("model_usage")
                if isinstance(row.get("metadata"), dict)
                else [],
            }
            for row in aggregate.get("worker_runs") or []
            if isinstance(row, dict)
        ],
    }


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout + completed.stderr


def _production_grade_review(
    *,
    aggregate: dict[str, Any],
    output_evidence: dict[str, Any],
) -> dict[str, Any]:
    dimensions = [
        _grade_workflow_state(aggregate),
        _grade_plan(aggregate),
        _grade_qa_author(aggregate, output_evidence),
        _grade_implementation(aggregate, output_evidence),
        _grade_reviewer(aggregate),
        _grade_validator(aggregate, "scrutiny"),
        _grade_validator(aggregate, "usertest"),
        _grade_token_efficiency(output_evidence),
    ]
    blocking = [
        finding
        for dimension in dimensions
        for finding in dimension.get("findings", [])
        if finding.get("severity") == "blocking"
    ]
    advisory = [
        finding
        for dimension in dimensions
        for finding in dimension.get("findings", [])
        if finding.get("severity") != "blocking"
    ]
    review = {
        "verdict": "production_grade" if not blocking else "not_production_grade",
        "dimensions": dimensions,
        "blocking_findings": blocking,
        "advisory_findings": advisory,
    }
    return review


def _grade_workflow_state(aggregate: dict[str, Any]) -> dict[str, Any]:
    tasks = [row for row in aggregate.get("tasks") or [] if isinstance(row, dict)]
    if not tasks:
        return _dimension(
            "workflow_state",
            "missing",
            _finding("blocking", "workflow_no_tasks", "No workflow tasks found."),
        )
    findings: list[dict[str, str]] = []
    for task in tasks:
        state = str(task.get("state") or "")
        if state == "done":
            continue
        task_id = str(task.get("id") or "")
        task_type = str(task.get("task_type") or "")
        blocker = str(task.get("blocker_code") or task.get("blocker_reason") or "")
        suffix = f" ({blocker})" if blocker else ""
        findings.append(
            _finding(
                "blocking",
                "workflow_task_not_complete",
                f"{task_type} task {task_id} is {state}{suffix}.",
            )
        )
    return _dimension(
        "workflow_state",
        "production_grade" if not findings else "revise",
        *findings,
    )


def _grade_plan(aggregate: dict[str, Any]) -> dict[str, Any]:
    plan_row = aggregate.get("active_plan_contract")
    if not isinstance(plan_row, dict):
        return _dimension(
            "plan",
            "missing",
            _finding("blocking", "plan_missing", "No active plan."),
        )
    raw_plan = plan_row.get("contract")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    task_types = {
        item.get("task_type")
        for item in plan.get("task_slices", [])
        if isinstance(item, dict)
    }
    required = {
        "engineering.qa.author",
        "engineering.implement",
        "engineering.review",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }
    findings: list[dict[str, str]] = []
    if not required.issubset(task_types):
        findings.append(
            _finding(
                "blocking",
                "plan_missing_roles",
                f"Plan missing role slices: {sorted(required - task_types)}",
            )
        )
    if not plan.get("milestones"):
        findings.append(_finding("blocking", "plan_missing_milestones", "No milestones."))
    if not plan.get("acceptance_assertions"):
        findings.append(
            _finding("blocking", "plan_missing_assertions", "No acceptance assertions.")
        )
    try:
        production_grade = evaluate_production_grade(PlanContract.model_validate(plan))
    except Exception as exc:
        findings.append(
            _finding(
                "blocking",
                "plan_contract_invalid",
                f"Plan contract cannot be production-grade evaluated: {exc}",
            )
        )
    else:
        for finding in production_grade.blocking_findings:
            findings.append(
                _finding("blocking", finding.code, finding.message)
            )
    return _dimension("plan", "production_grade" if not findings else "revise", *findings)


def _grade_qa_author(
    aggregate: dict[str, Any],
    output_evidence: dict[str, Any],
) -> dict[str, Any]:
    qa = _output_for_task_type(aggregate, "engineering.qa.author").get("qa_author_contract")
    if not isinstance(qa, dict):
        return _dimension(
            "qa_author",
            "missing",
            _finding("blocking", "qa_missing", "No QA contract."),
        )
    findings: list[dict[str, str]] = []
    if not qa.get("tests_added"):
        findings.append(_finding("blocking", "qa_no_tests", "QA author produced no tests."))
    if not qa.get("red_proof"):
        findings.append(_finding("blocking", "qa_no_red_proof", "QA author produced no red proof."))
    matrix = qa.get("matrix_coverage")
    if not isinstance(matrix, dict) or not matrix:
        findings.append(
            _finding("blocking", "qa_no_matrix", "QA author produced no coverage matrix.")
        )
    snapshots = _file_snapshots(output_evidence)
    benchmark = _snapshot_text(
        snapshots,
        "benchmarks/src/jmh/java/com/joshorig/ull/lvc/bench/RangeScanBenchmark.java",
    )
    if "Proxy.newProxyInstance" in benchmark or "InvocationHandler" in benchmark:
        findings.append(
            _finding(
                "blocking",
                "qa_benchmark_allocating_visitor",
                (
                    "RangeScanBenchmark uses a reflection proxy visitor, which "
                    "allocates on visit and cannot prove zero-allocation range scans."
                ),
            )
        )
    diff_path = output_evidence.get("git_diff_path")
    diff_text = (
        Path(str(diff_path)).read_text(encoding="utf-8", errors="replace")
        if diff_path
        else ""
    )
    conformance = _snapshot_text(
        snapshots,
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/RangeConformanceTest.java",
    )
    if not conformance:
        conformance = _snapshot_text(
            snapshots,
            "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/RangeScanConformanceTest.java",
        )
    journey = _snapshot_text(
        snapshots,
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/RangeScanConsumerJourneyTest.java",
    )
    support = _snapshot_text(
        snapshots,
        "conformance-tests/src/test/java/com/joshorig/ull/lvc/conformance/RangeApiTestSupport.java",
    )
    if "assertPrefixOverloadPresent" in conformance and "assertVisitedSlots" in support:
        prefixed_calls = (
            ".putByte(4" in support
            and "new UnsafeBuffer" in conformance
            and "assertVisitedSlots" in conformance
            and "prefix" in conformance
        )
        if not prefixed_calls:
            findings.append(
                _finding(
                    "blocking",
                    "qa_prefix_filter_only_structural",
                    (
                        "Prefix-filter QA only checks overload presence; it does "
                        "not exercise matching and non-matching prefixes."
                    ),
                )
            )
    prefix_surface = f"{conformance}\n{journey}\n{diff_text}"
    if _range_prefix_looks_payload_based(prefix_surface):
        findings.append(
            _finding(
                "blocking",
                "qa_key_prefix_drifted_to_payload_prefix",
                (
                    "R-003 asks for key-prefix filtering, but accepted QA artifacts "
                    "appear to seed matching bytes into payloads instead of proving "
                    "prefix matching against logical keys or an explicit key mapping."
                ),
            )
        )
    return _dimension("qa_author", "production_grade" if not findings else "revise", *findings)


def _range_prefix_looks_payload_based(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if "prefix" not in lowered:
        return False
    has_prefix_range_exercise = any(
        marker in lowered
        for marker in [
            "ascendingrange",
            "descendingrange",
            "collectascending",
            "collectdescending",
        ]
    )
    if not has_prefix_range_exercise:
        return False
    key_signals = [
        "keyprefix",
        "key prefix",
        "keyindex",
        "genericlvc",
        "longkeyindex",
        "toslotid",
        "logical key",
        "logical-key",
        "key bytes",
        "keybytes",
    ]
    if any(signal in lowered for signal in key_signals):
        return False
    compact = re.sub(r"\s+", "", lowered)
    payload_signals = [
        "payload[0]=",
        "payload[1]=",
        ".putbyte(0,",
        ".putbyte(1,",
        "payload.putbyte(0,",
        "payload.putbyte(1,",
        "matchesprefix(buffer,payloadoffset,len,prefix)",
        "matchesprefix(slab,payloadoffset,len,prefix)",
        "buffer.getbyte(offset+i)!=prefix[i]",
    ]
    return any(signal in compact for signal in payload_signals) or "payloadprefix" in compact


def _grade_implementation(
    aggregate: dict[str, Any],
    output_evidence: dict[str, Any],
) -> dict[str, Any]:
    impl = _output_for_task_type(aggregate, "engineering.implement")
    findings: list[dict[str, str]] = []
    if not impl:
        return _dimension(
            "implementation",
            "missing",
            _finding("blocking", "implementation_missing", "No implementation result."),
        )
    nested_impl = impl.get("task_result_contract")
    impl_contract: dict[str, Any] = nested_impl if isinstance(nested_impl, dict) else impl
    if impl_contract.get("blockers"):
        findings.append(
            _finding(
                "blocking",
                "implementation_reported_blockers",
                f"Implementation reported blockers: {impl_contract.get('blockers')}",
            )
        )
    raw_changed = impl_contract.get("changed_files")
    changed: list[str] = (
        [str(item) for item in raw_changed] if isinstance(raw_changed, list) else []
    )
    if not changed:
        findings.append(
            _finding("blocking", "implementation_no_changed_files", "No changed files reported.")
        )
    diff_path = output_evidence.get("git_diff_path")
    diff_text = (
        Path(str(diff_path)).read_text(encoding="utf-8", errors="replace")
        if diff_path
        else ""
    )
    snapshots = _file_snapshots(output_evidence)
    if _diff_introduces_forbidden_query_model(diff_text):
        findings.append(
            _finding(
                "blocking",
                "implementation_forbidden_model",
                "Implementation diff mentions forbidden query/index model.",
            )
        )
    benchmark = _snapshot_text(
        snapshots,
        "benchmarks/src/jmh/java/com/joshorig/ull/lvc/bench/RangeScanBenchmark.java",
    )
    if "Proxy.newProxyInstance" in benchmark or "InvocationHandler" in benchmark:
        findings.append(
            _finding(
                "blocking",
                "implementation_allocating_benchmark_visitor",
                (
                    "RangeScanBenchmark uses a dynamic proxy/InvocationHandler "
                    "visitor, so benchmark evidence would include per-visit allocation."
                ),
            )
        )
    if (
        "benchmarks/src/jmh/java/com/joshorig/ull/lvc/bench/RangeScanBenchmark.java"
        in changed
        and "benchmarks/build.gradle" not in changed
    ):
        findings.append(
            _finding(
                "blocking",
                "implementation_range_benchmark_not_gated",
                (
                    "RangeScanBenchmark was added without updating the benchmark "
                    "smoke allocation gate."
                ),
            )
        )
    return _dimension("implementation", "production_grade" if not findings else "revise", *findings)


def _diff_introduces_forbidden_query_model(diff_text: str) -> bool:
    forbidden_terms = ("sorted-by-value", "secondary index", "secondary indexes")
    negating_terms = (
        "out of scope",
        "forbidden",
        "do not",
        "does not",
        "must not",
        "no secondary index",
        "no secondary indexes",
        "not introduce",
        "not introduced",
        "without secondary index",
    )
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        lowered = line.lower()
        if not any(term in lowered for term in forbidden_terms):
            continue
        if any(term in lowered for term in negating_terms):
            continue
        return True
    return False


def _grade_reviewer(aggregate: dict[str, Any]) -> dict[str, Any]:
    review = _output_for_task_type(aggregate, "engineering.review").get("review_verdict_contract")
    task = _task_for_type(aggregate, "engineering.review")
    if isinstance(review, dict):
        return _dimension(
            "reviewer",
            "production_grade",
            *(
                [
                    _finding(
                        "advisory",
                        "reviewer_non_approve",
                        f"Reviewer verdict was {review.get('verdict')}.",
                    )
                ]
                if review.get("verdict") != "approve"
                else []
            ),
        )
    raw = (
        task.get("result", {}).get("raw_response")
        if isinstance(task.get("result"), dict)
        else None
    )
    if isinstance(raw, str) and raw.strip():
        return _dimension(
            "reviewer",
            "production_grade",
            _finding(
                "advisory",
                "reviewer_output_not_normalized",
                "Reviewer produced useful raw findings but contract normalization failed.",
            ),
        )
    return _dimension(
        "reviewer",
        "missing",
        _finding("blocking", "review_missing", "No reviewer verdict or raw review output."),
    )


def _grade_validator(aggregate: dict[str, Any], validator: str) -> dict[str, Any]:
    task_type = f"engineering.qa.verify.{validator}"
    result = _output_for_task_type(aggregate, task_type).get("qa_result_contract")
    if not isinstance(result, dict):
        return _dimension(
            f"qa_{validator}",
            "missing",
            _finding("blocking", f"{validator}_missing", f"No {validator} QA result."),
        )
    findings = []
    if result.get("verdict") != "pass":
        findings.append(
            _finding(
                "blocking",
                f"{validator}_not_pass",
                f"{validator} verdict was {result.get('verdict')}.",
            )
        )
    if not result.get("validation_evidence"):
        findings.append(
            _finding(
                "blocking",
                f"{validator}_no_evidence",
                f"{validator} produced no typed validation evidence.",
            )
        )
    return _dimension(
        f"qa_{validator}",
        "production_grade" if not findings else "revise",
        *findings,
    )


def _grade_token_efficiency(output_evidence: dict[str, Any]) -> dict[str, Any]:
    telemetry = output_evidence.get("telemetry") if isinstance(output_evidence, dict) else {}
    worker_runs = telemetry.get("worker_runs", []) if isinstance(telemetry, dict) else []
    findings: list[dict[str, str]] = []
    for row in worker_runs:
        if not isinstance(row, dict):
            continue
        input_tokens = int(row.get("input_tokens") or 0)
        if input_tokens > 250_000:
            findings.append(
                _finding(
                    "blocking",
                    "worker_prompt_too_large",
                    f"{row.get('role')}:{row.get('phase')} used {input_tokens} input tokens.",
                )
            )
    return _dimension(
        "token_efficiency",
        "production_grade" if not findings else "revise",
        *findings,
    )


def _output_for_task_type(aggregate: dict[str, Any], task_type: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for row in aggregate.get("task_contracts") or []:
        if not isinstance(row, dict):
            continue
        input_contract = row.get("input_contract")
        if not isinstance(input_contract, dict) or input_contract.get("task_type") != task_type:
            continue
        output = row.get("output_contract")
        if isinstance(output, dict):
            matches.append(row)
    if not matches:
        return {}
    completed = [row for row in matches if row.get("status") == "completed"]
    selected = (completed or matches)[-1]
    output = selected.get("output_contract")
    return output if isinstance(output, dict) else {}


def _task_for_type(aggregate: dict[str, Any], task_type: str) -> dict[str, Any]:
    for row in aggregate.get("tasks") or []:
        if isinstance(row, dict) and row.get("task_type") == task_type:
            return row
    return {}


def _dimension(name: str, verdict: str, *findings: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "verdict": verdict, "findings": list(findings)}


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _file_snapshots(output_evidence: dict[str, Any]) -> dict[str, Any]:
    raw_path = output_evidence.get("file_snapshots_path")
    if not isinstance(raw_path, str) or not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _snapshot_text(snapshots: dict[str, Any], path: str) -> str:
    item = snapshots.get(path)
    if not isinstance(item, dict):
        return ""
    excerpt = item.get("excerpt")
    return excerpt if isinstance(excerpt, str) else ""


def _score(
    *,
    role: str,
    aggregate: dict[str, Any],
    output_evidence: dict[str, Any],
    enabled_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    worker_runs = aggregate.get("worker_runs") or []
    handoffs = aggregate.get("handoffs") or []
    task_contracts = aggregate.get("task_contracts") or []
    expected_map = {
        "planner": {"engineering.plan"},
        "qa-author": {"engineering.qa.author"},
        "implementer": {"engineering.implement"},
        "reviewer": {"engineering.review"},
        "qa-scrutiny": {"engineering.qa.verify.scrutiny"},
        "qa-usertest": {"engineering.qa.verify.usertest"},
        "worker-orchestration": {
            "engineering.implement",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        },
        "qa-to-end": {
            "engineering.qa.author",
            "engineering.implement",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        },
        "orchestration": {
            "engineering.plan",
            "engineering.qa.author",
            "engineering.implement",
            "engineering.review",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        },
    }
    expected = _expected_task_types_for_enabled_roles(enabled_roles) or expected_map[role]
    if role == "orchestration":
        expected = {
            str(row.get("task_type"))
            for row in aggregate.get("tasks") or []
            if isinstance(row, dict)
        }
    completed = {
        row["input_contract"]["task_type"]
        for row in task_contracts
        if row.get("status") == "completed" and isinstance(row.get("input_contract"), dict)
    }
    completed.update(
        str(row.get("task_type"))
        for row in aggregate.get("tasks") or []
        if isinstance(row, dict) and row.get("state") == "done"
    )
    run_types = {
        row.get("metadata", {}).get("task_type")
        for row in worker_runs
        if row.get("status") == "done"
    }
    non_done_tasks = [
        {
            "id": str(row.get("id") or ""),
            "task_type": str(row.get("task_type") or ""),
            "state": str(row.get("state") or ""),
            "blocker_code": str(row.get("blocker_code") or ""),
        }
        for row in aggregate.get("tasks") or []
        if isinstance(row, dict) and str(row.get("state") or "") != "done"
    ]
    return [
        {
            "name": "all workflow tasks done",
            "passed": not non_done_tasks,
            "actual": non_done_tasks,
        },
        {
            "name": "expected task types completed",
            "passed": expected.issubset(completed),
            "expected": sorted(expected),
            "actual": sorted(completed),
        },
        {
            "name": "worker telemetry recorded",
            "passed": expected.issubset(run_types),
            "expected": sorted(expected),
            "actual": sorted(item for item in run_types if isinstance(item, str)),
        },
        {
            "name": "handoff evidence recorded",
            "passed": bool(handoffs),
            "actual": [row.get("handoff_type") for row in handoffs],
        },
        {
            "name": "worktree artifacts preserved",
            "passed": bool(output_evidence.get("changed_files")),
            "actual": output_evidence.get("changed_files", []),
        },
        {
            "name": "telemetry economy captured",
            "passed": bool(
                output_evidence.get("telemetry", {})
                .get("worker_run_summary", {})
                .get("runs")
            ),
            "actual": output_evidence.get("telemetry", {}).get("worker_run_summary", {}),
        },
        {
            "name": "command artifacts captured",
            "passed": role == "reviewer" or bool(output_evidence.get("artifacts")),
            "actual": [
                item.get("artifact_type") for item in output_evidence.get("artifacts", [])
            ],
        },
    ]


def _expected_task_types_for_enabled_roles(enabled_roles: list[str] | None) -> set[str]:
    if not enabled_roles:
        return set()
    task_types: set[str] = set()
    for item in enabled_roles:
        normalized = item.replace("-", "_")
        mapped = {
            "planner": "engineering.plan",
            "design": "engineering.design",
            "designer": "engineering.design",
            "qa": "engineering.qa.author",
            "qa_author": "engineering.qa.author",
            "qa_engineer": "engineering.qa.author",
            "implementer": "engineering.implement",
            "implementation": "engineering.implement",
            "reviewer": "engineering.review",
            "review": "engineering.review",
            "qa_scrutiny": "engineering.qa.verify.scrutiny",
            "scrutiny": "engineering.qa.verify.scrutiny",
            "qa_usertest": "engineering.qa.verify.usertest",
            "usertest": "engineering.qa.verify.usertest",
            "recovery": "engineering.plan",
        }.get(normalized)
        if mapped is not None:
            task_types.add(mapped)
    return task_types


def _write_result(result: LiveRoleEvalResult) -> None:
    payload = result.asdict()
    (result.output_dir / "outcome.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    lines = [
        f"# {result.case_id}",
        "",
        f"- role: `{result.role}`",
        f"- status: `{result.status}`",
        f"- feature_id: `{result.feature_id}`",
        f"- elapsed_seconds: `{result.elapsed_seconds}`",
        "",
        "## Checks",
    ]
    for check in result.checks:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {mark}: {check['name']}")
    (result.output_dir / "outcome.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
