from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pgloom_engineering.contracts import PlanContract, TaskSliceContract

Severity = Literal["blocking", "advisory"]


class PlannerSubstanceFinding(BaseModel):
    severity: Severity
    code: str
    message: str
    slice_id: str | None = None
    category: str


class PlannerSubstanceReport(BaseModel):
    verdict: Literal["accept", "revise"]
    score: int = Field(ge=0, le=100)
    findings: list[PlannerSubstanceFinding] = Field(default_factory=list)
    category_scores: dict[str, int] = Field(default_factory=dict)


def evaluate_planner_substance(
    plan: PlanContract,
    *,
    project_context: Any | None = None,
) -> PlannerSubstanceReport:
    """Evaluate whether a valid plan is useful enough for autonomous execution."""
    qa_policy = _qa_policy(project_context)
    findings: list[PlannerSubstanceFinding] = []
    findings.extend(_verification_findings(plan))
    findings.extend(_qa_author_readiness_findings(plan, qa_policy))
    findings.extend(_implementation_readiness_findings(plan))
    findings.extend(_dependency_unblock_findings(plan))
    findings.extend(_cost_token_findings(plan))
    category_scores = _category_scores(findings)
    score = min(category_scores.values()) if category_scores else 100
    blocking = [finding for finding in findings if finding.severity == "blocking"]
    return PlannerSubstanceReport(
        verdict="revise" if blocking else "accept",
        score=score,
        findings=findings,
        category_scores=category_scores,
    )


def planner_qa_policy_summary(project_metadata: dict[str, Any]) -> dict[str, Any]:
    qa = project_metadata.get("qa")
    if not isinstance(qa, dict):
        return {}
    semantic = qa.get("semantic_conventions")
    if not isinstance(semantic, dict):
        semantic = {}
    payload = {
        "quality_gates": list(qa.get("quality_gates") or [])[:8],
        "avoid_patterns": list(qa.get("avoid_patterns") or [])[:8],
        "required_gates": qa.get("required_gates") or [],
        "benchmark_frameworks": qa.get("benchmark_frameworks") or [],
        "benchmark_variants": qa.get("benchmark_variants") or [],
        "endpoint_acceptance": semantic.get("endpoint_acceptance") or {},
        "payload_assertions": semantic.get("payload_assertions") or {},
        "preferred_helpers": qa.get("preferred_helpers") or {},
        "behavior_coverage_rules": qa.get("behavior_coverage_rules") or [],
    }
    return {key: value for key, value in payload.items() if value}


def _qa_policy(project_context: Any | None) -> dict[str, Any]:
    if project_context is None:
        return {}
    raw = getattr(project_context, "qa_policy_summary", {})
    return raw if isinstance(raw, dict) else {}


def _verification_findings(plan: PlanContract) -> list[PlannerSubstanceFinding]:
    findings: list[PlannerSubstanceFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.role not in {"implementer", "qa", "reviewer"}:
            continue
        commands = [_command_text(command) for command in task_slice.verification_commands]
        if not commands:
            continue
        non_verify = [command for command in commands if _is_non_verification_command(command)]
        if non_verify:
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_non_verification_command",
                    slice_id=task_slice.slice_id,
                    category="verification_specificity",
                    message=(
                        "Slice verification includes exploratory or dry-run commands that do "
                        "not prove build/test behavior."
                    ),
                )
            )
        if task_slice.role in {"implementer", "qa"} and _only_broad_gate_commands(commands):
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_broad_gate_without_module_local_command",
                    slice_id=task_slice.slice_id,
                    category="verification_specificity",
                    message=(
                        "Slice relies only on broad QA gates; include module-local test/build "
                        "commands for implementer and QA-author handoff readiness."
                    ),
                )
            )
    return findings


def _qa_author_readiness_findings(
    plan: PlanContract,
    qa_policy: dict[str, Any],
) -> list[PlannerSubstanceFinding]:
    qa_authors = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.qa.author"
    ]
    if not qa_authors:
        return []
    text = _plan_text(plan)
    findings: list[PlannerSubstanceFinding] = []
    for author in qa_authors:
        author_text = _slice_text(author)
        if _endpoint_policy_required(qa_policy) and _endpoint_feature(text):
            if not _mentions_any(
                author_text,
                ["mockmvc", "webtestclient", "testresttemplate", "http harness"],
            ):
                findings.append(
                    PlannerSubstanceFinding(
                        severity="advisory",
                        code="planner_endpoint_harness_guidance_missing",
                        slice_id=author.slice_id,
                        category="qa_author_readiness",
                        message=(
                            "Endpoint acceptance is in scope, but QA author guidance does not "
                            "require MockMvc, WebTestClient, TestRestTemplate, or an HTTP harness."
                        ),
                    )
                )
        if _structured_payload_policy_required(qa_policy) and _payload_feature(text):
            if not _mentions_any(
                author_text,
                ["jsonpath", "structured", "field assertion", "payload assertion"],
            ):
                findings.append(
                    PlannerSubstanceFinding(
                        severity="advisory",
                        code="planner_structured_assertion_guidance_missing",
                        slice_id=author.slice_id,
                        category="qa_author_readiness",
                        message=(
                            "Payload acceptance is in scope, but QA author guidance does not "
                            "require structured JSON/YAML field assertions."
                        ),
                    )
                )
        variants = [str(item).lower() for item in qa_policy.get("benchmark_variants") or []]
        if variants and _benchmark_feature(text):
            missing = [variant for variant in variants if variant not in author_text.lower()]
            if missing:
                findings.append(
                    PlannerSubstanceFinding(
                        severity="advisory",
                        code="planner_benchmark_variant_guidance_missing",
                        slice_id=author.slice_id,
                        category="qa_author_readiness",
                        message=(
                            "Benchmark acceptance is in scope, but QA author guidance omits "
                            f"configured variants: {', '.join(missing)}."
                        ),
                    )
                )
        if _generic_outputs(author.expected_outputs):
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_qa_expected_outputs_too_generic",
                    slice_id=author.slice_id,
                    category="qa_author_readiness",
                    message=(
                        "QA author expected_outputs should name concrete test files or fixtures."
                    ),
                )
            )
    return findings


def _implementation_readiness_findings(
    plan: PlanContract,
) -> list[PlannerSubstanceFinding]:
    findings: list[PlannerSubstanceFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.role != "implementer":
            continue
        if _generic_outputs(task_slice.expected_outputs):
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_implementation_outputs_too_generic",
                    slice_id=task_slice.slice_id,
                    category="implementation_readiness",
                    message=(
                        "Implementer expected_outputs should name concrete artifacts, APIs, "
                        "classes, or commands rather than generic contract names."
                    ),
                )
            )
        if len(task_slice.allowed_paths) > 8:
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_implementation_surface_broad",
                    slice_id=task_slice.slice_id,
                    category="implementation_readiness",
                    message="Implementer allowed_paths are broad enough to weaken ownership.",
                )
            )
    return findings


def _dependency_unblock_findings(plan: PlanContract) -> list[PlannerSubstanceFinding]:
    text = _plan_text(plan).lower()
    if not any(term in text for term in ["r-002", "prerequisite", "blocked", "readiness verdict"]):
        return []
    findings: list[PlannerSubstanceFinding] = []
    for task_slice in plan.task_slices:
        if "gated on" not in task_slice.objective.lower():
            continue
        dependency_text = " ".join(task_slice.depends_on).lower()
        if "design" not in dependency_text:
            findings.append(
                PlannerSubstanceFinding(
                    severity="advisory",
                    code="planner_dependency_gate_not_encoded",
                    slice_id=task_slice.slice_id,
                    category="dependency_unblock_clarity",
                    message=(
                        "Slice objective says it is gated, but dependency order does not encode "
                        "the prerequisite decision slice directly."
                    ),
                )
            )
    return findings


def _cost_token_findings(plan: PlanContract) -> list[PlannerSubstanceFinding]:
    text_size = len(_plan_text(plan))
    if text_size < 28_000:
        return []
    return [
        PlannerSubstanceFinding(
            severity="advisory",
            code="planner_plan_token_surface_large",
            category="cost_token_feasibility",
            message=(
                "Plan is large enough to raise downstream token/cost risk; keep handoffs "
                "specific but compact."
            ),
        )
    ]


def _category_scores(findings: list[PlannerSubstanceFinding]) -> dict[str, int]:
    categories = {
        "implementation_readiness",
        "qa_author_readiness",
        "verification_specificity",
        "path_boundary_safety",
        "dependency_unblock_clarity",
        "semantic_acceptance_quality",
        "cost_token_feasibility",
    }
    scores = dict.fromkeys(categories, 100)
    for finding in findings:
        penalty = 25 if finding.severity == "blocking" else 8
        scores[finding.category] = max(0, scores.get(finding.category, 100) - penalty)
    return scores


def _command_text(command: list[str]) -> str:
    return " ".join(str(part) for part in command).strip()


def _is_non_verification_command(command: str) -> bool:
    lowered = command.lower().strip()
    return (
        lowered.startswith(("grep ", "cat ", "echo ", "find "))
        or "--dry-run" in lowered
        or " --list" in lowered
    )


def _only_broad_gate_commands(commands: list[str]) -> bool:
    return bool(commands) and all(_is_broad_gate_command(command) for command in commands)


def _is_broad_gate_command(command: str) -> bool:
    lowered = command.lower()
    return (
        "qa/smoke.sh" in lowered
        or "qa/regression.sh" in lowered
        or lowered in {"./gradlew test", "gradlew test", "./gradlew check", "gradlew check"}
    )


def _endpoint_policy_required(qa_policy: dict[str, Any]) -> bool:
    endpoint = qa_policy.get("endpoint_acceptance")
    return isinstance(endpoint, dict) and bool(endpoint.get("require_http_harness"))


def _structured_payload_policy_required(qa_policy: dict[str, Any]) -> bool:
    payload = qa_policy.get("payload_assertions")
    return isinstance(payload, dict) and bool(payload.get("prefer_structured_json_paths"))


def _endpoint_feature(text: str) -> bool:
    return any(token in text.lower() for token in ["endpoint", "controller", " route", "/api/"])


def _payload_feature(text: str) -> bool:
    return any(token in text.lower() for token in ["json", "yaml", "payload", "response body"])


def _benchmark_feature(text: str) -> bool:
    return any(token in text.lower() for token in ["benchmark", "jmh", "allocation", "latency"])


def _mentions_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _generic_outputs(outputs: list[str]) -> bool:
    if not outputs:
        return True
    generic = {
        "qaauthorcontract",
        "taskresultcontract",
        "reviewverdictcontract",
        "qaresultcontract",
        "designcontract",
        "reviewreport",
    }
    normalized = [item.strip().lower() for item in outputs]
    return bool(normalized) and all(item in generic for item in normalized)


def _slice_text(task_slice: TaskSliceContract) -> str:
    return "\n".join(
        [
            task_slice.objective,
            *task_slice.expected_outputs,
            *task_slice.allowed_paths,
            *[" ".join(command) for command in task_slice.verification_commands],
        ]
    )


def _plan_text(plan: PlanContract) -> str:
    return "\n".join(
        [
            plan.problem_statement,
            *plan.assumptions,
            *plan.affected_surfaces,
            *plan.acceptance_test_matrix,
            *plan.risk_register,
            *[_slice_text(task_slice) for task_slice in plan.task_slices],
        ]
    )
