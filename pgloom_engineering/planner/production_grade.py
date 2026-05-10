from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pgloom_engineering.contracts import PlanContract
from pgloom_engineering.path_policy import discover_qa_write_paths, is_qa_write_path
from pgloom_engineering.planner.critic import _path_overlaps


class ProductionFinding(BaseModel):
    severity: Literal["blocking", "advisory"]
    code: str
    message: str
    slice_id: str | None = None


class ProductionGradeReport(BaseModel):
    verdict: Literal["accept", "revise"]
    score: int = Field(ge=0, le=100)
    blocking_findings: list[ProductionFinding] = Field(default_factory=list)
    advisory_findings: list[ProductionFinding] = Field(default_factory=list)


def evaluate_production_grade(
    plan: PlanContract,
    *,
    project_root: Path | None = None,
    qa_write_paths: list[str] | None = None,
) -> ProductionGradeReport:
    root = project_root or _default_project_root(plan.project)
    qa_roots = (
        [path.rstrip("/") + "/" for path in qa_write_paths]
        if qa_write_paths
        else discover_qa_write_paths(root)
        if root is not None and root.exists()
        else []
    )
    findings: list[ProductionFinding] = []
    findings.extend(_path_scope_findings(plan, root))
    findings.extend(_same_slice_overlap_findings(plan))
    findings.extend(_qa_verification_path_findings(plan, qa_roots))
    findings.extend(_qa_benchmark_output_path_findings(plan))
    findings.extend(_milestone_signoff_findings(plan))
    findings.extend(_small_feature_surface_findings(plan))
    blocking = [finding for finding in findings if finding.severity == "blocking"]
    advisory = [finding for finding in findings if finding.severity == "advisory"]
    score = max(0, 100 - len(blocking) * 25 - len(advisory) * 5)
    return ProductionGradeReport(
        verdict="accept" if not blocking else "revise",
        score=score,
        blocking_findings=blocking,
        advisory_findings=advisory,
    )


def _path_scope_findings(
    plan: PlanContract,
    project_root: Path | None,
) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        for path in [*task_slice.allowed_paths, *task_slice.forbidden_paths]:
            if "*" in path:
                findings.append(
                    ProductionFinding(
                        severity="blocking",
                        code="wildcard_path_scope",
                        slice_id=task_slice.slice_id,
                        message=f"Path scope must be concrete, not wildcarded: {path}",
                    )
                )
        for path in task_slice.allowed_paths:
            if project_root is not None and project_root.exists() and not _path_prefix_exists(
                project_root, path
            ):
                findings.append(
                    ProductionFinding(
                        severity="advisory",
                        code="path_prefix_not_found",
                        slice_id=task_slice.slice_id,
                        message=f"Path prefix does not exist in project checkout: {path}",
                    )
                )
    return findings


def _same_slice_overlap_findings(plan: PlanContract) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if _path_list_overlaps(task_slice.allowed_paths, task_slice.forbidden_paths):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="same_slice_allowed_forbidden_overlap",
                    slice_id=task_slice.slice_id,
                    message="Slice allowed_paths overlap its own forbidden_paths.",
                )
            )
    return findings


def _qa_verification_path_findings(
    plan: PlanContract,
    qa_roots: list[str],
) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
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
    qa_allowed = list(dict.fromkeys(path for item in qa_slices for path in item.allowed_paths))
    required_roots = _required_qa_roots(plan, qa_roots)
    for root in required_roots:
        if not any(_path_overlaps(root, allowed) for allowed in qa_allowed):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="qa_root_missing_for_verification",
                    message=(
                        f"Verification requires QA root {root}, but QA slices do not allow it."
                    ),
                )
            )
        if qa_roots and not any(_path_overlaps(root, discovered) for discovered in qa_roots):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="qa_root_not_registered",
                    message=f"Required QA root is not registered/discovered: {root}",
                )
            )
    for task_slice in plan.task_slices:
        if task_slice.role == "implementer":
            bad = [path for path in task_slice.allowed_paths if is_qa_write_path(path, qa_roots)]
            if bad:
                findings.append(
                    ProductionFinding(
                        severity="blocking",
                        code="implementer_owns_qa_root",
                        slice_id=task_slice.slice_id,
                        message=f"Implementer allowed_paths include QA roots: {', '.join(bad)}",
                    )
                )
    return findings


def _qa_benchmark_output_path_findings(plan: PlanContract) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.task_type != "engineering.qa.author":
            continue
        authoring_text = _authoring_text(task_slice)
        benchmark_paths = _benchmark_output_paths(task_slice.expected_outputs)
        if not benchmark_paths and not _requires_benchmark_source_root(authoring_text):
            continue
        missing_paths = [
            path
            for path in benchmark_paths
            if not any(_path_overlaps(path, allowed) for allowed in task_slice.allowed_paths)
        ]
        has_benchmark_root = any(
            _looks_like_benchmark_root(path) for path in task_slice.allowed_paths
        )
        if missing_paths or not has_benchmark_root:
            detail = (
                f" Missing expected benchmark paths: {', '.join(missing_paths)}."
                if missing_paths
                else ""
            )
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="qa_benchmark_output_path_not_allowed",
                    slice_id=task_slice.slice_id,
                    message=(
                        "QA author is expected to create benchmark/JMH artifacts, but "
                        "allowed_paths do not include a benchmark QA root."
                        f"{detail}"
                    ),
                )
            )
    return findings


def _small_feature_surface_findings(plan: PlanContract) -> list[ProductionFinding]:
    text = " ".join([plan.problem_statement, *plan.acceptance_test_matrix]).lower()
    if _is_wide_feature_text(text):
        return []
    if not any(term in text for term in ["config", "diagnostic", "range", "yaml"]):
        return []
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.role != "implementer":
            continue
        if len(task_slice.allowed_paths) > 7:
            findings.append(
                ProductionFinding(
                    severity="advisory",
                    code="small_feature_impl_scope_broad",
                    slice_id=task_slice.slice_id,
                    message="Small-feature implementer slice has a broad write surface.",
                )
            )
    return findings


def _milestone_signoff_findings(plan: PlanContract) -> list[ProductionFinding]:
    slice_type_by_id = {
        task_slice.slice_id: task_slice.task_type for task_slice in plan.task_slices
    }
    findings: list[ProductionFinding] = []
    for milestone in plan.milestones:
        slice_types = {slice_type_by_id.get(slice_id) for slice_id in milestone.slice_ids}
        if (
            milestone.signoff_policy == "scrutiny_and_usertest"
            and "engineering.qa.verify.scrutiny" not in slice_types
            and "engineering.qa.verify.usertest" not in slice_types
        ):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="milestone_signoff_unachievable",
                    message=(
                        f"Milestone {milestone.milestone_id} requires scrutiny/usertest "
                        "signoff but contains no validator slices."
                    ),
                )
            )
        elif (
            milestone.signoff_policy == "scrutiny_and_usertest"
            and (
                "engineering.qa.verify.scrutiny" not in slice_types
                or "engineering.qa.verify.usertest" not in slice_types
            )
        ):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="milestone_signoff_incomplete",
                    message=(
                        f"Milestone {milestone.milestone_id} requires both split "
                        "validators in its slice set."
                    ),
                )
            )
        elif (
            milestone.signoff_policy == "scrutiny_only"
            and "engineering.qa.verify.scrutiny" not in slice_types
        ):
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="milestone_signoff_incomplete",
                    message=(
                        f"Milestone {milestone.milestone_id} requires scrutiny signoff "
                        "but contains no scrutiny validator slice."
                    ),
                )
            )
    return findings


def _required_qa_roots(plan: PlanContract, qa_roots: list[str]) -> list[str]:
    roots: list[str] = []
    for task_slice in plan.task_slices:
        for command in task_slice.verification_commands:
            text = " ".join(command)
            if ":app-api:test" in text:
                roots.append("app-api/src/test/")
            if ":app-core:test" in text:
                roots.append("app-core/src/test/")
            if "npm --prefix ui" in text or "cd ui" in text or "playwright" in text.lower():
                roots.append("ui/tests/")
            if "./gradlew test" in text or text.strip() == "./gradlew test":
                roots.extend(_likely_gradle_test_roots(plan, qa_roots))
    return list(dict.fromkeys(roots))


def _likely_gradle_test_roots(plan: PlanContract, qa_roots: list[str]) -> list[str]:
    source_modules = {
        path.split("/", 1)[0]
        for task_slice in plan.task_slices
        for path in task_slice.allowed_paths
        if "/" in path and not is_qa_write_path(path, qa_roots)
    }
    roots = [
        root
        for root in qa_roots
        if root.split("/", 1)[0] in source_modules and "/src/test" in root
    ]
    return roots[:4] or [root for root in qa_roots if "/src/test" in root][:4]


def _is_wide_feature_text(text: str) -> bool:
    return any(
        term in text
        for term in [
            "signalspec",
            "signal spec",
            "backpressure",
            "overflow",
            "snapshot",
            "restore",
            "replication",
            "persistence",
            "promote",
        ]
    )


def _path_list_overlaps(left: list[str], right: list[str]) -> bool:
    for left_path in left:
        for right_path in right:
            if _path_overlaps(left_path, right_path):
                return True
    return False


def _path_prefix_exists(project_root: Path, path: str) -> bool:
    normalized = path.strip().rstrip("/")
    if normalized in {"", "."}:
        return True
    if normalized.endswith("/*"):
        normalized = normalized[:-2]
    full = project_root / normalized
    if full.exists():
        return True
    parent = full.parent
    return parent.exists()


def _authoring_text(task_slice: object) -> str:
    fields: list[str] = []
    for attr in [
        "objective",
        "expected_outputs",
        "verification_commands",
        "grading_criteria",
        "required_procedures",
    ]:
        value = getattr(task_slice, attr, None)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, list):
            fields.extend(str(item) for item in value)
        elif value is not None:
            fields.append(str(value))
    return " ".join(fields).lower()


def _requires_benchmark_source_root(text: str) -> bool:
    return any(
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
    )


def _benchmark_output_paths(expected_outputs: list[str]) -> list[str]:
    paths: list[str] = []
    for output in expected_outputs:
        for token in output.replace("`", " ").replace(",", " ").split():
            cleaned = token.strip(" .:;()[]{}\"'")
            if "/" in cleaned and _looks_like_benchmark_source_path(cleaned):
                paths.append(cleaned.rstrip("/") + "/")
    return list(dict.fromkeys(paths))


def _looks_like_benchmark_source_path(path: str) -> bool:
    lowered = path.lower()
    return (
        "src/jmh/" in lowered
        or lowered.startswith("benchmarks/src/")
        or "/benchmarks/src/" in lowered
    )


def _looks_like_benchmark_root(path: str) -> bool:
    lowered = path.lower()
    return "benchmark" in lowered or "src/jmh" in lowered or "/jmh/" in lowered


def _default_project_root(project: str) -> Path | None:
    roots = {
        "lvc-standard": Path("/Volumes/devssd/repos/ull/lvc-standard"),
        "trade-research-platform": Path("/Volumes/devssd/repos/apps/trade-research-platform"),
        "dag-framework": Path("/Volumes/devssd/repos/ull/dag_framework"),
    }
    return roots.get(project)
