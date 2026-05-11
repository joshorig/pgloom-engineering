from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pgloom.models.cli import CLIModelProfile
from pydantic import BaseModel, Field

from pgloom_engineering.contracts import (
    ImplementationTopology,
    PlanContract,
    TaskSliceContract,
    canonical_acceptance_assertion_id,
)
from pgloom_engineering.path_policy import is_qa_write_path
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.planner.plan_summary import candidate_summary
from pgloom_engineering.role_gate_contracts import build_planner_gate_contract


class ModelProvider(Protocol):
    def invoke(
        self,
        *,
        profile: CLIModelProfile,
        prompt: str,
        input_tokens_hint: int | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> Any: ...


class CriticFinding(BaseModel):
    severity: Literal["blocking", "advisory"]
    check_id: str
    code: str
    slice_id: str | None = None
    message: str


class CriticCheckResult(BaseModel):
    check_id: str
    name: str
    passed: bool
    severity_if_failed: Literal["blocking", "advisory"]
    findings: list[CriticFinding] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    verdict: Literal["accept", "revise", "reject"]
    rationale: str
    findings: list[CriticFinding] = Field(default_factory=list)
    per_check_results: list[CriticCheckResult] = Field(default_factory=list)
    model_usage_id: int | None = None
    quality_report: PlanQualityReport | None = None


class PlanQualityReport(BaseModel):
    verdict: Literal["accept", "revise", "reject"]
    validator_error_count: int
    blocking_check_count: int
    advisory_check_count: int
    deterministic_blocking_findings: list[CriticFinding] = Field(default_factory=list)
    model_blocking_findings: list[CriticFinding] = Field(default_factory=list)
    score: int = Field(ge=0, le=100)


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    name: str
    severity_if_failed: Literal["blocking", "advisory"]
    rubric: str


RUBRIC_CHECKS: list[CheckDefinition] = [
    CheckDefinition(
        "check_design_contract_completeness",
        "Design contract completeness",
        "blocking",
        "Lifecycle work must define persistence and concurrency protocols.",
    ),
    CheckDefinition(
        "check_slice_path_coverage",
        "Slice path coverage",
        "blocking",
        "Every affected surface must be covered by slice allowed paths and tests.",
    ),
    CheckDefinition(
        "check_forbidden_path_overlap",
        "Forbidden-path overlap",
        "blocking",
        "No slice may include overlapping entries in its own allowed_paths and forbidden_paths.",
    ),
    CheckDefinition(
        "check_verification_commands",
        "Verification command coverage",
        "blocking",
        "Implementer and QA slices must define qa or Gradle verification commands.",
    ),
    CheckDefinition(
        "check_lifecycle_coverage",
        "Acceptance matrix lifecycle coverage",
        "blocking",
        "Lifecycle work must cover stale/invalid, invariant, and failure/partial cases.",
    ),
    CheckDefinition(
        "check_topology_consistency",
        "Implementation topology consistency",
        "blocking",
        "SINGLE topology is incompatible with multiple implementer slices.",
    ),
    CheckDefinition(
        "check_reviewer_present",
        "Reviewer slice presence",
        "blocking",
        "Plans must include at least one reviewer slice.",
    ),
    CheckDefinition(
        "check_qa_author_present",
        "QA author slice presence",
        "blocking",
        "Plans must include a test-first engineering.qa.author slice before implementers.",
    ),
    CheckDefinition(
        "check_qa_verify_present",
        "QA scrutiny + user-test slice presence",
        "blocking",
        "Plans must include engineering.qa.verify.scrutiny after reviewers and "
        "engineering.qa.verify.usertest after scrutiny unless metadata authorizes a skip.",
    ),
    CheckDefinition(
        "check_qa_paths_disjoint",
        "QA paths disjoint from source",
        "blocking",
        (
            "QA author/verify slices must write only tests or fixtures and stay "
            "disjoint from implementers."
        ),
    ),
    CheckDefinition(
        "check_role_write_path_hygiene",
        "Role write path hygiene",
        "blocking",
        (
            "Design slices must not claim production source, tests, or benchmark "
            "write paths; implementer slices must not claim docs or repo-memory "
            "write paths."
        ),
    ),
    CheckDefinition(
        "check_acceptance_assertion_coverage",
        "Acceptance assertion coverage",
        "blocking",
        "Every assertion is claimed by a slice and every slice claims an assertion.",
    ),
    CheckDefinition(
        "check_milestones_present",
        "Milestone contracts present",
        "blocking",
        "Plans must include milestone contracts with validation contracts.",
    ),
    CheckDefinition(
        "check_orphan_slices",
        "Orphan slice detection",
        "advisory",
        "Non-terminal slices should feed a later reviewer, QA, or historian slice.",
    ),
    CheckDefinition(
        "check_finalization_policy",
        "Finalization policy locked to human merge",
        "blocking",
        "Final feature PR merge must remain human-gated.",
    ),
    CheckDefinition(
        "check_objective_specificity",
        "Objective specificity",
        "advisory",
        "Slice objectives should name concrete artifacts, files, tests, or metrics.",
    ),
    CheckDefinition(
        "check_risk_register_present",
        "Risk register present",
        "advisory",
        "Lifecycle work should carry an explicit risk register.",
    ),
    CheckDefinition(
        "check_roadmap_dependency_handling",
        "Roadmap dependency handling",
        "blocking",
        (
            "Plans for dependency-gated roadmap items must block, narrow, or explicitly "
            "sequence prerequisites."
        ),
    ),
    CheckDefinition(
        "check_hot_path_invariants",
        "Hot-path invariant preservation",
        "blocking",
        (
            "Plans must not schedule work that violates stated zero-allocation or hot-path "
            "constraints."
        ),
    ),
    CheckDefinition(
        "check_behavioral_coverage_not_inventory_only",
        "Behavioral coverage not inventory-only",
        "blocking",
        (
            "Endpoint, route, prefix, filter, query, and benchmark acceptance must be "
            "proven by behavior cases, not only method, route, or build-file inventory."
        ),
    ),
    CheckDefinition(
        "check_small_feature_compactness",
        "Small-feature compactness",
        "blocking",
        "Small or single-surface roadmap items should use a compact handoff with limited slices.",
    ),
    CheckDefinition(
        "check_frozen_prefix_unchanged",
        "Frozen prefix unchanged",
        "blocking",
        "Operator replan-from-milestone must preserve frozen-prefix slices exactly.",
    ),
]


def compute_verdict(
    per_check_results: list[CriticCheckResult],
    validator_errors: list[dict[str, Any]],
) -> Literal["accept", "revise", "reject"]:
    if validator_errors:
        return "revise"
    for result in per_check_results:
        if not result.passed and result.severity_if_failed == "blocking":
            return "revise"
    return "accept"


class CriticRunner:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        profile_name: str,
        timeout_seconds: float = 300.0,
        command: list[str] | None = None,
    ) -> None:
        self._provider = provider
        self._profile = CLIModelProfile(
            name=profile_name,
            command=command or ["cat"],
            timeout_seconds=timeout_seconds,
            parse_response="text",
        )

    def review(
        self,
        *,
        plan: PlanContract,
        project_context: Any,
        validator_errors: list[dict[str, Any]],
        baseline_plan: PlanContract | None = None,
        replan_from_milestone_id: str | None = None,
        frozen_prefix_slice_ids: list[str] | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> CriticVerdict:
        prompt = build_critic_prompt(
            plan,
            project_context,
            validator_errors,
            baseline_plan=baseline_plan,
            replan_from_milestone_id=replan_from_milestone_id,
            frozen_prefix_slice_ids=frozen_prefix_slice_ids,
        )
        response = self._provider.invoke(
            profile=self._profile,
            prompt=prompt,
            workflow_id=workflow_id,
            task_id=task_id,
        )
        raw = str(getattr(response, "text", ""))
        try:
            payload = extract_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("critic response must be a JSON object")
        except Exception as exc:
            payload = {"rationale": f"critic response invalid: {exc}", "per_check_results": []}
        qa_write_paths = _context_qa_write_paths(project_context)
        deterministic_results = deterministic_check_results(
            plan,
            validator_errors,
            qa_write_paths=qa_write_paths,
            baseline_plan=baseline_plan,
            frozen_prefix_slice_ids=frozen_prefix_slice_ids,
        )
        results = normalize_check_results(payload.get("per_check_results"))
        results = reconcile_model_results_with_deterministic_checks(
            model_results=results,
            deterministic_results=deterministic_results,
        )
        results = enforce_deterministic_failures(
            model_results=results,
            deterministic_results=deterministic_results,
        )
        findings = [finding for result in results for finding in result.findings]
        verdict = compute_verdict(results, validator_errors)
        quality_report = build_plan_quality_report(
            verdict=verdict,
            validator_errors=validator_errors,
            model_results=results,
            deterministic_results=deterministic_results,
        )
        return CriticVerdict(
            verdict=verdict,
            rationale=str(payload.get("rationale") or _default_rationale(verdict, findings)),
            findings=findings,
            per_check_results=results,
            model_usage_id=getattr(response, "model_usage_id", None),
            quality_report=quality_report,
        )


def normalize_check_results(value: object) -> list[CriticCheckResult]:
    by_id: dict[str, CriticCheckResult] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            check_id = str(item.get("check_id") or "")
            if not check_id:
                continue
            definition = _definition(check_id)
            if definition is None:
                continue
            findings = item.get("findings")
            if not isinstance(findings, list):
                findings = []
            if item.get("passed") is False and not findings:
                findings = [
                    {
                        "severity": definition.severity_if_failed,
                        "check_id": definition.check_id,
                        "code": "critic_failed_check_without_finding",
                        "message": f"Critic failed {definition.check_id} without details.",
                    }
                ]
            item = {
                **item,
                "name": definition.name,
                "severity_if_failed": definition.severity_if_failed,
                "passed": bool(item.get("passed", not findings)),
                "findings": findings,
            }
            try:
                by_id[check_id] = CriticCheckResult.model_validate(item)
            except Exception:
                by_id[check_id] = CriticCheckResult(
                    check_id=definition.check_id,
                    name=definition.name,
                    passed=False,
                    severity_if_failed=definition.severity_if_failed,
                    findings=[
                        CriticFinding(
                            severity=definition.severity_if_failed,
                            check_id=definition.check_id,
                            code="critic_malformed_check_result",
                            message=f"Critic returned malformed result for {definition.check_id}.",
                        )
                    ],
                )
    results: list[CriticCheckResult] = []
    for definition in RUBRIC_CHECKS:
        result = by_id.get(definition.check_id)
        if result is None:
            result = CriticCheckResult(
                check_id=definition.check_id,
                name=definition.name,
                passed=False,
                severity_if_failed=definition.severity_if_failed,
                findings=[
                    CriticFinding(
                        severity=definition.severity_if_failed,
                        check_id=definition.check_id,
                        code="critic_did_not_evaluate_check",
                        message=f"Critic did not evaluate {definition.check_id}.",
                    )
                ],
            )
        results.append(result)
    return results


def reconcile_model_results_with_deterministic_checks(
    *,
    model_results: list[CriticCheckResult],
    deterministic_results: list[CriticCheckResult],
) -> list[CriticCheckResult]:
    deterministic_by_id = {item.check_id: item for item in deterministic_results}
    reconciled: list[CriticCheckResult] = []
    for result in model_results:
        deterministic = deterministic_by_id.get(result.check_id)
        if deterministic is None:
            reconciled.append(result)
            continue
        if (
            not result.passed
            and deterministic.passed
            and _only_unsupported_model_findings(result.findings)
        ):
            reconciled.append(
                result.model_copy(
                    update={
                        "passed": True,
                        "findings": [],
                    }
                )
            )
            continue
        reconciled.append(result)
    return reconciled


def enforce_deterministic_failures(
    *,
    model_results: list[CriticCheckResult],
    deterministic_results: list[CriticCheckResult],
) -> list[CriticCheckResult]:
    model_by_id = {item.check_id: item for item in model_results}
    enforced: list[CriticCheckResult] = []
    for deterministic in deterministic_results:
        model = model_by_id.get(deterministic.check_id)
        if model is None:
            enforced.append(deterministic)
            continue
        if deterministic.passed:
            enforced.append(model)
            continue
        findings = list(model.findings)
        deterministic_findings = [
            finding
            for finding in deterministic.findings
            if finding.model_dump(mode="json") not in [
                item.model_dump(mode="json") for item in findings
            ]
        ]
        findings.extend(deterministic_findings)
        enforced.append(
            model.model_copy(
                update={
                    "passed": False,
                    "findings": findings,
                }
            )
        )
    return enforced


def build_plan_quality_report(
    *,
    verdict: Literal["accept", "revise", "reject"],
    validator_errors: list[dict[str, Any]],
    model_results: list[CriticCheckResult],
    deterministic_results: list[CriticCheckResult],
) -> PlanQualityReport:
    deterministic_blockers = [
        finding
        for result in deterministic_results
        if not result.passed and result.severity_if_failed == "blocking"
        for finding in result.findings
    ]
    model_blockers = [
        finding
        for result in model_results
        if not result.passed and result.severity_if_failed == "blocking"
        for finding in result.findings
    ]
    advisory_count = len(
        [
            result
            for result in model_results
            if not result.passed and result.severity_if_failed == "advisory"
        ]
    )
    blocking_count = len(
        [
            result
            for result in model_results
            if not result.passed and result.severity_if_failed == "blocking"
        ]
    )
    score = max(
        0,
        100
        - len(validator_errors) * 20
        - len(deterministic_blockers) * 15
        - max(0, len(model_blockers) - len(deterministic_blockers)) * 10
        - advisory_count * 3,
    )
    if verdict != "accept":
        score = min(score, 79)
    return PlanQualityReport(
        verdict=verdict,
        validator_error_count=len(validator_errors),
        blocking_check_count=blocking_count,
        advisory_check_count=advisory_count,
        deterministic_blocking_findings=deterministic_blockers,
        model_blocking_findings=model_blockers,
        score=score,
    )


def deterministic_check_results(
    plan: PlanContract,
    validator_errors: list[dict[str, Any]],
    *,
    qa_write_paths: list[str] | None = None,
    baseline_plan: PlanContract | None = None,
    frozen_prefix_slice_ids: list[str] | None = None,
) -> list[CriticCheckResult]:
    checks: list[CriticCheckResult] = []
    for definition in RUBRIC_CHECKS:
        findings = _run_deterministic_check(
            definition,
            plan,
            validator_errors,
            qa_write_paths=qa_write_paths,
            baseline_plan=baseline_plan,
            frozen_prefix_slice_ids=frozen_prefix_slice_ids,
        )
        checks.append(
            CriticCheckResult(
                check_id=definition.check_id,
                name=definition.name,
                passed=not findings,
                severity_if_failed=definition.severity_if_failed,
                findings=findings,
            )
        )
    return checks


def deterministic_accept_verdict(
    *,
    plan: PlanContract,
    validator_errors: list[dict[str, Any]],
    rationale: str,
    preempted: bool = False,
    qa_write_paths: list[str] | None = None,
    baseline_plan: PlanContract | None = None,
    frozen_prefix_slice_ids: list[str] | None = None,
) -> CriticVerdict:
    deterministic_results = deterministic_check_results(
        plan,
        validator_errors,
        qa_write_paths=qa_write_paths,
        baseline_plan=baseline_plan,
        frozen_prefix_slice_ids=frozen_prefix_slice_ids,
    )
    verdict = compute_verdict(deterministic_results, validator_errors)
    quality_report = build_plan_quality_report(
        verdict=verdict,
        validator_errors=validator_errors,
        model_results=deterministic_results,
        deterministic_results=deterministic_results,
    )
    return CriticVerdict(
        verdict=verdict,
        rationale=rationale,
        findings=[finding for result in deterministic_results for finding in result.findings],
        per_check_results=deterministic_results,
        model_usage_id=None,
        quality_report=quality_report,
    ).model_copy(
        update={
            "rationale": (
                f"{rationale} (preempted_model_critic={str(preempted).lower()})"
            )
        }
    )


def _only_unsupported_model_findings(findings: list[CriticFinding]) -> bool:
    if not findings:
        return True
    unsupported_codes = {
        "critic_failed_check_without_finding",
        "critic_did_not_evaluate_check",
    }
    return all(_finding_code(finding) in unsupported_codes for finding in findings)


def _finding_code(finding: CriticFinding | dict[str, Any]) -> str:
    if isinstance(finding, dict):
        return str(finding.get("code") or "")
    return finding.code


def build_critic_prompt(
    plan: PlanContract,
    project_context: Any,
    validator_errors: list[dict[str, Any]],
    *,
    baseline_plan: PlanContract | None = None,
    replan_from_milestone_id: str | None = None,
    frozen_prefix_slice_ids: list[str] | None = None,
) -> str:
    rubric = "\n".join(
        f"### {check.check_id}\nName: {check.name}\nSeverity: {check.severity_if_failed}\n"
        f"Rubric: {check.rubric}"
        for check in RUBRIC_CHECKS
    )
    role_gate_contract = build_planner_gate_contract(
        project_context=project_context,
        planner_rubric=[
            {
                "check_id": check.check_id,
                "name": check.name,
                "severity_if_failed": check.severity_if_failed,
                "rubric": check.rubric,
            }
            for check in RUBRIC_CHECKS
        ],
    )
    return (
        Path(__file__).with_name("prompts").joinpath("critic.md").read_text(encoding="utf-8")
        + "\n\n"
        + rubric
        + "\n\nPLAN:\n"
        + json.dumps(candidate_summary(plan), indent=2, sort_keys=True)
        + "\n\nROLE_GATE_CONTRACT:\n"
        + json.dumps(role_gate_contract, indent=2, sort_keys=True, default=str)
        + "\n\nPROJECT_CONTEXT:\n"
        + json.dumps(_dump_context(project_context), indent=2, sort_keys=True, default=str)
        + "\n\nVALIDATOR_ERRORS:\n"
        + json.dumps(validator_errors, indent=2, sort_keys=True)
        + "\n\nINHERIT_BASELINE_MODE:\n"
        + json.dumps(
            _baseline_payload(
                baseline_plan,
                replan_from_milestone_id,
                frozen_prefix_slice_ids,
            ),
            indent=2,
            sort_keys=True,
        )
    )


def _run_deterministic_check(
    definition: CheckDefinition,
    plan: PlanContract,
    validator_errors: list[dict[str, Any]],
    *,
    qa_write_paths: list[str] | None = None,
    baseline_plan: PlanContract | None = None,
    frozen_prefix_slice_ids: list[str] | None = None,
) -> list[CriticFinding]:
    check_id = definition.check_id
    if check_id == "check_frozen_prefix_unchanged":
        return _frozen_prefix_findings(
            definition,
            plan,
            baseline_plan=baseline_plan,
            frozen_prefix_slice_ids=frozen_prefix_slice_ids,
        )
    if check_id == "check_design_contract_completeness":
        text = _plan_text(plan)
        if _is_lifecycle_text(text) and (
            not plan.design_contract.persistence_protocol
            or not plan.design_contract.concurrency_protocol
        ):
            return [
                _finding(
                    definition,
                    "design_protocol_missing",
                    "Lifecycle plan lacks protocols.",
                )
            ]
    if check_id == "check_verification_commands":
        bad = [
            task_slice.slice_id
            for task_slice in plan.task_slices
            if task_slice.role in {"implementer", "qa"} and not task_slice.verification_commands
        ]
        return [
            _finding(definition, "verification_missing", "Missing verification.", item)
            for item in bad
        ]
    if check_id == "check_forbidden_path_overlap":
        return _forbidden_path_overlap_findings(definition, plan)
    if check_id == "check_lifecycle_coverage":
        if any(error.get("code") == "planner_contract_incomplete" for error in validator_errors):
            return [
                _finding(
                    definition,
                    "lifecycle_coverage_missing",
                    "Missing lifecycle coverage.",
                )
            ]
    if check_id == "check_topology_consistency":
        implementers = [item for item in plan.task_slices if item.role == "implementer"]
        if plan.implementation_topology == ImplementationTopology.SINGLE and len(implementers) > 1:
            return [
                _finding(
                    definition,
                    "single_topology_conflict",
                    "SINGLE topology has multiple implementers.",
                )
            ]
    if check_id == "check_reviewer_present":
        if not any(item.role == "reviewer" for item in plan.task_slices):
            return [
                _finding(definition, "reviewer_slice_missing", "Missing reviewer slice.")
            ]
    if check_id == "check_qa_author_present":
        return _qa_author_findings(definition, plan, qa_write_paths=qa_write_paths)
    if check_id == "check_qa_verify_present":
        return _qa_verify_findings(definition, plan, qa_write_paths=qa_write_paths)
    if check_id == "check_qa_paths_disjoint":
        return _qa_paths_disjoint_findings(definition, plan, qa_write_paths=qa_write_paths)
    if check_id == "check_role_write_path_hygiene":
        return _role_write_path_hygiene_findings(definition, plan)
    if check_id == "check_acceptance_assertion_coverage":
        return _acceptance_assertion_findings(definition, plan)
    if check_id == "check_milestones_present":
        return _milestone_findings(definition, plan)
    if check_id == "check_finalization_policy":
        if plan.finalization_policy != "open_final_feature_pr_for_human_merge":
            return [
                _finding(
                    definition,
                    "finalization_not_human_gated",
                    "Finalization must be human-gated.",
                )
            ]
    if check_id == "check_risk_register_present":
        if _is_lifecycle_text(_plan_text(plan)) and not plan.risk_register:
            return [
                _finding(
                    definition,
                    "risk_register_empty",
                    "Lifecycle plan has empty risk register.",
                )
            ]
    if check_id == "check_roadmap_dependency_handling":
        return _roadmap_dependency_findings(definition, plan)
    if check_id == "check_hot_path_invariants":
        return _hot_path_findings(definition, plan)
    if check_id == "check_behavioral_coverage_not_inventory_only":
        return _inventory_only_findings(definition, plan)
    if check_id == "check_small_feature_compactness":
        return _compactness_findings(definition, plan)
    return []


def _frozen_prefix_findings(
    definition: CheckDefinition,
    plan: PlanContract,
    *,
    baseline_plan: PlanContract | None,
    frozen_prefix_slice_ids: list[str] | None,
) -> list[CriticFinding]:
    if baseline_plan is None or not frozen_prefix_slice_ids:
        return []
    findings: list[CriticFinding] = []
    current_by_id = {item.slice_id: item for item in plan.task_slices}
    baseline_by_id = {item.slice_id: item for item in baseline_plan.task_slices}
    for slice_id in frozen_prefix_slice_ids:
        baseline_slice = baseline_by_id.get(slice_id)
        current_slice = current_by_id.get(slice_id)
        if baseline_slice is None:
            continue
        if current_slice is None:
            findings.append(
                _finding(
                    definition,
                    "frozen_prefix_slice_missing",
                    f"Frozen-prefix slice {slice_id} is missing from replanned contract.",
                    slice_id,
                )
            )
            continue
        baseline_text = json.dumps(
            baseline_slice.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        current_text = json.dumps(
            current_slice.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        if baseline_text != current_text:
            findings.append(
                _finding(
                    definition,
                    "frozen_prefix_slice_changed",
                    f"Frozen-prefix slice {slice_id} changed during milestone replan.",
                    slice_id,
                )
            )
    return findings


def _definition(check_id: str) -> CheckDefinition | None:
    for definition in RUBRIC_CHECKS:
        if definition.check_id == check_id:
            return definition
    return None


def _baseline_payload(
    baseline_plan: PlanContract | None,
    replan_from_milestone_id: str | None,
    frozen_prefix_slice_ids: list[str] | None,
) -> dict[str, Any]:
    if baseline_plan is None or not replan_from_milestone_id:
        return {"enabled": False}
    frozen = set(frozen_prefix_slice_ids or [])
    return {
        "enabled": True,
        "replan_from_milestone_id": replan_from_milestone_id,
        "frozen_prefix_slice_ids": sorted(frozen),
        "baseline_frozen_slices": [
            item.model_dump(mode="json")
            for item in baseline_plan.task_slices
            if item.slice_id in frozen
        ],
    }


def _finding(
    definition: CheckDefinition,
    code: str,
    message: str,
    slice_id: str | None = None,
) -> CriticFinding:
    return CriticFinding(
        severity=definition.severity_if_failed,
        check_id=definition.check_id,
        code=code,
        slice_id=slice_id,
        message=message,
    )


def _default_rationale(verdict: str, findings: list[CriticFinding]) -> str:
    if verdict == "accept":
        return "All blocking rubric checks passed."
    return f"{len(findings)} rubric finding(s) require revision."


def _dump_context(project_context: Any) -> dict[str, Any]:
    if hasattr(project_context, "model_dump"):
        dumped = project_context.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    return {}


def _context_qa_write_paths(project_context: Any) -> list[str] | None:
    paths = getattr(project_context, "qa_write_paths", None)
    if isinstance(paths, list):
        return [str(path) for path in paths]
    return None


def _plan_text(plan: PlanContract) -> str:
    return json.dumps(plan.model_dump(mode="json", exclude={"council_reports"}), sort_keys=True)


def _is_lifecycle_text(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ["store", "restore", "persist", "resume", "snapshot"])


def _roadmap_dependency_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    text = _plan_text(plan).lower()
    is_replication = "r-006" in text or "replication" in text or "standby" in text
    is_compression = "r-004" in text or "compression" in text or "lz4" in text
    if not (is_replication or is_compression):
        return []
    dependency_terms = [
        "r-002",
        "snapshot prerequisite",
        "blocked by snapshot",
        "blocked by r-002",
        "prerequisite blocker",
    ]
    if any(term in text for term in dependency_terms):
        return []
    return [
        _finding(
            definition,
            "roadmap_dependency_missing",
            "Dependency-gated roadmap item does not explicitly handle R-002/snapshot prerequisite.",
        )
    ]


def _hot_path_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    text = _plan_text(plan).lower()
    if "proxy.newproxyinstance" in text or "invocationhandler" in text:
        return [
            _finding(
                definition,
                "benchmark_allocating_indirection",
                "Benchmark plan uses reflection proxy/InvocationHandler in a hot measured path.",
            )
        ]
    if _hot_path_shared_api_requires_wrapper_coverage(plan):
        return [
            _finding(
                definition,
                "hot_path_wrapper_coverage_missing",
                (
                    "Hot-path shared API plan must cover wrappers, decorators, "
                    "adapters, or delegating implementations that implement the "
                    "same contract."
                ),
            )
        ]
    if "compression" not in text and "lz4" not in text:
        return []
    hot_path_violation_terms = [
        "compress on publish",
        "compression on publish",
        "publish compression",
        "hot publish compression",
        "allocate on publish",
        "allocating on publish",
    ]
    if not any(term in text for term in hot_path_violation_terms):
        return []
    return [
        _finding(
            definition,
            "hot_path_constraint_violation",
            "Compression plan appears to put compression/allocation on the publish hot path.",
        )
    ]


def _hot_path_shared_api_requires_wrapper_coverage(plan: PlanContract) -> bool:
    text = _plan_semantic_text(plan).lower()
    hot_path_terms = (
        "zero-allocation",
        "zero allocation",
        "hot-path",
        "hot path",
        "allocation gate",
        "alloc gate",
    )
    shared_api_terms = (
        "interface",
        "shared api",
        "public api",
        "api contract",
        "api addition",
    )
    wrapper_terms = (
        "wrapper",
        "wrappers",
        "decorator",
        "decorators",
        "adapter",
        "adapters",
        "delegating",
        "forwarding",
    )
    return (
        any(term in text for term in hot_path_terms)
        and any(term in text for term in shared_api_terms)
        and not any(
            re.search(rf"\b{re.escape(term)}\b", text) for term in wrapper_terms
        )
    )


def _plan_semantic_text(plan: PlanContract) -> str:
    parts: list[str] = [
        plan.problem_statement,
        plan.design_contract.public_api,
        plan.design_contract.ownership_boundaries,
        plan.design_contract.concurrency_protocol,
        plan.design_contract.persistence_protocol,
        " ".join(plan.design_contract.acceptance_tests),
        " ".join(plan.acceptance_test_matrix),
        " ".join(plan.risk_register),
    ]
    for task_slice in plan.task_slices:
        parts.extend(
            [
                task_slice.objective,
                " ".join(task_slice.expected_outputs),
                " ".join(task_slice.grading_criteria),
                json.dumps(task_slice.validation_strategy, sort_keys=True),
            ]
        )
    return " ".join(part for part in parts if part)


def _inventory_only_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    inventory_terms = [
        "overload present",
        "overload presence",
        "method present",
        "method exists",
        "route list",
        "build file string",
        "string-checking",
    ]
    behavior_domains = ["prefix", "filter", "query", "route", "endpoint"]
    for task_slice in plan.task_slices:
        text = json.dumps(task_slice.model_dump(mode="json"), sort_keys=True).lower()
        if not any(domain in text for domain in behavior_domains):
            continue
        if not any(term in text for term in inventory_terms):
            continue
        if "matching" in text and "non-matching" in text:
            continue
        findings.append(
            _finding(
                definition,
                "inventory_only_behavior_coverage",
                (
                    "Behavior acceptance relies on inventory/presence checks "
                    "without matching and non-matching cases."
                ),
                task_slice.slice_id,
            )
        )
    return findings


def _qa_author_findings(
    definition: CheckDefinition,
    plan: PlanContract,
    *,
    qa_write_paths: list[str] | None = None,
) -> list[CriticFinding]:
    authors = _task_type_slices(plan, "engineering.qa.author")
    if not authors:
        return [
            _finding(
                definition,
                "qa_author_missing",
                "Missing engineering.qa.author slice.",
            )
        ]
    findings: list[CriticFinding] = []
    implementers = [item for item in plan.task_slices if item.role == "implementer"]
    author_ids = {item.slice_id for item in authors}
    for author in authors:
        bad_paths = [
            path for path in author.allowed_paths if not is_qa_write_path(path, qa_write_paths)
        ]
        if bad_paths:
            findings.append(
                _finding(
                    definition,
                    "qa_author_paths_not_restricted",
                    "QA author allowed_paths must be restricted to registered QA/test roots.",
                    author.slice_id,
                )
            )
        if _qa_author_needs_benchmark_root(author):
            findings.append(
                _finding(
                    definition,
                    "qa_benchmark_output_path_not_allowed",
                    "QA author expected benchmark/JMH artifacts but allowed_paths omit a "
                    "benchmark QA root.",
                    author.slice_id,
                )
            )
    for implementer in implementers:
        if not any(
            _depends_on_transitively(plan, implementer.slice_id, author)
            for author in author_ids
        ):
            findings.append(
                _finding(
                    definition,
                    "qa_author_not_before_implementer",
                    "Every implementer slice must depend on a QA author slice.",
                    implementer.slice_id,
                )
            )
    return findings


def _qa_author_needs_benchmark_root(author: TaskSliceContract) -> bool:
    text_parts: list[str] = []
    for attr in [
        "objective",
        "expected_outputs",
        "verification_commands",
        "grading_criteria",
        "required_procedures",
    ]:
        value = getattr(author, attr, None)
        if isinstance(value, str):
            text_parts.append(value)
        elif isinstance(value, list):
            text_parts.extend(str(item) for item in value)
        elif value is not None:
            text_parts.append(str(value))
    text = " ".join(text_parts).lower()
    if not any(
        token in text
        for token in [
            "src/jmh",
            "benchmark file",
            "benchmark class",
            "benchmark stub",
            "benchmark source",
            "jmh file",
            "jmh class",
            "jmh stub",
            "jmh source",
        ]
    ):
        return False
    return not any(_looks_like_benchmark_root(path) for path in author.allowed_paths)


def _looks_like_benchmark_root(path: str) -> bool:
    lowered = path.lower()
    return "benchmark" in lowered or "src/jmh" in lowered or "/jmh/" in lowered


def _forbidden_path_overlap_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    for task_slice in plan.task_slices:
        if _paths_overlap(task_slice.allowed_paths, task_slice.forbidden_paths):
            findings.append(
                _finding(
                    definition,
                    "slice_allowed_forbidden_overlap",
                    "Slice allowed_paths overlap its own forbidden_paths.",
                    task_slice.slice_id,
                )
            )
    return findings


def _qa_verify_findings(
    definition: CheckDefinition,
    plan: PlanContract,
    *,
    qa_write_paths: list[str] | None = None,
) -> list[CriticFinding]:
    scrutinies = _task_type_slices(plan, "engineering.qa.verify.scrutiny")
    usertests = _task_type_slices(plan, "engineering.qa.verify.usertest")
    if not scrutinies:
        return [
            _finding(
                definition,
                "qa_scrutiny_missing",
                "Missing engineering.qa.verify.scrutiny slice.",
            )
        ]
    findings: list[CriticFinding] = []
    reviewers = [item for item in plan.task_slices if item.role == "reviewer"]
    reviewer_ids = {item.slice_id for item in reviewers}
    for verify in [*scrutinies, *usertests]:
        bad_paths = [
            path for path in verify.allowed_paths if not is_qa_write_path(path, qa_write_paths)
        ]
        if bad_paths:
            findings.append(
                _finding(
                    definition,
                    "qa_verify_paths_not_restricted",
                    "QA validator allowed_paths must be restricted to registered QA/test roots.",
                    verify.slice_id,
                )
            )
    for verify in scrutinies:
        commands = {_command_text(command) for command in verify.verification_commands}
        has_benchmark_smoke = any(
            _is_feature_benchmark_smoke_command(command) for command in commands
        )
        has_periodic_regression = any(
            "qa/regression.sh" in command
            or command.endswith(":benchmarks:jmh")
            or ":benchmarks:jmh " in command
            for command in commands
        )
        has_bare_project_gate = any(
            _is_bare_gradle_project_gate(command) for command in commands
        )
        has_lint_or_style = any(_is_feature_lint_command(command) for command in commands)
        has_build_or_compile = any(_is_feature_build_command(command) for command in commands)
        has_feature_test = any(_is_feature_test_command(command) for command in commands)
        if has_periodic_regression:
            findings.append(
                _finding(
                    definition,
                    "qa_verify_uses_periodic_regression_gate",
                    (
                        "Feature QA scrutiny must not run full regression/JMH sweeps; "
                        "reserve qa/regression.sh for project-scheduled periodic validation."
                    ),
                    verify.slice_id,
                )
            )
        if has_bare_project_gate:
            findings.append(
                _finding(
                    definition,
                    "qa_verify_uses_broad_project_check",
                    (
                        "Feature QA scrutiny must not run broad project smoke or bare "
                        "./gradlew test/check; use scoped compile/lint/build commands "
                        "plus feature-specific tests and direct benchmark smoke."
                    ),
                    verify.slice_id,
                )
            )
        if (
            not has_benchmark_smoke
            or not has_lint_or_style
            or not has_build_or_compile
            or not has_feature_test
        ):
            findings.append(
                _finding(
                    definition,
                    "qa_verify_missing_feature_validation",
                    (
                        "QA scrutiny must include direct benchmark-smoke plus "
                        "feature-specific lint/build/test commands; do not rely on "
                        "broad project smoke scripts as the benchmark gate."
                    ),
                    verify.slice_id,
                )
            )
        for reviewer_id in reviewer_ids:
            if not _depends_on_transitively(plan, verify.slice_id, reviewer_id):
                findings.append(
                    _finding(
                        definition,
                        "qa_verify_not_after_reviewer",
                        "QA scrutiny must depend on every reviewer slice.",
                        verify.slice_id,
                    )
                )
    if not usertests:
        findings.append(
            _finding(
                definition,
                "qa_usertest_missing",
                "Missing engineering.qa.verify.usertest slice after scrutiny.",
            )
        )
    for usertest in usertests:
        commands = {_command_text(command) for command in usertest.verification_commands}
        broad_commands = [
            command
            for command in commands
            if _is_broad_usertest_gate(command)
        ]
        if broad_commands:
            findings.append(
                _finding(
                    definition,
                    "qa_usertest_uses_broad_gate",
                    (
                        "QA user-test must exercise the feature through a user-facing "
                        "CLI/API/browser/app flow, not substitute qa/smoke.sh, "
                        "benchmark-smoke gates, full regression, or bare project checks."
                    ),
                    usertest.slice_id,
                )
            )
        for scrutiny in scrutinies:
            if not _depends_on_transitively(plan, usertest.slice_id, scrutiny.slice_id):
                findings.append(
                    _finding(
                        definition,
                        "qa_usertest_not_after_scrutiny",
                        "QA user-test must depend on QA scrutiny.",
                        usertest.slice_id,
                    )
                )
    return findings


def _is_feature_lint_command(command: str) -> bool:
    lowered = command.lower()
    return any(
        marker in lowered
        for marker in [
            "checkstyle",
            "spotbugs",
            "pmd",
            "ktlint",
            " detekt",
            ":detekt",
            "eslint",
            "ruff",
            "clippy",
            "golangci-lint",
        ]
    )


def _is_feature_build_command(command: str) -> bool:
    lowered = command.lower()
    return (
        "gradlew" in lowered
        and (
            ":compile" in lowered
            or " compile" in lowered
            or "assemble" in lowered
            or ":classes" in lowered
        )
    ) or any(
        marker in lowered
        for marker in [
            "mvn -q -dskiptests compile",
            "cargo build",
            "go test -run '^$'",
            "tsc ",
            "npm run build",
        ]
    )


def _is_feature_test_command(command: str) -> bool:
    lowered = command.lower()
    return (
        "gradlew" in lowered
        and (
            ":test" in lowered
            or " test " in lowered
            or lowered.endswith(" test")
        )
        and (
            "--tests" in lowered
            or "testsingle" in lowered
            or ".test" in lowered
        )
    ) or any(
        marker in lowered
        for marker in [
            "pytest ",
            "pytest\t",
            "vitest",
            "jest ",
            "go test ./",
            "cargo test",
        ]
    )


def _is_broad_usertest_gate(command: str) -> bool:
    return (
        "qa/smoke.sh" in command
        or "qa/regression.sh" in command
        or ":benchmarks:jmhSmokeCheck" in command
        or command.endswith(":benchmarks:jmh")
        or ":benchmarks:jmh " in command
        or _is_bare_gradle_project_gate(command)
    )


def _is_bare_gradle_project_gate(command: str) -> bool:
    if "qa/smoke.sh" in command:
        return True
    parts = command.split()
    if not parts:
        return False
    if parts[0] not in {"./gradlew", "gradlew"}:
        return False
    meaningful = [
        part
        for part in parts[1:]
        if part not in {"--no-daemon", "--console=plain", "--console", "plain"}
    ]
    return meaningful in (["check"], ["test"])


def _is_feature_benchmark_smoke_command(command: str) -> bool:
    lowered = command.lower()
    return (
        ":benchmarks:jmhsmokecheck" in lowered
        or "jmhsmokecheck" in lowered
        or "benchmark-smoke" in lowered
        or "benchmark smoke" in lowered
    )


def _qa_paths_disjoint_findings(
    definition: CheckDefinition,
    plan: PlanContract,
    *,
    qa_write_paths: list[str] | None = None,
) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    qa_slices = [
        item
        for item in plan.task_slices
        if item.task_type
        in {
            "engineering.qa.author",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }
    ]
    implementers = [item for item in plan.task_slices if item.role == "implementer"]
    for implementer in implementers:
        bad_paths = [
            path for path in implementer.allowed_paths if is_qa_write_path(path, qa_write_paths)
        ]
        if bad_paths:
            findings.append(
                _finding(
                    definition,
                    "implementer_claims_qa_paths",
                    "Implementer slices may not write registered QA/test roots.",
                    implementer.slice_id,
                )
            )
    for qa_slice in qa_slices:
        for implementer in implementers:
            if _paths_overlap(qa_slice.allowed_paths, implementer.allowed_paths):
                findings.append(
                    _finding(
                        definition,
                        "qa_paths_overlap_implementer",
                        "QA write paths overlap implementer source paths.",
                        qa_slice.slice_id,
                )
            )
    return findings


def _role_write_path_hygiene_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.task_type == "engineering.design":
            bad_paths = [
                path
                for path in task_slice.allowed_paths
                if _is_source_path(path) or _is_test_or_benchmark_path(path)
            ]
            if bad_paths:
                findings.append(
                    _finding(
                        definition,
                        "design_claims_execution_paths",
                        (
                            "Design slices may name source/test/benchmark files in the "
                            "objective or expected outputs, but allowed_paths must stay "
                            "limited to design documentation paths."
                        ),
                        task_slice.slice_id,
                    )
                )
        if task_slice.role == "implementer":
            bad_paths = [
                path for path in task_slice.allowed_paths if _is_docs_or_memory_path(path)
            ]
            if bad_paths:
                findings.append(
                    _finding(
                        definition,
                        "implementer_claims_docs_or_memory_paths",
                        (
                            "Implementer slices must not write docs or repo-memory; "
                            "documentation/status updates belong to design or final "
                            "human-gated follow-up work."
                        ),
                        task_slice.slice_id,
                    )
                )
    return findings


def _is_source_path(path: str) -> bool:
    normalized = _normalize_path(path).lower()
    return "/src/main/" in normalized or normalized.endswith("/src/main/")


def _is_test_or_benchmark_path(path: str) -> bool:
    normalized = _normalize_path(path).lower()
    return (
        "/src/test/" in normalized
        or normalized.endswith("/src/test/")
        or normalized.startswith("tests/")
        or normalized.startswith("qa/")
        or "benchmark" in normalized
    )


def _is_docs_or_memory_path(path: str) -> bool:
    normalized = _normalize_path(path).lower()
    return normalized.startswith("docs/") or normalized.startswith("repo-memory/")


def _acceptance_assertion_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    assertion_labels = {
        canonical_acceptance_assertion_id(assertion): assertion
        for assertion in plan.acceptance_assertions
    }
    for milestone in plan.milestones:
        assertion_labels.update(
            {
                canonical_acceptance_assertion_id(assertion): assertion
                for assertion in milestone.acceptance_assertions
            }
        )
    findings: list[CriticFinding] = []
    if not assertion_labels:
        return [
            _finding(
                definition,
                "acceptance_assertions_missing",
                "Plan must define acceptance assertions.",
            )
        ]
    claimed: set[str] = set()
    for task_slice in plan.task_slices:
        if not task_slice.acceptance_assertion_ids:
            findings.append(
                _finding(
                    definition,
                    "slice_missing_acceptance_assertion",
                    "Task slice must claim at least one acceptance assertion.",
                    task_slice.slice_id,
                )
            )
        claimed.update(
            canonical_acceptance_assertion_id(assertion)
            for assertion in task_slice.acceptance_assertion_ids
        )
    for assertion in sorted(assertion_labels.keys() - claimed):
        findings.append(
            _finding(
                definition,
                "acceptance_assertion_unclaimed",
                "Acceptance assertion is not claimed by any slice: "
                f"{assertion_labels[assertion]}",
            )
        )
    return findings


def _milestone_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    if not plan.milestones:
        return [
            _finding(
                definition,
                "milestones_missing",
                "Plan must define milestone contracts.",
            )
        ]
    slice_ids = {task_slice.slice_id for task_slice in plan.task_slices}
    slice_type_by_id = {
        task_slice.slice_id: task_slice.task_type for task_slice in plan.task_slices
    }
    findings: list[CriticFinding] = []
    for milestone in plan.milestones:
        if not milestone.validation_contract:
            findings.append(
                _finding(
                    definition,
                    "milestone_validation_contract_missing",
                    "Milestone must carry a validation contract.",
                    milestone.milestone_id,
                )
            )
        slice_types = {slice_type_by_id.get(slice_id) for slice_id in milestone.slice_ids}
        if (
            milestone.signoff_policy == "scrutiny_and_usertest"
            and (
                "engineering.qa.verify.scrutiny" not in slice_types
                or "engineering.qa.verify.usertest" not in slice_types
            )
        ):
            findings.append(
                _finding(
                    definition,
                    "milestone_validator_signoff_unachievable",
                    (
                        "Milestone requires scrutiny/usertest signoff but does not "
                        "contain both validator slices."
                    ),
                    milestone.milestone_id,
                )
            )
        if (
            milestone.signoff_policy == "scrutiny_only"
            and "engineering.qa.verify.scrutiny" not in slice_types
        ):
            findings.append(
                _finding(
                    definition,
                    "milestone_validator_signoff_unachievable",
                    "Milestone requires scrutiny signoff but contains no scrutiny validator.",
                    milestone.milestone_id,
                )
            )
        missing = [slice_id for slice_id in milestone.slice_ids if slice_id not in slice_ids]
        if missing:
            findings.append(
                _finding(
                    definition,
                    "milestone_unknown_slice",
                    "Milestone references unknown slices: " + ", ".join(missing),
                    milestone.milestone_id,
                )
            )
    return findings


def _compactness_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    if not _is_small_feature(plan):
        return []
    findings: list[CriticFinding] = []
    if len(plan.task_slices) > 7:
        findings.append(
            _finding(
                definition,
                "small_feature_too_many_slices",
                "Small roadmap item should usually fit in 5-7 slices.",
            )
        )
    role_counts = {
        role: len([item for item in plan.task_slices if item.role == role])
        for role in {"designer", "implementer", "reviewer", "qa", "historian"}
    }
    if role_counts["reviewer"] > 1:
        findings.append(
            _finding(
                definition,
                "small_feature_too_many_reviewers",
                "Small roadmap item should use one reviewer slice.",
            )
        )
    if role_counts["qa"] > 3:
        findings.append(
            _finding(
                definition,
                "small_feature_too_many_qa_slices",
                "Small roadmap item should combine QA gates unless there is a clear risk split.",
            )
        )
    if role_counts["historian"] > 0 and not _requires_history_update(plan):
        findings.append(
            _finding(
                definition,
                "small_feature_unnecessary_historian",
                (
                    "Small roadmap item should fold final notes into QA/review unless "
                    "docs must change."
                ),
            )
        )
    return findings


def _task_type_slices(plan: PlanContract, task_type: str) -> list[Any]:
    return [item for item in plan.task_slices if item.task_type == task_type]


def _depends_on_transitively(plan: PlanContract, slice_id: str, dependency_id: str) -> bool:
    by_id = {item.slice_id: item for item in plan.task_slices}
    task_slice = by_id.get(slice_id)
    if task_slice is None:
        return False
    seen: set[str] = set()
    stack = list(task_slice.depends_on)
    while stack:
        current = stack.pop()
        if current == dependency_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        parent = by_id.get(current)
        if parent is not None:
            stack.extend(parent.depends_on)
    return False


def _paths_overlap(left: list[str], right: list[str]) -> bool:
    for left_path in left:
        for right_path in right:
            if _path_overlaps(left_path, right_path):
                return True
    return False


def _path_overlaps(left: str, right: str) -> bool:
    left_normalized = _normalize_path(left)
    right_normalized = _normalize_path(right)
    return left_normalized.startswith(right_normalized) or right_normalized.startswith(
        left_normalized
    )


def _normalize_path(path: str) -> str:
    stripped = path.strip()
    if stripped in {"", "."}:
        return ""
    return stripped.rstrip("/") + "/"


def _command_text(command: list[str]) -> str:
    return " ".join(command)


def _is_small_feature(plan: PlanContract) -> bool:
    text = " ".join(
        [
            plan.problem_statement,
            plan.design_contract.public_api,
            " ".join(plan.design_contract.acceptance_tests),
            " ".join(plan.acceptance_test_matrix),
        ]
    ).lower()
    if any(term in text for term in ["distributed", "replication", "snapshot", "restore"]):
        return False
    small_terms = [
        "range-query",
        "range query",
        "config + diagnostics",
        "diagnostics parity",
        "yaml graph topology loader",
        "per-node metrics exporter",
        "json export",
        "visualizer",
    ]
    return any(term in text for term in small_terms)


def _requires_history_update(plan: PlanContract) -> bool:
    text = _plan_text(plan).lower()
    return any(term in text for term in ["decisions.md", "roadmap.md", "current_state.md"])
