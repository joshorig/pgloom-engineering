from __future__ import annotations

from pathlib import Path

from pgloom_engineering.contracts import (
    DesignContract,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
)
from pgloom_engineering.projects import ProjectConfig
from pgloom_engineering.roles.planner import (
    _apply_corrective_slice_scope,
    _apply_metadata_required_usertest_fixtures,
    _apply_replan_supersession,
    _assign_task_slice_milestones,
    _canonicalize_plan_feature_id,
    _feature_scoped_verification_commands,
    _normalize_feature_scoped_plan_verification,
    _plan_validation_error_summary,
    _post_normalization_quality_errors,
    _provider_usage_limit_reason,
    _task_replan_context_payload,
)


def test_canonicalize_plan_feature_id_uses_workflow_id() -> None:
    plan = PlanContract(
        feature_id="R-003",
        project="trade-research-platform",
        problem_statement="Implement roadmap item.",
        design_contract=DesignContract(acceptance_tests=["coverage"]),
        affected_surfaces=["app-api/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write failing tests.",
                allowed_paths=["app-api/src/test/java/"],
                forbidden_paths=["app-api/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[["./gradlew", ":app-api:test"]],
            )
        ],
        acceptance_test_matrix=["coverage"],
    )

    canonical = _canonicalize_plan_feature_id(plan, {"workflow_id": "wf_live"})

    assert canonical.feature_id == "wf_live"
    assert plan.feature_id == "R-003"


def test_plan_validation_error_summary_includes_actionable_codes() -> None:
    summary = _plan_validation_error_summary(
        [
            {
                "code": "slice_missing_acceptance_assertion",
                "message": "impl must claim at least one acceptance assertion.",
            },
            {
                "code": "acceptance_assertion_unclaimed",
                "message": "Acceptance assertions have no claiming slice: benchmark smoke.",
            },
        ]
    )

    assert "slice_missing_acceptance_assertion" in summary
    assert "impl must claim" in summary
    assert "acceptance_assertion_unclaimed" in summary


def test_provider_usage_limit_reason_detects_codex_quota_output() -> None:
    reason = _provider_usage_limit_reason(
        [
            {
                "panelist_id": "panelist-0",
                "raw_response": (
                    "{\"type\":\"error\",\"message\":\"You've hit your usage limit. "
                    'Visit https://chatgpt.com/codex/settings/usage to purchase '
                    'more credits or try again at 7:49 PM."}'
                ),
                "parse_error": "missing PlanContract fields",
            }
        ]
    )

    assert reason is not None
    assert "provider usage limit" in reason
    assert "panelist-0" in reason


def test_apply_replan_supersession_marks_corrective_plan() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct QA scrutiny commands.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run focused QA scrutiny.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["core/src/main/java/"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["./gradlew", ":core:test"]],
            )
        ],
        acceptance_test_matrix=["focused QA scrutiny passes"],
    )

    corrected = _apply_replan_supersession(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "active_plan_contract_id": "plan_old",
                "blocker_code": "engineering.qa_verify_failed",
                "blocker_reason": "bare ./gradlew check failed",
            }
        },
    )

    assert corrected.supersedes_plan_id == "plan_old"
    assert "engineering.qa_verify_failed" in str(corrected.supersession_rationale)


def test_assign_task_slice_milestones_from_milestone_membership() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Persist first-class milestone membership.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement range scans.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["tests/"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review range scans.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["tests/"],
                depends_on=["impl"],
                expected_outputs=["ReviewVerdictContract"],
                milestone_id="existing",
            ),
        ],
        acceptance_test_matrix=["range scans work"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Implementation",
                slice_ids=["impl", "review"],
            )
        ],
    )

    assigned = _assign_task_slice_milestones(plan)

    assert assigned.task_slices[0].milestone_id == "m1"
    assert assigned.task_slices[1].milestone_id == "existing"


def test_task_replan_context_payload_compacts_corrective_evidence() -> None:
    payload = {
        "replan_context": {
            "mode": "corrective_slice",
            "source": "workflow_driver",
            "blocked_task_id": "task-1",
            "blocker_code": "engineering.review_rejected",
            "blocker_reason": "review failed",
            "failure_context": "x" * 4000,
            "blocked_slice_id": "impl",
            "same_blocker_recovery_count": 1,
            "benchmark_gate_classification": "material_allocation",
            "benchmark_allocation_diagnosis": {
                "classification": "material_allocation",
                "diagnostic_required": True,
                "failing_benchmarks": [
                    {
                        "benchmark": "RangeScanBenchmark.ascendingScan",
                        "b_op": 0.031,
                        "threshold_b_op": 0.005,
                    }
                ],
                "repair_directive": "y" * 4000,
            },
            "summary": "repair the rejected range behavior",
            "blocked_task_contract": {"large": "omitted"},
        }
    }

    context = _task_replan_context_payload(payload)

    assert context is not None
    assert context["mode"] == "corrective_slice"
    assert context["blocker_code"] == "engineering.review_rejected"
    assert context["blocked_slice_id"] == "impl"
    assert context["same_blocker_recovery_count"] == 1
    assert len(context["failure_context"]) == 3000
    assert context["benchmark_gate_classification"] == "material_allocation"
    assert context["benchmark_allocation_diagnosis"]["diagnostic_required"] is True
    assert (
        context["benchmark_allocation_diagnosis"]["failing_benchmarks"][0]["benchmark"]
        == "RangeScanBenchmark.ascendingScan"
    )
    assert len(context["benchmark_allocation_diagnosis"]["repair_directive"]) == 3000
    assert "blocked_task_contract" not in context


def test_apply_corrective_slice_scope_removes_design_and_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct reviewer findings.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Re-state design.",
                allowed_paths=["docs/"],
                forbidden_paths=["tests/"],
                expected_outputs=["DesignContract"],
            ),
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write new tests.",
                allowed_paths=["tests/"],
                forbidden_paths=["core/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix reviewer finding.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["tests/"],
                depends_on=["qa-author"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["tests/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify fix.",
                allowed_paths=["tests/"],
                forbidden_paths=["core/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Replay fix.",
                allowed_paths=["tests/"],
                forbidden_paths=["core/"],
                depends_on=["qa-scrutiny"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        acceptance_test_matrix=["reviewer finding fixed"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=[
                    "design",
                    "qa-author",
                    "impl-fix",
                    "review",
                    "qa-scrutiny",
                    "qa-usertest",
                ],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.review_rejected",
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-fix",
        "review",
        "qa-scrutiny",
        "qa-usertest",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.milestones[0].slice_ids == [
        "impl-fix",
        "review",
        "qa-scrutiny",
        "qa-usertest",
    ]


def test_apply_corrective_slice_scope_restores_kept_milestone_membership() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct QA scrutiny failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark allocation remains below gate"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix benchmark allocation.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                expected_outputs=["TaskResultContract"],
                milestone_id="m1",
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review benchmark allocation fix.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
                milestone_id="m1",
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify feature gates.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
                milestone_id="m1",
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Exercise public behavior.",
                allowed_paths=["qa/fixtures/"],
                forbidden_paths=[],
                depends_on=["qa-scrutiny"],
                expected_outputs=["QAResultContract"],
                milestone_id="m1",
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["impl-fix", "review"],
                signoff_policy="scrutiny_and_usertest",
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.qa_verify_failed",
                "failure_context": (
                    "RangeScanBenchmark.ascendingRange allocated 0.207 B/op "
                    "above the 0.005 B/op threshold"
                ),
            }
        },
    )

    assert scoped.milestones[0].slice_ids == [
        "impl-fix",
        "review",
        "qa-scrutiny",
        "qa-usertest",
    ]


def test_apply_corrective_slice_scope_handles_plan_contract_invalid() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Repair invalid corrective plan.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Rewrite broad QA tests.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["core/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
                acceptance_assertion_ids=["assertion-no-forbidden-scope"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair descending empty-intersection behavior.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["core/src/test/java/"],
                depends_on=["qa-author"],
                expected_outputs=["TaskResultContract"],
                acceptance_assertion_ids=["descending empty intersections are safe"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review repair.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["core/src/test/java/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
                acceptance_assertion_ids=["descending empty intersections are safe"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run feature-specific verification.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
                acceptance_assertion_ids=["descending empty intersections are safe"],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Run user-test journey.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["qa-scrutiny"],
                expected_outputs=["QAResultContract"],
                acceptance_assertion_ids=["descending empty intersections are safe"],
            ),
        ],
        acceptance_test_matrix=["descending empty intersections are safe"],
        acceptance_assertions=[
            "descending empty intersections are safe",
            "assertion-no-forbidden-scope",
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=[
                    "qa-author",
                    "impl-fix",
                    "review",
                    "qa-scrutiny",
                    "qa-usertest",
                ],
                acceptance_assertions=[
                    "descending empty intersections are safe",
                    "assertion-no-forbidden-scope",
                ],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.plan_contract_invalid",
                "blocker_reason": (
                    "plan contract failed validation: "
                    "acceptance_assertion_unclaimed"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-fix",
        "review",
        "qa-scrutiny",
        "qa-usertest",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.acceptance_assertions == ["descending empty intersections are safe"]
    assert scoped.milestones[0].acceptance_assertions == [
        "descending empty intersections are safe"
    ]


def test_apply_corrective_slice_scope_keeps_feature_test_failure_on_implementer() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Repair failed implementer verification.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Rewrite range tests.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair production descending range behavior.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["core/src/test/java/"],
                depends_on=["qa-author"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review repair.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["core/src/test/java/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        acceptance_test_matrix=["feature-specific test passes"],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "blocker_reason": (
                    "implementer verification commands failed: "
                    "./gradlew :core:test --tests "
                    "com.example.RangeScanApiTest.storeVisitorIsPublicFunctionalInterface"
                ),
                "failure_context": (
                    "command=./gradlew :core:test --tests "
                    "com.example.RangeScanApiTest.storeVisitorIsPublicFunctionalInterface "
                    "changed_files=store/src/main/java/Store.java"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []


def test_apply_corrective_slice_scope_preserves_blocked_implementer_paths() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct implementer contract output.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair range scan implementation.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["docs/"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["docs/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        acceptance_test_matrix=["range scans work"],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementer_contract_invalid",
                "blocked_task_contract": {
                    "task_type": "engineering.implement",
                },
                "blocked_slice_allowed_paths": [
                    "store/src/main/java/com/joshorig/ull/lvc/store/mmap/"
                ],
                "blocked_slice_forbidden_paths": [
                    "core/src/main/java/com/joshorig/ull/lvc/metrics/"
                ],
            }
        },
    )

    implementer = scoped.task_slices[0]
    reviewer = scoped.task_slices[1]
    assert implementer.allowed_paths == [
        "store/src/main/java/com/joshorig/ull/lvc/store/mmap/",
    ]
    assert implementer.forbidden_paths == [
        "core/src/main/java/com/joshorig/ull/lvc/metrics/"
    ]
    assert reviewer.allowed_paths == ["core/src/main/java/", "store/src/main/java/"]


def test_apply_corrective_slice_scope_restores_explicit_source_paths_from_failure() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="example-library",
        problem_statement="Correct public API implementation.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["api/", "storage/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-api-storage",
                role="implementer",
                task_type="engineering.implement",
                objective=(
                    "Repair the public visitor API and storage implementation named by "
                    "the failure evidence."
                ),
                allowed_paths=[
                    "storage/src/main/java/example/store/",
                    "docs/Storage.md",
                ],
                forbidden_paths=[
                    "api/src/test/java/",
                    "api/src/main/java/",
                    "benchmarks/build.gradle",
                ],
                expected_outputs=["VisitorApiImplementation"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":api:test",
                        "--tests",
                        "example.api.RangeApiTest",
                    ]
                ],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review range API repair.",
                allowed_paths=["api/src/main/java/", "storage/src/main/java/"],
                forbidden_paths=["api/src/test/java/"],
                depends_on=["impl-api-storage"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        acceptance_test_matrix=["Range API tests compile."],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "blocked_task_contract": {
                    "task_type": "engineering.implement",
                },
                "blocked_slice_allowed_paths": [
                    "storage/src/main/java/example/store/",
                    "docs/Storage.md",
                ],
                "blocked_slice_forbidden_paths": [
                    "api/src/test/java/",
                    "api/src/main/java/",
                    "benchmarks/build.gradle",
                ],
                "failure_context": (
                    "RangeApiTest.java:111: error: cannot find symbol class Visitor; "
                    "see api/src/main/java/example/api/Visitor.java"
                ),
            }
        },
    )

    implementer = scoped.task_slices[0]
    assert "api/src/main/java/example/api/Visitor.java" in implementer.allowed_paths
    assert "docs/Storage.md" not in implementer.allowed_paths
    assert "api/src/main/java/" not in implementer.forbidden_paths
    assert "api/src/test/java/" in implementer.forbidden_paths


def test_apply_corrective_slice_scope_preserves_planner_added_implementation_paths() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="example-library",
        problem_statement="Correct indexed range API implementation.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "storage/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-range-api-corrective",
                role="implementer",
                task_type="engineering.implement",
                objective=(
                    "Repair the public range API and the B-tree backend named by "
                    "the failure evidence."
                ),
                allowed_paths=[
                    "core/src/main/java/example/api/",
                    "storage/src/main/java/example/store/BTreeRangeIndex.java",
                ],
                forbidden_paths=[
                    "core/src/test/java/",
                    "storage/src/test/java/",
                ],
                expected_outputs=["TaskResultContract"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":core:test",
                        "--tests",
                        "example.RangeScanApiTest",
                    ]
                ],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review range repair.",
                allowed_paths=["core/src/main/java/", "storage/src/main/java/"],
                forbidden_paths=["core/src/test/java/"],
                depends_on=["impl-range-api-corrective"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        acceptance_test_matrix=["Range API tests pass."],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "blocked_task_contract": {
                    "task_type": "engineering.implement",
                },
                "blocked_slice_allowed_paths": [
                    "core/src/main/java/example/api/",
                ],
                "blocked_slice_forbidden_paths": [
                    "core/src/test/java/",
                    "storage/src/test/java/",
                ],
                "failure_context": (
                    "RangeScanApiTest failed at "
                    "storage/src/main/java/example/store/BTreeRangeIndex.java:65"
                ),
            }
        },
    )

    implementer = scoped.task_slices[0]
    assert (
        "storage/src/main/java/example/store/BTreeRangeIndex.java"
        in implementer.allowed_paths
    )
    assert "core/src/main/java/example/api/" in implementer.allowed_paths
    assert "storage/src/test/java/" in implementer.forbidden_paths


def test_post_normalization_allows_narrow_corrective_hot_path_slice(
    tmp_path: Path,
) -> None:
    (tmp_path / "core/src/main/java/example/api").mkdir(parents=True)
    (tmp_path / "store/src/main/java/example/store").mkdir(parents=True)
    (tmp_path / "core/src/main/java/example/api/HotStore.java").write_text(
        "package example.api; public interface HotStore {}\n",
        encoding="utf-8",
    )
    (tmp_path / "store/src/main/java/example/store/SingleHotStore.java").write_text(
        "package example.store; import example.api.HotStore; "
        "final class SingleHotStore implements HotStore {}\n",
        encoding="utf-8",
    )
    (tmp_path / "store/src/main/java/example/store/DoubleHotStore.java").write_text(
        "package example.store; import example.api.HotStore; "
        "final class DoubleHotStore implements HotStore {}\n",
        encoding="utf-8",
    )
    plan = PlanContract(
        feature_id="wf_range",
        project="example-library",
        problem_statement="Repair zero-allocation public API interface HotStore.",
        design_contract=DesignContract(public_api="HotStore range scan API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-single-hot-store-corrective",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair the SingleHotStore allocation failure.",
                allowed_paths=[
                    "store/src/main/java/example/store/SingleHotStore.java",
                ],
                forbidden_paths=["core/src/test/java/"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["./gradlew", ":store:compileJava"]],
            )
        ],
        acceptance_test_matrix=["HotStore range scans remain zero allocation."],
    )
    project = ProjectConfig(
        name="example-library",
        root=tmp_path,
        base_branch="main",
        metadata={},
    )

    strict_errors = _post_normalization_quality_errors(
        plan,
        project=project,
        qa_write_paths=[],
    )
    corrective_errors = _post_normalization_quality_errors(
        plan,
        project=project,
        qa_write_paths=[],
        allow_narrow_corrective_slice=True,
    )

    assert any(
        error["code"] == "hot_path_implementation_surface_missing"
        for error in strict_errors
    )
    assert not any(
        error["code"] == "hot_path_implementation_surface_missing"
        for error in corrective_errors
    )
    assert any(
        error["code"] == "qa_author_missing_before_implementer"
        for error in strict_errors
    )
    assert not any(
        error["code"] == "qa_author_missing_before_implementer"
        for error in corrective_errors
    )


def test_post_normalization_allows_allocation_diagnostic_without_full_signoff(
    tmp_path: Path,
) -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="example-library",
        problem_statement="Diagnose repeated benchmark allocation failure.",
        design_contract=DesignContract(public_api="HotStore range scan API"),
        affected_surfaces=["benchmarks/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-scrutiny-diagnostic",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Name the allocation source before another repair.",
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["AllocationDiagnosisContract", "QAResultContract"],
                verification_commands=[["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            )
        ],
        acceptance_test_matrix=["Allocation source is diagnosed."],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Diagnose",
                slice_ids=["qa-scrutiny-diagnostic"],
                signoff_policy="scrutiny_and_usertest",
            )
        ],
    )
    project = ProjectConfig(
        name="example-library",
        root=tmp_path,
        base_branch="main",
        metadata={},
    )

    strict_errors = _post_normalization_quality_errors(
        plan,
        project=project,
        qa_write_paths=[],
    )
    diagnostic_errors = _post_normalization_quality_errors(
        plan,
        project=project,
        qa_write_paths=[],
        allow_narrow_corrective_slice=True,
        allow_diagnostic_corrective_slice=True,
    )

    assert any(
        error["code"] == "milestone_signoff_incomplete" for error in strict_errors
    )
    assert not any(
        error["code"] == "milestone_signoff_incomplete"
        for error in diagnostic_errors
    )


def test_apply_corrective_slice_scope_does_not_give_qa_paths_to_implementer() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct QA scrutiny benchmark failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["range smoke benchmark passes"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-mmap-range",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair mmap range scan allocation behavior.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["benchmarks/", "conformance-tests/src/test/java/"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review mmap repair.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["impl-mmap-range"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run focused feature gates.",
                allowed_paths=["benchmarks/", "conformance-tests/src/test/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["impl-mmap-range", "review", "qa-scrutiny"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.qa_verify_failed",
                "blocked_task_contract": {
                    "task_type": "engineering.qa.verify.scrutiny",
                },
                "blocked_slice_allowed_paths": [
                    "benchmarks/",
                    "conformance-tests/src/test/java/",
                ],
                "blocked_slice_forbidden_paths": [
                    "core/src/main/java/",
                    "store/src/main/java/",
                ],
                "blocker_reason": (
                    "mmap rangeScanSmoke allocation 0.008 B/op exceeds 0.005 B/op"
                ),
            }
        },
    )

    implementer = scoped.task_slices[0]
    assert implementer.task_type == "engineering.implement"
    assert implementer.allowed_paths == ["store/src/main/java/"]
    assert implementer.forbidden_paths == [
        "benchmarks/",
        "conformance-tests/src/test/java/",
    ]


def test_apply_corrective_slice_scope_keeps_one_best_implementer_slice() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct reviewer findings.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-single",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair SINGLE direct range scans.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["tests/"],
                expected_outputs=["SINGLE range repair"],
            ),
            TaskSliceContract(
                slice_id="impl-double-mmap",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair DOUBLE mmap seqlock snapshot copy and validation.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["tests/"],
                depends_on=["impl-single"],
                expected_outputs=["DOUBLE mmap seqlock repair"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["tests/"],
                depends_on=["impl-double-mmap"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify fix.",
                allowed_paths=["tests/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        acceptance_test_matrix=["reviewer finding fixed"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["impl-single", "impl-double-mmap", "review", "qa-scrutiny"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.review_rejected",
                "blocker_reason": "DOUBLE mmap range scan has seqlock snapshot bug.",
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-double-mmap",
        "review",
        "qa-scrutiny",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["impl-double-mmap"]
    assert scoped.task_slices[2].depends_on == ["review"]
    assert scoped.milestones[0].slice_ids == [
        "impl-double-mmap",
        "review",
        "qa-scrutiny",
    ]


def test_apply_corrective_slice_scope_routes_qa_owned_review_rejection_to_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct reviewer findings.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "benchmarks/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair benchmark smoke harness.",
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Repair production code only if needed.",
                allowed_paths=["core/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review repaired harness.",
                allowed_paths=["core/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        acceptance_test_matrix=["benchmark smoke harness is valid"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.review_rejected",
                "blocker_reason": (
                    "Reviewer rejected benchmark-smoke coverage in "
                    "benchmarks/src/jmh/java/RangeScanSmokeBenchmark.java."
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]


def test_apply_corrective_slice_scope_keeps_qa_author_for_qa_owned_benchmark_failure() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct benchmark gate failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Re-state design.",
                allowed_paths=["docs/"],
                forbidden_paths=["tests/"],
                expected_outputs=["DesignContract"],
            ),
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair QA-owned benchmark smoke wiring.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix production behavior if still needed.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["design", "qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "failure_context": (
                    "Missing smoke benchmark result for RangeScanVisitorBenchmark; "
                    "benchmarks/src/jmh/java was QA-authored and forbidden to implementer"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]
    assert scoped.milestones[0].slice_ids == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]


def test_apply_corrective_slice_scope_routes_benchmark_gate_failure_to_implementer() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct repeated benchmark smoke failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair benchmark smoke allocation gate.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Rewrite range scan implementation broadly.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review benchmark repair.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify benchmark smoke repair.",
                allowed_paths=["benchmarks/", "core/", "store/"],
                forbidden_paths=[],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review", "qa-scrutiny"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "same_blocker_recovery_count": 1,
                "failure_context": (
                    "benchmark_smoke_diagnostic: rangeScanSmoke allocated "
                    "0.031 B/op above threshold during :benchmarks:jmhSmokeCheck"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["impl-fix"]
    assert scoped.task_slices[2].depends_on == ["review"]
    assert scoped.milestones[0].slice_ids == [
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]


def test_apply_corrective_slice_scope_routes_material_benchmark_gate_to_implementer() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct material benchmark smoke failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair benchmark smoke allocation gate.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix RangeScanBenchmark allocation in the store hot path.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review benchmark repair.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify benchmark smoke repair.",
                allowed_paths=["benchmarks/", "core/", "store/"],
                forbidden_paths=[],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review", "qa-scrutiny"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "same_blocker_recovery_count": 1,
                "benchmark_gate_classification": "material_allocation",
                "failure_context": (
                    "benchmark_smoke_diagnostic: RangeScanBenchmark.ascendingScan "
                    "allocated 0.031 B/op above 0.005 B/op threshold; "
                    "source-level allocation uses ByteBuffer.allocate in the range loop"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["impl-fix"]
    assert scoped.task_slices[2].depends_on == ["review"]
    assert scoped.milestones[0].slice_ids == [
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]


def test_apply_corrective_slice_scope_requires_benchmark_allocation_diagnostic() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Diagnose repeated material benchmark allocation failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark allocation source is diagnosed"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair benchmark smoke allocation gate.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix RangeScanBenchmark allocation in the store hot path.",
                allowed_paths=["core/src/main/java/com/joshorig/ull/lvc/api/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review benchmark repair.",
                allowed_paths=["core/src/main/java/com/joshorig/ull/lvc/api/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny-diagnostic",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective=(
                    "Profile RangeScanBenchmark.ascendingScan and emit an "
                    "AllocationDiagnosisContract naming the allocation source."
                ),
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["AllocationDiagnosisContract", "QAResultContract"],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Confirm no user-test action is needed for the library diagnostic.",
                allowed_paths=["qa/fixtures/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["qa-scrutiny-diagnostic"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Diagnose",
                slice_ids=[
                    "qa-author-repair",
                    "impl-fix",
                    "review",
                    "qa-scrutiny-diagnostic",
                    "qa-usertest",
                ],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_verification_failed",
                "same_blocker_recovery_count": 2,
                "benchmark_gate_classification": "material_allocation",
                "benchmark_allocation_diagnosis": {
                    "classification": "material_allocation",
                    "diagnostic_required": True,
                    "failing_benchmarks": [
                        {
                            "benchmark": "RangeScanBenchmark.ascendingScan",
                            "b_op": 0.031,
                            "threshold_b_op": 0.005,
                        }
                    ],
                },
                "failure_context": (
                    "RangeScanBenchmark.ascendingScan allocated 0.031 B/op above "
                    "0.005 B/op threshold during :benchmarks:jmhSmokeCheck"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-scrutiny-diagnostic"
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[0].expected_outputs == [
        "AllocationDiagnosisContract",
        "QAResultContract",
    ]
    assert scoped.milestones[0].slice_ids == ["qa-scrutiny-diagnostic"]


def test_apply_corrective_slice_scope_routes_review_benchmark_rejection_to_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct reviewer benchmark finding.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair QA-owned StoreVisitor benchmark smoke wiring.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix production range behavior only if still needed.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review benchmark and implementation repair.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify focused feature gates.",
                allowed_paths=["benchmarks/", "conformance-tests/src/test/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review", "qa-scrutiny"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.review_rejected",
                "blocker_reason": (
                    "benchmarks/src/jmh/java/CiSmokeBenchmark.java does not call "
                    "LvcStore.ascendingRange; benchmark-smoke misses StoreVisitor API"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]
    assert scoped.task_slices[3].depends_on == ["review"]


def test_apply_corrective_slice_scope_routes_path_violation_to_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct path violation.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair QA-owned benchmark smoke wiring.",
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix production behavior if still needed.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.implementation_path_violation",
                "failure_context": (
                    "path_violations=benchmarks/src/jmh/java/RangeScanSmokeBenchmark.java:"
                    "forbidden_path"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]


def test_apply_corrective_slice_scope_routes_qa_compile_failure_to_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Correct QA compile failure.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["core/", "store/", "benchmarks/"],
        acceptance_test_matrix=["QA-authored tests compile"],
        acceptance_assertions=["QA-authored tests compile"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Repair QA-owned JMH compile failure.",
                allowed_paths=["benchmarks/src/jmh/java/", "core/src/test/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix production behavior if still needed.",
                allowed_paths=["core/src/main/java/", "store/src/main/java/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review fix.",
                allowed_paths=["core/", "store/", "benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Repair",
                slice_ids=["qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.qa_tests_do_not_compile",
                "failure_context": (
                    "command=./gradlew :benchmarks:compileJmhJava "
                    "changed_files=benchmarks/src/jmh/java/RangeScanBenchmark.java"
                ),
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]


def test_apply_corrective_slice_scope_repairs_qa_semantic_failure_with_impl_handoff() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Repair QA-authored benchmark smoke.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["benchmarks/"],
        acceptance_test_matrix=["benchmark smoke gate is valid"],
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Re-state design.",
                allowed_paths=["docs/"],
                forbidden_paths=["tests/"],
                expected_outputs=["DesignContract"],
            ),
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Rewrite reflective JMH smoke benchmark as typed harness.",
                allowed_paths=["benchmarks/src/jmh/java/", "benchmarks/build.gradle"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Fix production behavior if still needed.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review repaired QA harness.",
                allowed_paths=["benchmarks/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="QA Repair",
                slice_ids=["design", "qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.qa_semantic_quality_failed",
                "failure_context": "qa_semantic_jmh_reflective_invocation",
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]
    assert scoped.milestones[0].slice_ids == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]


def test_apply_corrective_slice_scope_keeps_qa_author_for_missing_handoff() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Restore QA handoff before implementation.",
        design_contract=DesignContract(public_api="Range API"),
        affected_surfaces=["benchmarks/", "core/", "store/"],
        acceptance_test_matrix=["QA handoff exists"],
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Re-state design.",
                allowed_paths=["docs/"],
                forbidden_paths=["tests/"],
                expected_outputs=["DesignContract"],
            ),
            TaskSliceContract(
                slice_id="qa-author-repair",
                role="qa",
                task_type="engineering.qa.author",
                objective="Restore QA handoff.",
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/", "store/src/main/java/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
            ),
            TaskSliceContract(
                slice_id="impl-fix",
                role="implementer",
                task_type="engineering.implement",
                objective="Use QA handoff to fix production behavior.",
                allowed_paths=["core/", "store/"],
                forbidden_paths=["benchmarks/"],
                depends_on=["qa-author-repair"],
                expected_outputs=["TaskResultContract"],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review repaired handoff flow.",
                allowed_paths=["benchmarks/", "core/", "store/"],
                forbidden_paths=[],
                depends_on=["impl-fix"],
                expected_outputs=["ReviewVerdictContract"],
            ),
        ],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Handoff Repair",
                slice_ids=["design", "qa-author-repair", "impl-fix", "review"],
            )
        ],
    )

    scoped = _apply_corrective_slice_scope(
        plan,
        {
            "replan_context": {
                "mode": "corrective_slice",
                "blocker_code": "engineering.qa_handoff_missing",
            }
        },
    )

    assert [task_slice.slice_id for task_slice in scoped.task_slices] == [
        "qa-author-repair",
        "impl-fix",
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []


def test_feature_scoped_verification_commands_replace_broad_smoke_gate() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["core/"],
        task_slices=[],
        acceptance_test_matrix=["ascendingRange behavior"],
    )

    commands = _feature_scoped_verification_commands(
        [
            ["./gradlew", "check"],
            ["./gradlew", "test"],
            ["./gradlew", ":core:test"],
            ["./gradlew", ":benchmarks:jmhSmokeCheck"],
            ["./qa/smoke.sh"],
        ],
        plan=plan,
        task_objective="Run range benchmark smoke.",
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["StoreVisitor", "range"],
                        "replaces": [
                            "./qa/smoke.sh",
                            "./gradlew check",
                            "./gradlew test",
                            ":benchmarks:jmhSmokeCheck",
                        ],
                        "commands": [
                            [
                                "./gradlew",
                                ":core:compileJava",
                                ":store:compileJava",
                            ],
                            [
                                "./gradlew",
                                ":core:checkstyleMain",
                                ":store:checkstyleMain",
                                ":core:checkstyleTest",
                                ":conformance-tests:checkstyleTest",
                                ":benchmarks:checkstyleJmh",
                                "--continue",
                            ],
                            [
                                "./gradlew",
                                ":benchmarks:jmhSmokeCheck",
                                "-Pjmh.smoke=true",
                            ]
                        ],
                    }
                ]
            }
        },
    )

    assert commands == [
        ["./gradlew", ":core:compileJava", ":store:compileJava"],
        [
            "./gradlew",
            ":core:checkstyleMain",
            ":store:checkstyleMain",
            ":core:checkstyleTest",
            ":conformance-tests:checkstyleTest",
            ":benchmarks:checkstyleJmh",
            "--continue",
        ],
        [
            "./gradlew",
            ":benchmarks:jmhSmokeCheck",
            "-Pjmh.smoke=true",
        ],
        ["./gradlew", ":core:test"],
    ]


def test_feature_scoped_verification_commands_drop_redundant_wildcard_test_filter() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["store/"],
        task_slices=[],
        acceptance_test_matrix=["double store range behavior"],
    )

    commands = _feature_scoped_verification_commands(
        [
            [
                "./gradlew",
                ":store:test",
                "--tests",
                "com.joshorig.ull.lvc.store.DoubleRangeScanTest",
            ],
            ["./gradlew", ":store:test", "--tests", "*Mmap*RangeScan*"],
            ["./gradlew", ":store:compileJava"],
        ],
        plan=plan,
        task_objective="Implement DOUBLE-store direct and mmap range scans.",
        project_metadata={},
    )

    assert commands == [
        [
            "./gradlew",
            ":store:test",
            "--tests",
            "com.joshorig.ull.lvc.store.DoubleRangeScanTest",
        ],
        ["./gradlew", ":store:compileJava"],
    ]


def test_apply_metadata_required_usertest_fixtures_updates_qa_author() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["Range user-test replay"]),
        affected_surfaces=["store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write range scan tests.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["core/src/main/java/"],
                expected_outputs=["core/src/test/java/RangeScanApiTest.java"],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Model-drive a CLI replay through the public range API.",
                allowed_paths=["qa/fixtures/"],
                forbidden_paths=["core/src/main/java/"],
                depends_on=["qa-author"],
                expected_outputs=["ValidationEvidence"],
            ),
        ],
        acceptance_test_matrix=["range user-test replay passes"],
    )

    normalized = _apply_metadata_required_usertest_fixtures(
        plan,
        project_metadata={
            "qa": {
                "usertest_harness": {
                    "kind": "cli_replay",
                    "required_fixture_paths": ["qa/fixtures/range_scan_usertest.jsh"],
                }
            }
        },
    )

    qa_author = normalized.task_slices[0]
    assert "qa/fixtures/" in qa_author.allowed_paths
    assert any(
        "qa/fixtures/range_scan_usertest.jsh" in output
        for output in qa_author.expected_outputs
    )
    assert any(
        "qa/fixtures/range_scan_usertest.jsh" in requirement
        for requirement in qa_author.handoff_requirements
    )
    assert not any(
        "qa/fixtures/range_scan_usertest.jsh" in output
        for output in plan.task_slices[0].expected_outputs
    )


def test_feature_scoped_verification_commands_drop_redundant_class_test_filter() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["store/"],
        task_slices=[],
        acceptance_test_matrix=["double store range behavior"],
    )

    commands = _feature_scoped_verification_commands(
        [
            [
                "./gradlew",
                ":conformance-tests:test",
                "--tests",
                "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest",
            ],
            [
                "./gradlew",
                ":conformance-tests:test",
                "--tests",
                "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest.doubleDirectAndMmapRangeScansVisitOrderedKeys",
            ],
            [
                "./gradlew",
                ":conformance-tests:test",
                "--tests",
                "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest.doubleDirectAndMmapShortPrefixMatchesMultipleKeys",
            ],
        ],
        plan=plan,
        task_objective="Implement DOUBLE-store direct and mmap range scans.",
        project_metadata={},
    )

    assert commands == [
        [
            "./gradlew",
            ":conformance-tests:test",
            "--tests",
            "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest.doubleDirectAndMmapRangeScansVisitOrderedKeys",
        ],
        [
            "./gradlew",
            ":conformance-tests:test",
            "--tests",
            "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest.doubleDirectAndMmapShortPrefixMatchesMultipleKeys",
        ],
    ]


def test_feature_scoped_verification_commands_replace_invented_method_with_project_gate() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["store/"],
        task_slices=[],
        acceptance_test_matrix=["double store range behavior"],
    )

    commands = _feature_scoped_verification_commands(
        [
            [
                "./gradlew",
                ":conformance-tests:test",
                "--tests",
                (
                    "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest."
                    "doubleStoreAscendingAndDescendingRanges"
                ),
            ],
            [
                "./gradlew",
                "--no-daemon",
                "--console=plain",
                ":benchmarks:jmhSmokeCheck",
                "-Pjmh.smoke=true",
                "-Pjmh.iterations=1",
                "-Pjmh.warmupIterations=1",
                "-Pjmh.forks=1",
                "-Pjmh.timeOnIteration=100ms",
            ],
        ],
        plan=plan,
        task_objective="Repair the range benchmark smoke failure.",
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["range", "StoreVisitor"],
                        "replaces": [
                            "./qa/smoke.sh",
                            "./gradlew check",
                            ":benchmarks:jmhSmokeCheck",
                        ],
                        "commands": [
                            [
                                "./gradlew",
                                ":conformance-tests:test",
                                "--tests",
                                (
                                    "com.joshorig.ull.lvc.conformance."
                                    "RangeScanConformanceTest"
                                ),
                            ],
                            [
                                "./gradlew",
                                "--no-daemon",
                                "--console=plain",
                                ":benchmarks:jmhSmokeCheck",
                                "-Pjmh.smoke=true",
                                "-Pjmh.iterations=1",
                                "-Pjmh.warmupIterations=1",
                                "-Pjmh.forks=1",
                                "-Pjmh.timeOnIteration=100ms",
                            ],
                        ],
                    }
                ]
            }
        },
    )

    assert commands == [
        [
            "./gradlew",
            ":conformance-tests:test",
            "--tests",
            "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest",
        ],
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":benchmarks:jmhSmokeCheck",
            "-Pjmh.smoke=true",
            "-Pjmh.iterations=1",
            "-Pjmh.warmupIterations=1",
            "-Pjmh.forks=1",
            "-Pjmh.timeOnIteration=100ms",
        ],
    ]


def test_feature_scoped_verification_commands_replace_implementer_method_filter() -> None:
    plan = PlanContract(
        feature_id="wf_feature",
        project="example-library",
        problem_statement="Implement a scoped public API behavior.",
        design_contract=DesignContract(acceptance_tests=["Feature conformance smoke"]),
        affected_surfaces=["src/"],
        task_slices=[],
        acceptance_test_matrix=["scoped feature behavior"],
    )
    method_command = [
        "./gradlew",
        ":feature-tests:test",
        "--tests",
        "com.example.FeatureConformanceTest.scopedBehaviorPasses",
    ]

    commands = _feature_scoped_verification_commands(
        [method_command],
        plan=plan,
        task_objective="Implement the scoped production slice.",
        task_type="engineering.implement",
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["public API", "behavior"],
                        "replaces": [":feature-tests:test"],
                        "commands": [
                            [
                                "./gradlew",
                                ":feature-tests:test",
                                "--tests",
                                "com.example.FeatureConformanceTest",
                            ]
                        ],
                    }
                ]
            }
        },
    )

    assert commands == [
        [
            "./gradlew",
            ":feature-tests:test",
            "--tests",
            "com.example.FeatureConformanceTest",
        ]
    ]


def test_normalize_feature_scoped_plan_verification_updates_saved_contract() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["core/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run range benchmark smoke through ./qa/smoke.sh.",
                allowed_paths=["benchmarks/src/jmh/java/"],
                forbidden_paths=["core/src/main/java/"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["./qa/smoke.sh"]],
            )
        ],
        acceptance_test_matrix=["ascendingRange behavior"],
        acceptance_assertions=["StoreVisitor range scan benchmark smoke"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Range",
                slice_ids=["qa-scrutiny"],
                validation_contract={
                    "required_gates": [
                        "./qa/smoke.sh",
                        "./gradlew :benchmarks:jmhSmokeCheck",
                    ]
                },
            )
        ],
    )

    normalized = _normalize_feature_scoped_plan_verification(
        plan,
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["StoreVisitor", "range"],
                        "replaces": [
                            "./qa/smoke.sh",
                            ":benchmarks:jmhSmokeCheck",
                        ],
                        "commands": [
                            [
                                "./gradlew",
                                "--no-daemon",
                                "--console=plain",
                                ":benchmarks:jmhSmokeCheck",
                                "-Pjmh.smoke=true",
                            ]
                        ],
                    }
                ]
            }
        },
    )

    expected = [
        [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            ":benchmarks:jmhSmokeCheck",
            "-Pjmh.smoke=true",
        ]
    ]
    assert normalized.task_slices[0].verification_commands == expected
    assert normalized.milestones[0].validation_contract["required_gates"] == [
        "./gradlew --no-daemon --console=plain :benchmarks:jmhSmokeCheck -Pjmh.smoke=true"
    ]


def test_normalize_feature_scoped_plan_verification_derives_milestone_required_gates() -> None:
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["range scan tests"]),
        affected_surfaces=["core/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement range scans.",
                allowed_paths=["store/src/main/java/"],
                forbidden_paths=["store/src/test/java/"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["./gradlew", ":store:compileJava"]],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run feature tests and smoke gates.",
                allowed_paths=["store/src/test/java/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["impl"],
                expected_outputs=["QAResultContract"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":store:test",
                        "--tests",
                        "com.example.RangeScanStoreTest",
                    ],
                    ["./gradlew", ":benchmarks:jmhSmokeCheck", "-Pjmh.smoke=true"],
                ],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Exercise the public CLI journey.",
                allowed_paths=["qa/fixtures/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["qa-scrutiny"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["jshell", "qa/fixtures/range_scan_usertest.jsh"]],
            ),
        ],
        acceptance_test_matrix=["range scans work"],
        acceptance_assertions=["range scans work"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Range",
                slice_ids=["impl", "qa-scrutiny", "qa-usertest"],
                validation_contract={"scrutiny": True, "usertest": True},
            )
        ],
    )

    normalized = _normalize_feature_scoped_plan_verification(plan, project_metadata={})

    assert normalized.milestones[0].validation_contract["required_gates"] == [
        "./gradlew :store:test --tests com.example.RangeScanStoreTest",
        "./gradlew :benchmarks:jmhSmokeCheck -Pjmh.smoke=true",
        "jshell qa/fixtures/range_scan_usertest.jsh",
    ]


def test_post_normalization_quality_rejects_broadened_variant_command(
    tmp_path: Path,
) -> None:
    (tmp_path / "core/src/main/java/example/api").mkdir(parents=True)
    (tmp_path / "store/src/main/java/example/store").mkdir(parents=True)
    (tmp_path / "core/src/main/java/example/api/HotStore.java").write_text(
        "package example.api; public interface HotStore {}\n",
        encoding="utf-8",
    )
    (tmp_path / "store/src/main/java/example/store/SingleHotStore.java").write_text(
        "package example.store; import example.api.HotStore; "
        "final class SingleHotStore implements HotStore {}\n",
        encoding="utf-8",
    )
    plan = PlanContract(
        feature_id="wf_range",
        project="example-library",
        problem_statement="Add zero-allocation public API interface HotStore.",
        design_contract=DesignContract(public_api="HotStore range scan API"),
        affected_surfaces=["core/", "store/"],
        task_slices=[
            TaskSliceContract(
                slice_id="impl-single",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement SINGLE HotStore range scans.",
                allowed_paths=[
                    "core/src/main/java/example/api/",
                    "store/src/main/java/example/store/",
                ],
                forbidden_paths=["core/src/test/java/"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":conformance-tests:test",
                        "--tests",
                        "example.RangeScanConformanceTest",
                    ]
                ],
            ),
            TaskSliceContract(
                slice_id="impl-double",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement DOUBLE HotStore range scans.",
                allowed_paths=["store/src/main/java/example/store/"],
                forbidden_paths=["core/src/test/java/"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":conformance-tests:test",
                        "--tests",
                        "example.RangeScanConformanceTest",
                    ]
                ],
            ),
        ],
        acceptance_test_matrix=["SINGLE and DOUBLE range scans pass."],
    )

    errors = _post_normalization_quality_errors(
        plan,
        project=ProjectConfig(
            name="example-library",
            root=tmp_path,
            base_branch="main",
            metadata={},
        ),
        qa_write_paths=[],
        project_metadata={
            "qa": {
                "variant_verification_rules": [
                    {
                        "conflicts": {
                            "single": ["double"],
                            "double": ["single"],
                        },
                        "broad_gate_markers": [
                            ":conformance-tests:test",
                            "RangeScanConformanceTest",
                        ],
                    }
                ]
            }
        },
    )

    assert any(
        error["code"] == "variant_slice_uses_broad_conformance_gate"
        for error in errors
    )


def test_feature_scoped_verification_commands_keep_unmatched_smoke_gate() -> None:
    plan = PlanContract(
        feature_id="wf_other",
        project="lvc-standard",
        problem_statement="Implement journal restore.",
        design_contract=DesignContract(acceptance_tests=["restore smoke"]),
        affected_surfaces=["store/"],
        task_slices=[],
        acceptance_test_matrix=["restore behavior"],
    )

    commands = _feature_scoped_verification_commands(
        [["./qa/smoke.sh"]],
        plan=plan,
        task_objective="Run smoke.",
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["range"],
                        "replaces": ["./qa/smoke.sh"],
                        "commands": [["./gradlew", ":benchmarks:jmh"]],
                    }
                ]
            }
        },
    )

    assert commands == [["./qa/smoke.sh"]]
