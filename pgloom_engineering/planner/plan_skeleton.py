from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pgloom_engineering.contracts import FeatureGoalContract

TaskClass = Literal["small", "medium", "high_risk"]


class SkeletonSlice(BaseModel):
    slice_id: str
    role: str
    task_type: str
    depends_on: list[str] = Field(default_factory=list)
    allowed_path_hint: str
    purpose: str


class DeterministicPlanSkeleton(BaseModel):
    task_class: TaskClass
    target_slice_count: str
    required_order: list[str]
    slices: list[SkeletonSlice]
    local_expansion_rules: list[str]


def build_deterministic_plan_skeleton(
    feature_goal: FeatureGoalContract,
    *,
    relevant_paths: list[str] | None = None,
    qa_write_paths: list[str] | None = None,
) -> DeterministicPlanSkeleton:
    task_class = classify_task(feature_goal)
    text = _feature_text(feature_goal)
    source_paths = _source_path_hints(relevant_paths or [])
    implementer_slices = _implementer_slices(task_class, source_paths, text)
    slices = [
        SkeletonSlice(
            slice_id="design",
            role="designer",
            task_type="engineering.design",
            allowed_path_hint="docs/ plus affected source paths",
            purpose="Lock public API, ownership, constraints, and acceptance coverage.",
        ),
        SkeletonSlice(
            slice_id="qa-author",
            role="qa",
            task_type="engineering.qa.author",
            depends_on=["design"],
            allowed_path_hint=", ".join(qa_write_paths or ["tests/", "qa/fixtures/"]),
            purpose="Write failing tests and fixtures before implementation.",
        ),
        *implementer_slices,
        SkeletonSlice(
            slice_id="review",
            role="reviewer",
            task_type="engineering.review",
            depends_on=[implementer_slices[-1].slice_id],
            allowed_path_hint="affected source paths for read/review evidence",
            purpose="Multi-panel review of correctness, risks, and contract compliance.",
        ),
        SkeletonSlice(
            slice_id="qa-verify",
            role="qa",
            task_type="engineering.qa.verify",
            depends_on=["review"],
            allowed_path_hint=", ".join(qa_write_paths or ["tests/", "qa/fixtures/"]),
            purpose="Run smoke and full regression after reviewer sign-off.",
        ),
    ]
    return DeterministicPlanSkeleton(
        task_class=task_class,
        target_slice_count=_target_slice_count(task_class),
        required_order=[item.slice_id for item in slices],
        slices=slices,
        local_expansion_rules=[
            "Keep slice IDs stable unless the feature clearly needs extra implementer slices.",
            "Do not add finalization or merge slices; finalization_policy carries the human gate.",
            "QA author and QA verify write only registered QA/test roots.",
            "QA author objectives must preserve project QA policy: endpoint harnesses, "
            "structured assertions, benchmark variants, required gates, and avoid patterns.",
            "Implementers write source/config/docs only and must not claim QA write paths.",
            "Prefer module-local test/build commands for QA author and implementer slices; "
            "broad smoke/regression gates belong primarily in final QA verify.",
            "Expand objectives, acceptance matrix, risk register, and verification commands "
            "from feature-specific evidence.",
        ],
    )


def skeleton_prompt_payload(skeleton: DeterministicPlanSkeleton) -> dict[str, Any]:
    return skeleton.model_dump(mode="json")


def classify_task(feature_goal: FeatureGoalContract) -> TaskClass:
    text = _feature_text(feature_goal)
    if any(
        term in text
        for term in (
            "snapshot",
            "restore",
            "migration",
            "schema",
            "security",
            "concurrency",
            "distributed",
            "replication",
            "persistence",
            "persist",
            "data loss",
            "payment",
            "backpressure",
            "overflow",
            "mmap",
            "spill",
            "signalspec",
            "signal spec",
            "promote",
            "archive",
            "compiler",
        )
    ):
        return "high_risk"
    if any(
        term in text
        for term in (
            "diagnostic",
            "config",
            "docs",
            "readme",
            "single-surface",
            "visualizer",
            "small",
            "export",
        )
    ):
        return "small"
    return "medium"


def _implementer_slices(
    task_class: TaskClass,
    source_paths: list[str],
    text: str,
) -> list[SkeletonSlice]:
    if task_class == "high_risk":
        if "signalspec" in text or "signal spec" in text:
            return [
                SkeletonSlice(
                    slice_id="impl-dsl-compiler",
                    role="implementer",
                    task_type="engineering.implement",
                    depends_on=["qa-author"],
                    allowed_path_hint="platform-dsl/ and related signal compiler paths",
                    purpose="Implement validation and compilation for declarative signal specs.",
                ),
                SkeletonSlice(
                    slice_id="impl-api-workflow",
                    role="implementer",
                    task_type="engineering.implement",
                    depends_on=["impl-dsl-compiler"],
                    allowed_path_hint="app-api/ and app-core workflow paths",
                    purpose=(
                        "Implement propose/catalog/get/promote/archive API workflow "
                        "and persistence."
                    ),
                ),
                SkeletonSlice(
                    slice_id="impl-ui-provenance",
                    role="implementer",
                    task_type="engineering.implement",
                    depends_on=["impl-api-workflow"],
                    allowed_path_hint="ui/ and explainability/provenance display paths",
                    purpose=(
                        "Implement UI catalog/provenance surface and explainability "
                        "envelope wiring."
                    ),
                ),
            ]
        if "backpressure" in text or "overflow" in text:
            return [
                SkeletonSlice(
                    slice_id="impl-policy-core",
                    role="implementer",
                    task_type="engineering.implement",
                    depends_on=["qa-author"],
                    allowed_path_hint="dag-framework-api/ and runtime-core/ backpressure paths",
                    purpose="Implement policy enum, per-edge config, and runtime policy behavior.",
                ),
                SkeletonSlice(
                    slice_id="impl-overflow-spill",
                    role="implementer",
                    task_type="engineering.implement",
                    depends_on=["impl-policy-core"],
                    allowed_path_hint="runtime-core/ and dag-framework-lvc/ spill/journal paths",
                    purpose=(
                        "Implement overflow-to-disk spill/drain behavior and hot-swap "
                        "invariants."
                    ),
                ),
            ]
        return [
            SkeletonSlice(
                slice_id="impl-core",
                role="implementer",
                task_type="engineering.implement",
                depends_on=["qa-author"],
                allowed_path_hint=source_paths[0],
                purpose="Implement the primary source/runtime change.",
            ),
            SkeletonSlice(
                slice_id="impl-invariants",
                role="implementer",
                task_type="engineering.implement",
                depends_on=["impl-core"],
                allowed_path_hint=source_paths[-1],
                purpose="Implement lifecycle, failure, persistence, or integration invariants.",
            ),
        ]
    return [
        SkeletonSlice(
            slice_id="impl-primary",
            role="implementer",
            task_type="engineering.implement",
            depends_on=["qa-author"],
            allowed_path_hint=", ".join(source_paths[:3]),
            purpose="Implement the narrow feature slice.",
        )
    ]


def _source_path_hints(relevant_paths: list[str]) -> list[str]:
    source_paths = [
        path
        for path in relevant_paths
        if not (path.startswith("tests/") or path.startswith("qa/fixtures/"))
    ]
    return source_paths[:4] or ["affected source paths"]


def _target_slice_count(task_class: TaskClass) -> str:
    if task_class == "small":
        return "5 slices: design, qa-author, impl-primary, review, qa-verify"
    if task_class == "high_risk":
        return "6-8 slices with only justified extra implementers"
    return "5-6 slices"


def _feature_text(feature_goal: FeatureGoalContract) -> str:
    return " ".join(
        [
            feature_goal.goal,
            *feature_goal.requirements,
            *feature_goal.constraints,
            *feature_goal.acceptance_criteria,
        ]
    ).lower()
