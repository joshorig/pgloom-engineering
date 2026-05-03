from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pgloom.models.cli import CLIModelProfile
from pydantic import BaseModel, Field

from pgloom_engineering.contracts import ImplementationTopology, PlanContract
from pgloom_engineering.path_policy import is_qa_write_path
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.planner.plan_summary import candidate_summary


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
        "QA verify slice presence",
        "blocking",
        "Plans must include an engineering.qa.verify slice after reviewers.",
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
        "check_small_feature_compactness",
        "Small-feature compactness",
        "blocking",
        "Small or single-surface roadmap items should use a compact handoff with limited slices.",
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
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> CriticVerdict:
        prompt = build_critic_prompt(plan, project_context, validator_errors)
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
        deterministic_results = deterministic_check_results(plan, validator_errors)
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
) -> list[CriticCheckResult]:
    checks: list[CriticCheckResult] = []
    for definition in RUBRIC_CHECKS:
        findings = _run_deterministic_check(definition, plan, validator_errors)
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
) -> CriticVerdict:
    deterministic_results = deterministic_check_results(plan, validator_errors)
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
) -> str:
    rubric = "\n".join(
        f"### {check.check_id}\nName: {check.name}\nSeverity: {check.severity_if_failed}\n"
        f"Rubric: {check.rubric}"
        for check in RUBRIC_CHECKS
    )
    return (
        Path(__file__).with_name("prompts").joinpath("critic.md").read_text(encoding="utf-8")
        + "\n\n"
        + rubric
        + "\n\nPLAN:\n"
        + json.dumps(candidate_summary(plan), indent=2, sort_keys=True)
        + "\n\nPROJECT_CONTEXT:\n"
        + json.dumps(_dump_context(project_context), indent=2, sort_keys=True, default=str)
        + "\n\nVALIDATOR_ERRORS:\n"
        + json.dumps(validator_errors, indent=2, sort_keys=True)
    )


def _run_deterministic_check(
    definition: CheckDefinition,
    plan: PlanContract,
    validator_errors: list[dict[str, Any]],
) -> list[CriticFinding]:
    check_id = definition.check_id
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
        return _qa_author_findings(definition, plan)
    if check_id == "check_qa_verify_present":
        return _qa_verify_findings(definition, plan)
    if check_id == "check_qa_paths_disjoint":
        return _qa_paths_disjoint_findings(definition, plan)
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
    if check_id == "check_small_feature_compactness":
        return _compactness_findings(definition, plan)
    return []


def _definition(check_id: str) -> CheckDefinition | None:
    for definition in RUBRIC_CHECKS:
        if definition.check_id == check_id:
            return definition
    return None


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


def _qa_author_findings(
    definition: CheckDefinition,
    plan: PlanContract,
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
        bad_paths = [path for path in author.allowed_paths if not is_qa_write_path(path)]
        if bad_paths:
            findings.append(
                _finding(
                    definition,
                    "qa_author_paths_not_restricted",
                    "QA author allowed_paths must be restricted to registered QA/test roots.",
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
) -> list[CriticFinding]:
    verifies = _task_type_slices(plan, "engineering.qa.verify")
    if not verifies:
        return [
            _finding(
                definition,
                "qa_verify_missing",
                "Missing engineering.qa.verify slice.",
            )
        ]
    findings: list[CriticFinding] = []
    reviewers = [item for item in plan.task_slices if item.role == "reviewer"]
    reviewer_ids = {item.slice_id for item in reviewers}
    for verify in verifies:
        bad_paths = [path for path in verify.allowed_paths if not is_qa_write_path(path)]
        if bad_paths:
            findings.append(
                _finding(
                    definition,
                    "qa_verify_paths_not_restricted",
                    "QA verify allowed_paths must be restricted to registered QA/test roots.",
                    verify.slice_id,
                )
            )
        commands = {_command_text(command) for command in verify.verification_commands}
        has_smoke = any("qa/smoke.sh" in command for command in commands)
        has_full = any(
            "qa/regression.sh" in command
            or "gradlew test" in command
            or "./gradlew test" in command
            for command in commands
        )
        if not has_smoke or not has_full:
            findings.append(
                _finding(
                    definition,
                    "qa_verify_missing_full_suite",
                    "QA verify must include smoke and full-suite/regression commands.",
                    verify.slice_id,
                )
            )
        for reviewer_id in reviewer_ids:
            if not _depends_on_transitively(plan, verify.slice_id, reviewer_id):
                findings.append(
                    _finding(
                        definition,
                        "qa_verify_not_after_reviewer",
                        "QA verify must depend on every reviewer slice.",
                        verify.slice_id,
                    )
                )
    return findings


def _qa_paths_disjoint_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    qa_slices = [
        item
        for item in plan.task_slices
        if item.task_type in {"engineering.qa.author", "engineering.qa.verify"}
    ]
    implementers = [item for item in plan.task_slices if item.role == "implementer"]
    for implementer in implementers:
        bad_paths = [path for path in implementer.allowed_paths if is_qa_write_path(path)]
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


def _compactness_findings(
    definition: CheckDefinition,
    plan: PlanContract,
) -> list[CriticFinding]:
    if not _is_small_feature(plan):
        return []
    findings: list[CriticFinding] = []
    if len(plan.task_slices) > 6:
        findings.append(
            _finding(
                definition,
                "small_feature_too_many_slices",
                "Small roadmap item should usually fit in 4-6 slices.",
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
    if role_counts["qa"] > 2:
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
