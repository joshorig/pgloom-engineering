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
    _canonicalize_plan_feature_id,
    _feature_scoped_verification_commands,
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
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.milestones[0].slice_ids == [
        "qa-author-repair",
        "review",
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
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []


def test_apply_corrective_slice_scope_keeps_only_qa_author_for_qa_quality_failure() -> None:
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
        "review",
    ]
    assert scoped.task_slices[0].depends_on == []
    assert scoped.milestones[0].slice_ids == [
        "qa-author-repair",
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
            ":benchmarks:jmhSmokeCheck",
            "-Pjmh.smoke=true",
        ],
        ["./gradlew", ":core:test"],
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
