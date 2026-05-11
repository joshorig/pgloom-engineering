from __future__ import annotations

from pgloom_engineering.contracts import (
    DesignContract,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
)
from pgloom_engineering.roles.planner import (
    _apply_corrective_slice_scope,
    _apply_replan_supersession,
    _assign_task_slice_milestones,
    _canonicalize_plan_feature_id,
    _feature_scoped_verification_commands,
    _normalize_feature_scoped_plan_verification,
    _plan_validation_error_summary,
    _provider_usage_limit_reason,
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
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Run feature-specific verification.",
                allowed_paths=["core/src/test/java/"],
                forbidden_paths=["store/src/main/java/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
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
            ),
        ],
        acceptance_test_matrix=["descending empty intersections are safe"],
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
        "store/src/main/java/com/joshorig/ull/lvc/store/mmap/"
    ]
    assert implementer.forbidden_paths == [
        "core/src/main/java/com/joshorig/ull/lvc/metrics/"
    ]
    assert reviewer.allowed_paths == ["core/src/main/java/", "store/src/main/java/"]


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


def test_apply_corrective_slice_scope_routes_repeated_benchmark_gate_to_qa_author() -> None:
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
        "qa-author-repair",
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.task_slices[1].depends_on == ["qa-author-repair"]
    assert scoped.task_slices[2].depends_on == ["impl-fix"]
    assert scoped.task_slices[3].depends_on == ["review"]
    assert scoped.milestones[0].slice_ids == [
        "qa-author-repair",
        "impl-fix",
        "review",
        "qa-scrutiny",
    ]


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
