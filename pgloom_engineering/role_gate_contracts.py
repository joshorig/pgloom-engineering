# ruff: noqa: E501, I001

from __future__ import annotations

from pathlib import Path
from typing import Any

from pgloom_engineering.contracts import PlanContract, TaskContract


CONTRACT_VERSION = "engineering.role_gate_contract.v1"


def build_planner_gate_contract(
    *,
    project_context: Any | None = None,
    planner_rubric: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _base_contract(
        role="planner",
        judged_by=[
            "PlanContract schema validation",
            "validate_plan_contract",
            "evaluate_production_grade",
            "planner deterministic critic checks",
            "post-normalization production-grade validation before decomposition",
        ],
        deterministic_gates=[
            "Emit canonical role/task_type pairs only.",
            "Include design, qa.author, implementer, reviewer, qa.verify.scrutiny, and qa.verify.usertest unless metadata authorizes user-test skip.",
            "Milestones must contain executable validation contracts; scrutiny_and_usertest milestones include both validator slices.",
            "Every acceptance assertion is claimed by at least one slice; every slice claims at least one assertion.",
            "QA paths stay in QA/test/support roots and are disjoint from implementer paths.",
            "Implementer paths are package/file-level production roots, not broad module source roots.",
            "Per-feature validation uses lint/build, feature-specific tests, and benchmark smoke only; do not schedule broad full regression gates.",
            "Hot-path shared API plans must include concrete implementations and discoverable wrappers/decorators/adapters that implement the changed contract.",
            "Variant-scoped implementer slices must use variant-specific verification commands or be merged into one broad implementation slice.",
            "Normalization cannot weaken the plan; the normalized persisted plan is judged again before task decomposition.",
        ],
        response_contract="PlanContract",
        project_inputs=_project_context_payload(project_context),
        planner_rubric=planner_rubric or [],
    )


def build_task_role_gate_contract(
    *,
    role: str,
    plan: PlanContract | None = None,
    task_contract: TaskContract | None = None,
    project_metadata: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    if role == "qa.author":
        return _qa_author_contract(
            plan=plan,
            task_contract=task_contract,
            project_metadata=project_metadata,
            project_root=project_root,
        )
    if role == "implementer":
        return _implementer_contract(
            task_contract=task_contract,
            project_metadata=project_metadata,
        )
    if role == "implementer.repair":
        contract = _implementer_contract(
            task_contract=task_contract,
            project_metadata=project_metadata,
        )
        contract["role"] = "implementer.repair"
        contract["deterministic_gates"].append(
            "Repair only the failing evidence in the same worktree; do not broaden scope or rerun broad gates."
        )
        return contract
    if role == "reviewer":
        return _reviewer_contract(task_contract=task_contract)
    if role == "qa.verify.scrutiny":
        return _qa_scrutiny_contract(task_contract=task_contract)
    if role == "qa.verify.usertest":
        return _qa_usertest_contract(
            task_contract=task_contract,
            project_metadata=project_metadata,
        )
    return _base_contract(
        role=role,
        judged_by=["TaskContract schema", "role handler post-gates"],
        deterministic_gates=[
            "Follow the TaskContract scope, allowed_paths, forbidden_paths, verification_commands, and required_procedures.",
            "Return the role's required contract shape without extra commentary.",
        ],
        response_contract="role-specific contract",
        task_inputs=_task_payload(task_contract),
    )


def _qa_author_contract(
    *,
    plan: PlanContract | None,
    task_contract: TaskContract | None,
    project_metadata: dict[str, Any] | None,
    project_root: Path | None,
) -> dict[str, Any]:
    return _base_contract(
        role="qa.author",
        judged_by=[
            "QAAuthorContract schema validation",
            "path_violations",
            "qa semantic quality review",
            "red proof command verification",
            "matrix coverage post-gate",
        ],
        deterministic_gates=[
            "Only edit allowed QA/test/support paths; no production implementation edits.",
            "matrix_coverage must include every exact acceptance_test_matrix entry.",
            "Tests must be behavior proof, not method/route/class inventory only.",
            "Authored tests must compile except for valid red proof caused by the missing expected public API.",
            "Benchmark allocation tests must measure the hot operation without fixture/proxy/boxing/reflection allocation inside the measured method.",
            "RangeScanBenchmark allocation thresholds stay at the project requirement, default 0.005 B/op unless metadata explicitly declares otherwise.",
            "Configured benchmark smoke gates must actually run the authored benchmark evidence.",
        ],
        response_contract="QAAuthorContract",
        task_inputs=_task_payload(task_contract),
        project_inputs=_project_metadata_payload(project_metadata, project_root),
        plan_inputs=_plan_payload(plan),
    )


def _implementer_contract(
    *,
    task_contract: TaskContract | None,
    project_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    return _base_contract(
        role="implementer",
        judged_by=[
            "TaskResultContract schema validation",
            "implementation_path_violations",
            "TaskContract verification_commands",
            "downstream reviewer",
            "downstream qa.verify.scrutiny and qa.verify.usertest",
        ],
        deterministic_gates=[
            "Only edit TaskContract allowed_paths and never edit forbidden paths or QA-authored tests.",
            "Implement only this slice objective and acceptance_assertion_ids; allowed_paths are permissions, not extra scope.",
            "Run exactly TaskContract verification_commands before returning.",
            "Do not add or substitute broad gates such as ./gradlew test, ./gradlew check, qa/smoke.sh, qa/regression.sh, or full JMH sweeps unless listed in the TaskContract.",
            "For hot-path shared APIs, update concrete implementations and discoverable wrappers/decorators/adapters that implement the same contract.",
            "Return blockers only for real implementation blockers, with evidence and narrow next action.",
        ],
        response_contract="TaskResultContract",
        task_inputs=_task_payload(task_contract),
        project_inputs=_project_metadata_payload(project_metadata, None),
    )


def _reviewer_contract(*, task_contract: TaskContract | None) -> dict[str, Any]:
    return _base_contract(
        role="reviewer",
        judged_by=[
            "ReviewVerdictContract schema validation",
            "review verdict normalization",
            "workflow recovery on coder_repair",
        ],
        deterministic_gates=[
            "Review source/diff and command evidence against the plan and TaskContract.",
            "Do not run verification commands; QA scrutiny owns command execution.",
            "Use verdict=approve only for scoped, verified work that is ready for QA validation.",
            "Use verdict=coder_repair for implementation defects; natural reject/revise aliases are normalized but should not be emitted.",
            "Do not block solely because downstream QA/user-test gates have not run yet.",
        ],
        response_contract="ReviewVerdictContract",
        task_inputs=_task_payload(task_contract),
    )


def _qa_scrutiny_contract(*, task_contract: TaskContract | None) -> dict[str, Any]:
    return _base_contract(
        role="qa.verify.scrutiny",
        judged_by=[
            "QAResultContract schema validation",
            "feature-specific verification command results",
            "typed ValidationEvidence persistence",
            "workflow recovery on fail",
        ],
        deterministic_gates=[
            "Run the focused verification commands for this feature and record exit codes, durations, evidence, and artifact ids.",
            "Use lint/build, feature-specific tests, and benchmark smoke; do not substitute broad full regression.",
            "Return pass only when all required feature gates pass and evidence covers the acceptance assertions.",
        ],
        response_contract="QAResultContract",
        task_inputs=_task_payload(task_contract),
    )


def _qa_usertest_contract(
    *,
    task_contract: TaskContract | None,
    project_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    skip = _usertest_skip_authorized(project_metadata or {})
    return _base_contract(
        role="qa.verify.usertest",
        judged_by=[
            "QAResultContract schema validation",
            "model-driven user-facing exercise evidence",
            "broad-command rejection post-gate",
            "workflow recovery on fail",
        ],
        deterministic_gates=[
            "Act as a fresh-context user-test validator and exercise a user-facing browser, CLI, API, service, or consumer-library flow.",
            "Do not rely only on static inspection or deterministic command replay.",
            "Do not use deterministic test/check/JMH commands, broad regression, or benchmark sweeps as the user test.",
            "Record commands, transcript/evidence summaries, artifacts, timings, and verdict.",
            "Return pass only when the exercised user-facing behavior satisfies acceptance criteria.",
        ],
        response_contract="QAResultContract",
        task_inputs=_task_payload(task_contract),
        project_inputs={
            **_project_metadata_payload(project_metadata, None),
            "usertest_skip_authorized": skip,
        },
    )


def _base_contract(
    *,
    role: str,
    judged_by: list[str],
    deterministic_gates: list[str],
    response_contract: str,
    task_inputs: dict[str, Any] | None = None,
    project_inputs: dict[str, Any] | None = None,
    plan_inputs: dict[str, Any] | None = None,
    planner_rubric: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "role": role,
        "purpose": (
            "Authoritative upfront contract for how this role output will be judged. "
            "Treat these gates as executable requirements, not advisory prompt text."
        ),
        "judged_by": judged_by,
        "deterministic_gates": deterministic_gates,
        "response_contract": response_contract,
    }
    if task_inputs:
        payload["task_inputs"] = task_inputs
    if project_inputs:
        payload["project_inputs"] = project_inputs
    if plan_inputs:
        payload["plan_inputs"] = plan_inputs
    if planner_rubric:
        payload["planner_rubric"] = planner_rubric
    return payload


def _task_payload(task_contract: TaskContract | None) -> dict[str, Any]:
    if task_contract is None:
        return {}
    return {
        "task_type": task_contract.task_type,
        "objective": task_contract.objective,
        "allowed_paths": list(task_contract.allowed_paths),
        "forbidden_paths": list(task_contract.forbidden_paths),
        "verification_commands": task_contract.verification_commands,
        "acceptance_assertion_ids": list(
            task_contract.inputs.get("acceptance_assertion_ids") or []
        ),
        "required_procedures": list(task_contract.required_procedures),
        "milestone_id": task_contract.inputs.get("milestone_id"),
    }


def _plan_payload(plan: PlanContract | None) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "feature_id": plan.feature_id,
        "acceptance_test_matrix": list(plan.acceptance_test_matrix),
        "acceptance_assertions": list(plan.acceptance_assertions),
        "finalization_policy": plan.finalization_policy,
    }


def _project_context_payload(project_context: Any | None) -> dict[str, Any]:
    if project_context is None:
        return {}
    return {
        "project_root": str(getattr(project_context, "project_root", "")),
        "relevant_paths": list(getattr(project_context, "relevant_paths", []) or []),
        "qa_write_paths": list(getattr(project_context, "qa_write_paths", []) or []),
        "qa_policy_summary": getattr(project_context, "qa_policy_summary", {}) or {},
    }


def _project_metadata_payload(
    project_metadata: dict[str, Any] | None,
    project_root: Path | None,
) -> dict[str, Any]:
    metadata = project_metadata or {}
    return {
        "project_root": str(project_root) if project_root is not None else None,
        "qa": metadata.get("qa"),
        "relevant_paths": metadata.get("relevant_paths"),
        "smoke_command": metadata.get("smoke_command"),
        "regression_command": metadata.get("regression_command"),
        "usertest_harness": metadata.get("usertest_harness"),
    }


def _usertest_skip_authorized(metadata: dict[str, Any]) -> bool:
    harness = metadata.get("usertest_harness")
    return isinstance(harness, dict) and harness.get("kind") == "none"
