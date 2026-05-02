from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

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


class TaskResultContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    changed_files: list[str] = Field(default_factory=list)
    branch: str | None = None
    commits: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    model_usage_ids: list[int] = Field(default_factory=list)
    token_savior_usage_ids: list[int] = Field(default_factory=list)


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
    findings: list[str] = Field(default_factory=list)


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
        "block_execution",
        "record_invalid_output",
        "record_crash",
        "human_escalation",
    ]
    rationale: str
    attempt: int = 1
    max_attempts: int = 3


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
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not contract.acceptance_test_matrix:
        errors.append(_error("missing_acceptance_matrix", "Plan must define acceptance tests."))
    if not contract.task_slices:
        errors.append(_error("missing_task_slices", "Plan must emit at least one task slice."))
    if contract.finalization_policy != "open_final_feature_pr_for_human_merge":
        errors.append(
            _error("invalid_finalization_policy", "Final PR merge must remain human-gated.")
        )
    if contract.design_contract.acceptance_tests and not contract.acceptance_test_matrix:
        errors.append(_error("missing_acceptance_matrix", "Design acceptance tests need a matrix."))
    errors.extend(_validate_design_contract_drift(contract, origin_contract=origin_contract))
    errors.extend(_validate_lifecycle_acceptance(contract))
    for task_slice in contract.task_slices:
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
            contract.design_contract.public_api,
            contract.design_contract.ownership_boundaries,
            contract.design_contract.concurrency_protocol,
            contract.design_contract.persistence_protocol,
            " ".join(contract.design_contract.hard_constraints),
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
            "concurrency",
            "persistence",
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
