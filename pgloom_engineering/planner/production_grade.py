from __future__ import annotations

import json
import re
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
    project_metadata: dict[str, object] | None = None,
    allow_narrow_corrective_slice: bool = False,
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
    findings.extend(
        _qa_author_dependency_findings(
            plan,
            allow_narrow_corrective_slice=allow_narrow_corrective_slice,
        )
    )
    findings.extend(_qa_benchmark_output_path_findings(plan))
    findings.extend(_qa_reflective_authoring_findings(plan))
    findings.extend(_qa_required_usertest_fixture_findings(plan, project_metadata or {}))
    findings.extend(_qa_usertest_command_findings(plan))
    findings.extend(_milestone_signoff_findings(plan))
    findings.extend(_variant_scope_verification_findings(plan, project_metadata or {}))
    findings.extend(_small_feature_surface_findings(plan))
    findings.extend(_broad_implementation_source_root_findings(plan))
    findings.extend(_hot_path_implementation_surface_findings(plan, root))
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


def _qa_reflective_authoring_findings(plan: PlanContract) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.task_type != "engineering.qa.author":
            continue
        text = _authoring_text(task_slice)
        if not _requires_public_api_behavior_tests(text):
            continue
        if not _mentions_reflective_api_testing(text):
            continue
        findings.append(
            ProductionFinding(
                severity="blocking",
                code="qa_author_reflective_api_testing",
                slice_id=task_slice.slice_id,
                message=(
                    "QA author guidance asks for reflective/proxy API tests. "
                    "Range/API acceptance tests must compile against the public API "
                    "and assert behavior directly instead of using Class.forName, "
                    "Method.invoke, Proxy, or reflection-oriented signature checks."
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


def _broad_implementation_source_root_findings(plan: PlanContract) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.role != "implementer":
            continue
        broad_paths = [
            path
            for path in task_slice.allowed_paths
            if _is_module_source_root(path)
        ]
        if not broad_paths:
            continue
        findings.append(
            ProductionFinding(
                severity="blocking",
                code="implementer_source_root_too_broad",
                slice_id=task_slice.slice_id,
                message=(
                    "Implementer allowed_paths must use package/file-level roots, not broad "
                    f"module source roots: {', '.join(broad_paths)}"
                ),
            )
        )
    return findings


def _hot_path_implementation_surface_findings(
    plan: PlanContract,
    project_root: Path | None,
) -> list[ProductionFinding]:
    if project_root is None or not project_root.exists():
        return []
    contract_names = _hot_path_shared_api_contract_names(plan)
    if not contract_names:
        return []
    implementation_paths = _java_implementation_paths(project_root, contract_names)
    if not implementation_paths:
        return []
    implementer_allowed = [
        path
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.implement"
        for path in task_slice.allowed_paths
    ]
    missing = [
        path
        for path in implementation_paths
        if not any(_path_overlaps(path, allowed) for allowed in implementer_allowed)
    ]
    if not missing:
        return []
    return [
        ProductionFinding(
            severity="blocking",
            code="hot_path_implementation_surface_missing",
            message=(
                "Hot-path shared API plan omits implementation/delegating source "
                "paths that implement the changed contract: "
                + ", ".join(missing[:8])
            ),
        )
    ]


def _hot_path_shared_api_contract_names(plan: PlanContract) -> set[str]:
    text = _semantic_plan_text(plan)
    lowered = text.lower()
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
    if not any(term in lowered for term in hot_path_terms) or not any(
        term in lowered for term in shared_api_terms
    ):
        return set()
    excluded_suffixes = (
        "Test",
        "Tests",
        "Benchmark",
        "Benchmarks",
        "Report",
        "Contract",
        "Evidence",
    )
    names = set(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", text))
    return {
        name
        for name in names
        if not name.endswith(excluded_suffixes)
        and name
        not in {
            "API",
            "JMH",
            "QA",
            "SINGLE",
            "DOUBLE",
        }
    }


def _java_implementation_paths(
    project_root: Path,
    contract_names: set[str],
) -> list[str]:
    paths: list[str] = []
    for source in project_root.rglob("*.java"):
        relative = source.relative_to(project_root).as_posix()
        if _skip_java_source_path(relative):
            continue
        if "/src/main/java/" not in relative:
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for clause in re.findall(r"\bimplements\s+([^{]+)", text):
            implemented = _implemented_java_type_names(clause)
            if implemented & contract_names:
                paths.append(relative)
                break
    return list(dict.fromkeys(paths))


def _skip_java_source_path(relative: str) -> bool:
    return any(
        part in {".git", ".gradle", ".local", "build", "target", "out"}
        for part in relative.split("/")
    )


def _implemented_java_type_names(clause: str) -> set[str]:
    names: set[str] = set()
    for item in clause.split(","):
        item = re.sub(r"<[^>]*>", "", item).strip()
        if not item:
            continue
        match = re.search(r"([A-Z][A-Za-z0-9_]*)(?:\s*$)", item)
        if match:
            names.add(match.group(1))
    return names


def _semantic_plan_text(plan: PlanContract) -> str:
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


def _is_module_source_root(path: str) -> bool:
    normalized = path.strip().rstrip("/")
    return bool(re.search(r"(^|/)src/main/(java|kotlin|scala)$", normalized))


def _variant_scope_verification_findings(
    plan: PlanContract,
    project_metadata: dict[str, object],
) -> list[ProductionFinding]:
    rules = _variant_verification_rules(project_metadata)
    if not rules:
        return []
    implementers = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.implement"
    ]
    findings: list[ProductionFinding] = []
    for rule in rules:
        scoped = [
            (task_slice, _variant_scope_text(_variant_scope_source(task_slice), rule))
            for task_slice in implementers
        ]
        for task_slice, scope in scoped:
            if not scope:
                continue
            if not any(
                _variant_scope_conflicts(scope, other_scope, rule)
                for _, other_scope in scoped
            ):
                continue
            if not _has_broad_variant_gate_without_specific_gate(
                task_slice.verification_commands,
                rule,
            ):
                continue
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="variant_slice_uses_broad_conformance_gate",
                    slice_id=task_slice.slice_id,
                    message=(
                        "Variant-scoped implementer slice uses a broad conformance gate. "
                        "Either keep the implementation in one slice, or provide a "
                        "slice-specific verification command so the worker is not forced "
                        "to implement sibling variants."
                    ),
                )
            )
    return findings


def _variant_verification_rules(
    project_metadata: dict[str, object],
) -> list[dict[str, object]]:
    planner = project_metadata.get("planner")
    qa = project_metadata.get("qa")
    candidates: list[object] = []
    if isinstance(planner, dict):
        candidates.append(planner.get("variant_verification_rules"))
    if isinstance(qa, dict):
        candidates.append(qa.get("variant_verification_rules"))
    candidates.append(project_metadata.get("variant_verification_rules"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _qa_usertest_command_findings(plan: PlanContract) -> list[ProductionFinding]:
    findings: list[ProductionFinding] = []
    for task_slice in plan.task_slices:
        if task_slice.task_type != "engineering.qa.verify.usertest":
            continue
        for command in task_slice.verification_commands:
            if not _looks_like_deterministic_usertest_substitute(command):
                continue
            findings.append(
                ProductionFinding(
                    severity="blocking",
                    code="qa_usertest_uses_deterministic_command",
                    slice_id=task_slice.slice_id,
                    message=(
                        "engineering.qa.verify.usertest must be a model-driven "
                        "user-facing exercise. Do not use deterministic test, lint, "
                        "check, smoke, regression, or benchmark commands as the "
                        "user-test verification_commands; keep those in "
                        "engineering.qa.verify.scrutiny and give usertest only a "
                        "launch/setup harness or interaction entrypoint."
                    ),
                )
            )
            break
    return findings


def _qa_required_usertest_fixture_findings(
    plan: PlanContract,
    project_metadata: dict[str, object],
) -> list[ProductionFinding]:
    required_paths = _metadata_required_usertest_fixture_paths(project_metadata)
    if not required_paths:
        return []
    if not any(
        task_slice.task_type == "engineering.qa.verify.usertest"
        for task_slice in plan.task_slices
    ):
        return []
    qa_authors = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.qa.author"
    ]
    if not qa_authors:
        return []
    findings: list[ProductionFinding] = []
    for required_path in required_paths:
        if any(
            _slice_mentions_path(task_slice, required_path)
            for task_slice in qa_authors
        ):
            continue
        findings.append(
            ProductionFinding(
                severity="blocking",
                code="qa_author_required_usertest_fixture_missing",
                slice_id=qa_authors[0].slice_id,
                message=(
                    "Project metadata requires a user-test replay fixture, but the "
                    f"QA author slice does not name {required_path} in its objective, "
                    "expected_outputs, or handoff requirements. Plans with "
                    "engineering.qa.verify.usertest must give QA author concrete "
                    "fixture outputs for downstream model-driven user testing."
                ),
            )
        )
    return findings


def _qa_author_dependency_findings(
    plan: PlanContract,
    *,
    allow_narrow_corrective_slice: bool = False,
) -> list[ProductionFinding]:
    implementers = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.implement"
    ]
    if not implementers:
        return []
    if allow_narrow_corrective_slice:
        return []
    qa_authors = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.task_type == "engineering.qa.author"
    ]
    if not qa_authors:
        return [
            ProductionFinding(
                severity="blocking",
                code="qa_author_missing_before_implementer",
                slice_id=implementers[0].slice_id,
                message=(
                    "Plans with implementer slices must include a QA author slice so "
                    "implementation starts from authored failing tests and a QA worktree "
                    "handoff."
                ),
            )
        ]
    qa_ids = {task_slice.slice_id for task_slice in qa_authors}
    findings: list[ProductionFinding] = []
    for implementer in implementers:
        if any(_depends_on_transitively(plan, implementer.slice_id, qa_id) for qa_id in qa_ids):
            continue
        findings.append(
            ProductionFinding(
                severity="blocking",
                code="implementer_missing_qa_author_dependency",
                slice_id=implementer.slice_id,
                message=(
                    "Implementer slices must depend on a QA author slice so the "
                    "workflow has a QA worktree handoff before implementation."
                ),
            )
        )
    return findings


def _depends_on_transitively(plan: PlanContract, slice_id: str, dependency_id: str) -> bool:
    by_id = {task_slice.slice_id: task_slice for task_slice in plan.task_slices}
    seen: set[str] = set()
    task_slice = by_id.get(slice_id)
    stack = list(task_slice.depends_on if task_slice is not None else [])
    while stack:
        current = stack.pop()
        if current == dependency_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        dependency = by_id.get(current)
        if dependency is not None:
            stack.extend(dependency.depends_on)
    return False


def _metadata_required_usertest_fixture_paths(
    project_metadata: dict[str, object],
) -> list[str]:
    qa = project_metadata.get("qa")
    if not isinstance(qa, dict):
        return []
    harness = qa.get("usertest_harness")
    if not isinstance(harness, dict) or harness.get("kind") == "none":
        return []
    raw_paths = harness.get("required_fixture_paths")
    if not isinstance(raw_paths, list):
        return []
    paths: list[str] = []
    for item in raw_paths:
        if not isinstance(item, str):
            continue
        path = item.strip().replace("\\", "/").lstrip("./")
        if path and path not in paths:
            paths.append(path)
    return paths


def _slice_mentions_path(task_slice: object, required_path: str) -> bool:
    text = json.dumps(
        {
            "objective": getattr(task_slice, "objective", ""),
            "expected_outputs": getattr(task_slice, "expected_outputs", []),
            "handoff_requirements": getattr(task_slice, "handoff_requirements", []),
            "required_procedures": getattr(task_slice, "required_procedures", []),
        },
        sort_keys=True,
    )
    return required_path in text


def _looks_like_deterministic_usertest_substitute(command: list[str]) -> bool:
    lowered = " ".join(command).lower()
    if not lowered.strip():
        return False
    deterministic_markers = [
        "./qa/smoke.sh",
        "./qa/regression.sh",
        "jmhsmokecheck",
        "jmh",
        "pytest",
        "go test",
        "cargo test",
        "mvn test",
        "npm test",
        "pnpm test",
        "yarn test",
        "checkstyle",
    ]
    if any(marker in lowered for marker in deterministic_markers):
        return True
    if re.search(r"(^|\s)(\./)?gradlew?(\s|$)", lowered):
        return any(
            token in lowered
            for token in [
                ":test",
                " test",
                ":check",
                " check",
                "checkstyle",
                "jmh",
            ]
        )
    return False


def _has_broad_variant_gate_without_specific_gate(
    commands: list[list[str]],
    rule: dict[str, object],
) -> bool:
    specific_classes: set[str] = set()
    for command in commands:
        test_filter = _gradle_test_filter(command)
        if (
            test_filter
            and _looks_like_method_test_filter(test_filter)
            and _gradle_test_task_key(command)
        ):
            specific_classes.add(test_filter.rsplit(".", maxsplit=1)[0].lower())
    for command in commands:
        test_filter = _gradle_test_filter(command)
        if not _looks_like_broad_variant_gate(command, rule):
            continue
        class_name = (test_filter or "").lower()
        if class_name not in specific_classes:
            return True
    return False


def _looks_like_method_test_filter(test_filter: str) -> bool:
    if "." not in test_filter:
        return False
    method_name = test_filter.rsplit(".", maxsplit=1)[1]
    return bool(method_name) and method_name[0].islower()


def _variant_scope_text(text: str, rule: dict[str, object]) -> set[str]:
    lowered = text.lower()
    conflicts = _variant_conflicts(rule)
    scope: set[str] = set()
    for include, excludes in conflicts.items():
        if include in lowered and not any(exclude in lowered for exclude in excludes):
            scope.add(include)
    return scope


def _variant_scope_conflicts(
    left: set[str],
    right: set[str],
    rule: dict[str, object],
) -> bool:
    conflicts = _variant_conflicts(rule)
    return any(conflict in right for item in left for conflict in conflicts.get(item, []))


def _variant_conflicts(rule: dict[str, object]) -> dict[str, set[str]]:
    raw = rule.get("conflicts")
    if not isinstance(raw, dict):
        return {}
    conflicts: dict[str, set[str]] = {}
    for key, values in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(values, str):
            conflicts[key.lower()] = {values.lower()}
        elif isinstance(values, list):
            conflicts[key.lower()] = {
                str(value).lower() for value in values if isinstance(value, str)
            }
    return {key: values for key, values in conflicts.items() if values}


def _looks_like_broad_variant_gate(
    command: list[str],
    rule: dict[str, object],
) -> bool:
    text = " ".join(command)
    lowered = text.lower()
    required_markers = _string_list(rule.get("broad_gate_markers"))
    if required_markers and not all(marker.lower() in lowered for marker in required_markers):
        return False
    if not required_markers:
        return False
    test_filter = _gradle_test_filter(command)
    if test_filter and _looks_like_method_test_filter(test_filter):
        return False
    exempt_markers = _string_list(rule.get("broad_gate_exempt_markers"))
    return not any(
        marker.lower() in lowered
        for marker in exempt_markers
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _gradle_test_filter(command: list[str]) -> str | None:
    try:
        index = command.index("--tests")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    value = command[index + 1]
    return value if isinstance(value, str) and value.strip() else None


def _gradle_test_task_key(command: list[str]) -> tuple[str, ...] | None:
    if not command:
        return None
    executable = Path(command[0]).name
    if executable not in {"gradle", "gradlew"} and command[0] != "./gradlew":
        return None
    task_parts: list[str] = []
    for part in command[1:]:
        if part == "--tests":
            break
        if part.startswith("-"):
            continue
        task_parts.append(part)
    return tuple(task_parts) if task_parts else None


def _variant_scope_source(task_slice: object) -> str:
    fields: list[str] = []
    for attr in [
        "slice_id",
        "objective",
    ]:
        value = getattr(task_slice, attr, None)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, list):
            fields.extend(str(item) for item in value)
        elif value is not None:
            fields.append(str(value))
    return " ".join(fields)


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


def _requires_public_api_behavior_tests(text: str) -> bool:
    return any(token in text for token in ["public api", "api test", "api contract", "lvcstore"])


def _mentions_reflective_api_testing(text: str) -> bool:
    reflective_tokens = [
        "reflection",
        "reflective",
        "class.forname",
        "getmethod",
        "method.invoke",
        "invocationhandler",
        "proxy.newproxyinstance",
        "reflection/signature",
    ]
    for sentence in re.split(r"[;\n]", text):
        if not any(token in sentence for token in reflective_tokens):
            continue
        if _prohibits_reflective_api_testing(sentence):
            continue
        return True
    return False


def _prohibits_reflective_api_testing(sentence: str) -> bool:
    return any(
        marker in sentence
        for marker in [
            "do not",
            "don't",
            "must not",
            "rather than",
            "instead of",
            "without",
            "avoid",
            "forbid",
            "forbidden",
            "not reflection",
            "not reflective",
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
