from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from pgloom_engineering.path_policy import is_qa_write_path

CONTRACT_VERSION = "engineering.contracts.v1"


class ImplementationTopology(StrEnum):
    SINGLE = "single"
    SPLIT_SPECIALISTS = "split_specialists"
    PARALLEL_CANDIDATES = "parallel_candidates"
    COUNCIL_DECIDES = "council_decides"


class AgentTopologyPolicy(BaseModel):
    planning: Literal["multi_agent"] = "multi_agent"
    review: Literal["multi_agent"] = "multi_agent"
    implementation: ImplementationTopology = ImplementationTopology.COUNCIL_DECIDES


class FeatureGoalContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    project: str
    goal: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    autonomy_policy: str = "autonomous_until_final_pr"
    final_human_gate: str = "final_feature_pr_merge"


class DesignContract(BaseModel):
    public_api: str = ""
    ownership_boundaries: str = ""
    concurrency_protocol: str = ""
    persistence_protocol: str = ""
    hard_constraints: list[str] = Field(default_factory=list)
    forbidden_alternatives: list[str] = Field(default_factory=list)
    acceptance_tests: list[str] = Field(default_factory=list)


class MilestoneContract(BaseModel):
    milestone_id: str
    name: str
    slice_ids: list[str]
    acceptance_assertions: list[str] = Field(default_factory=list)
    validation_contract: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    signoff_policy: Literal["scrutiny_and_usertest", "scrutiny_only"] = (
        "scrutiny_and_usertest"
    )


class TaskSliceContract(BaseModel):
    slice_id: str
    role: str
    task_type: str
    objective: str
    allowed_paths: list[str]
    forbidden_paths: list[str]
    depends_on: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    verification_commands: list[list[str]] = Field(default_factory=list)
    acceptance_assertion_ids: list[str] = Field(default_factory=list)
    grading_criteria: list[str] = Field(default_factory=list)
    validation_strategy: dict[str, Any] = Field(default_factory=dict)
    context_budget: int | None = None
    model_route_hint: str | None = None
    required_procedures: list[str] = Field(default_factory=list)
    handoff_requirements: list[str] = Field(default_factory=list)
    milestone_id: str | None = None


class RoleGateContract(BaseModel):
    project: str
    role: str
    status: Literal["enabled", "disabled"]
    source: str = "engineering_projects.metadata.role_gates"
    reason: str


class PlanContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    project: str
    problem_statement: str
    assumptions: list[str] = Field(default_factory=list)
    design_contract: DesignContract
    affected_surfaces: list[str] = Field(default_factory=list)
    implementation_topology: ImplementationTopology = ImplementationTopology.COUNCIL_DECIDES
    task_slices: list[TaskSliceContract]
    acceptance_test_matrix: list[str]
    acceptance_assertions: list[str] = Field(default_factory=list)
    milestones: list[MilestoneContract] = Field(default_factory=list)
    risk_register: list[str] = Field(default_factory=list)
    self_heal_policy: str = "retry_repair_replan_then_escalate"
    finalization_policy: str = "open_final_feature_pr_for_human_merge"
    council_reports: list[dict[str, Any]] = Field(default_factory=list)
    supersedes_plan_id: str | None = None
    supersession_rationale: str | None = None

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        seen: set[str] = set()
        for task_slice in self.task_slices:
            if task_slice.slice_id in seen:
                raise ValueError(f"duplicate task slice id: {task_slice.slice_id}")
            missing = [dep for dep in task_slice.depends_on if dep not in seen]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(
                    f"{task_slice.slice_id} depends on unknown or later slices: {joined}"
            )
            seen.add(task_slice.slice_id)
        if not self.acceptance_assertions:
            self.acceptance_assertions = list(self.acceptance_test_matrix)
        if self.acceptance_assertions:
            for task_slice in self.task_slices:
                if not task_slice.acceptance_assertion_ids:
                    task_slice.acceptance_assertion_ids = list(self.acceptance_assertions)
        if not self.milestones and self.task_slices:
            self.milestones = [
                MilestoneContract(
                    milestone_id="m1",
                    name="Milestone 1",
                    slice_ids=[task_slice.slice_id for task_slice in self.task_slices],
                    acceptance_assertions=list(self.acceptance_assertions),
                    validation_contract={
                        "scrutiny": True,
                        "usertest": True,
                    },
                )
            ]
        return self


class TaskContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    plan_contract_id: str
    role: str
    task_type: str
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    allowed_paths: list[str]
    forbidden_paths: list[str]
    dependencies: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    verification_commands: list[list[str]] = Field(default_factory=list)
    handoff_requirements: list[str] = Field(default_factory=list)
    required_procedures: list[str] = Field(default_factory=list)
    role_gate: RoleGateContract | None = None


class TaskResultContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    changed_files: list[str] = Field(default_factory=list)
    branch: str | None = None
    worktree_path: str | None = None
    commits: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    model_usage_ids: list[int] = Field(default_factory=list)
    token_savior_usage_ids: list[int] = Field(default_factory=list)
    commands_run: list[dict[str, Any]] = Field(default_factory=list)
    procedures_attestation: dict[str, bool | str] = Field(default_factory=dict)
    handoff_id: str | None = None


class QAAuthorContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    tests_added: list[str] = Field(default_factory=list)
    matrix_coverage: dict[str, list[str]] = Field(default_factory=dict)
    red_proof: list[dict[str, Any]] = Field(default_factory=list)
    paths_touched: list[str] = Field(default_factory=list)
    branch: str | None = None
    worktree_path: str | None = None
    model_usage_ids: list[int] = Field(default_factory=list)


class ReviewVerdictContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    panel: list[str]
    verdict: Literal["approve", "coder_repair", "planner_replan", "qa_expand", "human_escalation"]
    rationale: str
    findings: list[str] = Field(default_factory=list)


class QAResultContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    verdict: Literal["pass", "fail", "inconclusive"]
    commands: list[list[str]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    validation_evidence: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    validator_type: Literal["scrutiny", "usertest"] | None = None
    commands_run: list[dict[str, Any]] = Field(default_factory=list)
    procedures_attestation: dict[str, bool | str] = Field(default_factory=dict)


class RecoveryDecisionContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str | None = None
    blocker_code: str
    action: Literal[
        "retry",
        "replan",
        "repair_task",
        "pr_feedback_task",
        "rerun_verifier",
        "retire_superseded",
        "corrective_slice",
        "milestone_replan",
        "block_execution",
        "record_invalid_output",
        "record_crash",
        "human_escalation",
    ]
    rationale: str
    attempt: int = 1
    max_attempts: int = 3


class CommandRun(BaseModel):
    cmd: list[str]
    exit_code: int | None
    duration_s: float
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class ValidationEvidence(BaseModel):
    evidence_id: str
    kind: Literal[
        "test_run",
        "code_review",
        "ui_exercise",
        "integration_check",
        "lint_type_check",
        "benchmark",
        "screenshot",
        "network_trace",
        "command_log",
    ]
    summary: str
    verdict: Literal["pass", "fail", "inconclusive"]
    command_run_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HandoffEnvelope(BaseModel):
    handoff_id: str
    handoff_type: Literal[
        "plan_to_task",
        "worker_result",
        "validation",
        "recovery",
        "final",
        "task_result",
        "qa_author_contract",
    ]
    feature_id: str
    task_id: str | None = None
    role: str
    summary: str
    completed: list[str] = Field(default_factory=list)
    left_undone: list[str] = Field(default_factory=list)
    commands_run: list[CommandRun] = Field(default_factory=list)
    procedures_attestation: dict[str, bool | str] = Field(default_factory=dict)
    issues_discovered: list[dict[str, Any]] = Field(default_factory=list)
    next_worker_context: str = ""
    reviewer_context: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    telemetry_summary: dict[str, Any] = Field(default_factory=dict)
    evidence: list[ValidationEvidence] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    cumulative_cost_usd: Decimal = Decimal("0")
    cumulative_wall_clock_seconds: float = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_tokens_saved: int = 0
    cumulative_model_calls: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class FinalizationEvidenceContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    plan_contract_id: str
    task_prs: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    model_usage: dict[str, Any] = Field(default_factory=dict)
    token_savior: dict[str, Any] = Field(default_factory=dict)
    recovery_history: list[dict[str, Any]] = Field(default_factory=list)


def contract_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def contract_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_plan_contract(
    contract: PlanContract,
    *,
    origin_contract: dict[str, Any] | None = None,
    qa_write_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not contract.acceptance_test_matrix:
        errors.append(_error("missing_acceptance_matrix", "Plan must define acceptance tests."))
    if not contract.affected_surfaces:
        errors.append(_error("missing_affected_surfaces", "Plan must define affected surfaces."))
    if not contract.task_slices:
        errors.append(_error("missing_task_slices", "Plan must emit at least one task slice."))
    if not contract.acceptance_assertions:
        errors.append(
            _error(
                "missing_acceptance_assertions",
                "Plan must define acceptance assertions for bidirectional coverage.",
            )
        )
    if not contract.milestones:
        errors.append(_error("missing_milestones", "Plan must define milestone contracts."))
    if contract.finalization_policy != "open_final_feature_pr_for_human_merge":
        errors.append(
            _error("invalid_finalization_policy", "Final PR merge must remain human-gated.")
        )
    if contract.design_contract.acceptance_tests and not contract.acceptance_test_matrix:
        errors.append(_error("missing_acceptance_matrix", "Design acceptance tests need a matrix."))
    errors.extend(_validate_design_contract_drift(contract, origin_contract=origin_contract))
    errors.extend(_validate_lifecycle_acceptance(contract))
    errors.extend(_validate_milestones(contract))
    errors.extend(_validate_acceptance_assertion_coverage(contract))
    for task_slice in contract.task_slices:
        if task_slice.role not in {"designer", "implementer", "reviewer", "qa", "historian"}:
            errors.append(
                _error(
                    "invalid_task_role",
                    f"{task_slice.slice_id} has unsupported role {task_slice.role}.",
                )
            )
        errors.extend(_validate_role_task_type(task_slice))
        errors.extend(_validate_role_path_policy(task_slice, qa_write_paths=qa_write_paths))
        if not task_slice.allowed_paths:
            errors.append(
                _error("missing_allowed_paths", f"{task_slice.slice_id} must name allowed paths.")
            )
        if not task_slice.forbidden_paths:
            errors.append(
                _error(
                    "missing_forbidden_paths",
                    f"{task_slice.slice_id} must name forbidden paths.",
                )
            )
        if not task_slice.verification_commands:
            errors.append(
                _error(
                    "missing_verification_commands",
                    f"{task_slice.slice_id} must define verification commands.",
                )
            )
        if not task_slice.expected_outputs:
            errors.append(
                _error("missing_expected_outputs", f"{task_slice.slice_id} must define outputs.")
            )
    return errors


def _validate_role_task_type(task_slice: TaskSliceContract) -> list[dict[str, str]]:
    allowed = {
        "designer": {"engineering.design", "engineering.designer"},
        "implementer": {"engineering.implement", "engineering.implementation"},
        "reviewer": {"engineering.review"},
        "qa": {
            "engineering.qa.author",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        },
        "historian": {"engineering.history", "engineering.historian"},
    }.get(task_slice.role)
    if allowed is None or task_slice.task_type in allowed:
        return []
    return [
        _error(
            "invalid_role_task_type",
            (
                f"{task_slice.slice_id} role {task_slice.role} cannot use "
                f"task_type {task_slice.task_type}."
            ),
        )
    ]


def _validate_role_path_policy(
    task_slice: TaskSliceContract,
    *,
    qa_write_paths: list[str] | None = None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if task_slice.task_type in {
        "engineering.qa.author",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }:
        bad_paths = [
            path for path in task_slice.allowed_paths if not is_qa_write_path(path, qa_write_paths)
        ]
        if bad_paths:
            errors.append(
                _error(
                    "qa_paths_not_restricted",
                    (
                        f"{task_slice.slice_id} QA allowed_paths must be restricted "
                        "to QA/test roots."
                    ),
                )
            )
    if task_slice.role == "implementer":
        bad_paths = [
            path for path in task_slice.allowed_paths if is_qa_write_path(path, qa_write_paths)
        ]
        if bad_paths:
            errors.append(
                _error(
                    "implementer_claims_qa_paths",
                    f"{task_slice.slice_id} implementer slices may not write QA test paths.",
                )
            )
    return errors


def _validate_milestones(contract: PlanContract) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    slice_ids = {item.slice_id for item in contract.task_slices}
    seen: set[str] = set()
    for milestone in contract.milestones:
        if milestone.milestone_id in seen:
            errors.append(
                _error(
                    "duplicate_milestone_id",
                    f"duplicate milestone id: {milestone.milestone_id}",
                )
            )
        seen.add(milestone.milestone_id)
        missing = [slice_id for slice_id in milestone.slice_ids if slice_id not in slice_ids]
        if missing:
            errors.append(
                _error(
                    "milestone_unknown_slice",
                    f"{milestone.milestone_id} references unknown slices: {', '.join(missing)}.",
                )
            )
        unknown_deps = [dep for dep in milestone.depends_on if dep not in seen]
        if unknown_deps:
            errors.append(
                _error(
                    "milestone_unknown_dependency",
                    f"{milestone.milestone_id} depends on unknown or later milestones: "
                    f"{', '.join(unknown_deps)}.",
                )
            )
    return errors


def _validate_acceptance_assertion_coverage(contract: PlanContract) -> list[dict[str, str]]:
    assertion_labels = {
        canonical_acceptance_assertion_id(assertion): assertion
        for assertion in contract.acceptance_assertions
    }
    for milestone in contract.milestones:
        assertion_labels.update(
            {
                canonical_acceptance_assertion_id(assertion): assertion
                for assertion in milestone.acceptance_assertions
            }
        )
    if not assertion_labels:
        return []
    errors: list[dict[str, str]] = []
    claimed_labels: dict[str, str] = {}
    for task_slice in contract.task_slices:
        if not task_slice.acceptance_assertion_ids:
            errors.append(
                _error(
                    "slice_missing_acceptance_assertion",
                    f"{task_slice.slice_id} must claim at least one acceptance assertion.",
                )
            )
        claimed_labels.update(
            {
                canonical_acceptance_assertion_id(assertion): assertion
                for assertion in task_slice.acceptance_assertion_ids
            }
        )
    missing = sorted(
        assertion_labels[assertion]
        for assertion in assertion_labels.keys() - claimed_labels.keys()
    )
    unknown = sorted(
        claimed_labels[assertion]
        for assertion in claimed_labels.keys() - assertion_labels.keys()
    )
    if missing:
        errors.append(
            _error(
                "acceptance_assertion_unclaimed",
                f"Acceptance assertions have no claiming slice: {', '.join(missing)}.",
            )
        )
    if unknown:
        errors.append(
            _error(
                "acceptance_assertion_unknown",
                f"Slices claim unknown acceptance assertions: {', '.join(unknown)}.",
            )
        )
    return errors


def canonical_acceptance_assertion_id(assertion: str) -> str:
    return assertion.split(":", 1)[0].strip()


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _validate_design_contract_drift(
    contract: PlanContract,
    *,
    origin_contract: dict[str, Any] | None,
) -> list[dict[str, str]]:
    if not origin_contract:
        return []
    origin_design = origin_contract.get("design_contract")
    if not isinstance(origin_design, dict):
        return []
    current_design = contract.design_contract.model_dump(mode="json")
    changed = [
        key
        for key in (
            "public_api",
            "ownership_boundaries",
            "concurrency_protocol",
            "persistence_protocol",
        )
        if origin_design.get(key) and current_design.get(key) != origin_design.get(key)
    ]
    if changed and not (contract.supersedes_plan_id and contract.supersession_rationale):
        return [
            _error(
                "planner_contract_drift",
                "Design contract changed without supersedes_plan_id and supersession_rationale: "
                + ", ".join(changed),
            )
        ]
    return []


def _validate_lifecycle_acceptance(contract: PlanContract) -> list[dict[str, str]]:
    text = " ".join(
        [
            contract.problem_statement,
            contract.design_contract.public_api,
            " ".join(contract.design_contract.acceptance_tests),
            " ".join(contract.acceptance_test_matrix),
        ]
    ).lower()
    if not any(
        term in text
        for term in (
            "restore",
            "snapshot",
            "lifecycle",
            "state machine",
            "corruption",
            "recovery",
        )
    ):
        return []
    tests = " ".join(
        contract.design_contract.acceptance_tests + contract.acceptance_test_matrix
    ).lower()
    missing = [
        label
        for label, terms in {
            "stale_or_invalid": ("stale", "invalid", "open store", "closed", "precondition"),
            "invariant": ("invariant", "corrupt", "corruption", "crc", "state"),
            "failure_path": ("failure", "timeout", "exception", "partial"),
        }.items()
        if not any(term in tests for term in terms)
    ]
    if missing:
        return [
            _error(
                "planner_contract_incomplete",
                "Stateful lifecycle acceptance coverage missing: " + ", ".join(missing),
            )
        ]
    return []
