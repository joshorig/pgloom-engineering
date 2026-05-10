from __future__ import annotations

from pgloom_engineering.contracts import (
    DesignContract,
    FeatureGoalContract,
    ImplementationTopology,
    PlanContract,
    TaskSliceContract,
    validate_plan_contract,
)
from pgloom_engineering.planner.critic import (
    CriticCheckResult,
    compute_verdict,
    deterministic_check_results,
)


def test_r003_range_query_fixture_accepts_compact_plan() -> None:
    plan = _compact_range_plan()
    errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, errors)

    assert errors == []
    assert compute_verdict(checks, errors) == "accept"
    assert len(plan.task_slices) <= 7
    assert {item.role for item in plan.task_slices} == {
        "designer",
        "implementer",
        "reviewer",
        "qa",
    }


def test_r003_range_query_over_decomposed_plan_is_revised() -> None:
    plan = _over_decomposed_range_plan()
    errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, errors)
    compactness = _check(checks, "check_small_feature_compactness")

    assert compute_verdict(checks, errors) == "revise"
    assert not compactness.passed
    assert compactness.findings[0].code == "small_feature_too_many_slices"


def test_r005_schema_evolution_fixture_requires_compatibility_matrix() -> None:
    goal = _r005_goal()
    plan = _schema_evolution_plan()
    text = " ".join([goal.goal, *plan.acceptance_test_matrix]).lower()

    assert validate_plan_contract(plan) == []
    assert "v1-writer" in text
    assert "v2-reader" in text
    assert "additive" in text
    assert "sbe-adapters" in text


def test_r006_replication_plan_without_r002_dependency_is_revised() -> None:
    plan = _replication_plan_without_dependency()
    errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, errors)
    dependency_check = _check(checks, "check_roadmap_dependency_handling")

    assert compute_verdict(checks, errors) == "revise"
    assert not dependency_check.passed
    assert dependency_check.findings[0].code == "roadmap_dependency_missing"


def test_r004_compression_plan_rejects_publish_hot_path_compression() -> None:
    plan = _compression_hot_path_plan()
    errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, errors)
    hot_path_check = _check(checks, "check_hot_path_invariants")

    assert compute_verdict(checks, errors) == "revise"
    assert not hot_path_check.passed
    assert hot_path_check.findings[0].code == "hot_path_constraint_violation"


def _r005_goal() -> FeatureGoalContract:
    return FeatureGoalContract(
        project="lvc-standard",
        goal="R-005 SBE schema evolution adapters in sbe-adapters.",
        requirements=[
            "SchemaVersioned wrapper",
            "version discriminator",
            "compile-time additive-only check",
        ],
        acceptance_criteria=[
            "v1-writer to v2-reader",
            "v2-writer to v1-reader",
            "v2-writer to v2-reader",
        ],
    )


def _compact_range_plan() -> PlanContract:
    return _plan(
        feature_id="R-003",
        problem="R-003 Range-query API on stores.",
        design=DesignContract(
            public_api=(
                "Store.forEach(fromKey, toKey, StoreVisitor), ascendingRange, "
                "descendingRange"
            ),
            ownership_boundaries="store/ owns zero-allocation visitor range scan behavior",
            concurrency_protocol=(
                "range scan reads stable store state without allocating visitor wrappers"
            ),
            persistence_protocol="no persistence changes",
            hard_constraints=["zero allocation visitor hot path", "no Consumer boxing"],
            acceptance_tests=["empty range", "single-key range", "full keyspace", "reverse scan"],
        ),
        surfaces=["store/", "qa/"],
        acceptance=[
            "empty range visitor test",
            "single-key range visitor test",
            "full keyspace range test",
            "reverse descending scan test",
            "alloc gate green on visitor hot path",
        ],
        slices=[
            _design_slice("design-range", "Design zero-allocation StoreVisitor range API."),
            _qa_author_slice(["design-range"]),
            _impl_slice(
                "impl-range",
                "Implement ascendingRange and descendingRange on SINGLE and DOUBLE stores.",
                depends_on=["qa-author"],
            ),
            _review_slice(
                "review-range",
                "Review visitor allocation behavior and bounds-inclusive semantics.",
                depends_on=["impl-range"],
            ),
            _qa_verify_slice(
                "qa-verify-range",
                "Run range tests and alloc gate for visitor hot path.",
                depends_on=["review-range"],
            ),
        ],
    )


def _schema_evolution_plan() -> PlanContract:
    return _plan(
        feature_id="R-005",
        problem="R-005 SBE schema evolution adapters in sbe-adapters.",
        design=DesignContract(
            public_api="SchemaVersioned<T> and version discriminator in sbe-adapters",
            ownership_boundaries="sbe-adapters owns additive-only compatibility checks",
            concurrency_protocol="codec changes do not alter store concurrency",
            persistence_protocol="schema version discriminator controls compatible decode paths",
            hard_constraints=["alloc gate unaffected", "non-additive migrations forbidden"],
            acceptance_tests=[
                "v1-writer to v2-reader",
                "v2-writer to v1-reader",
                "v2-writer to v2-reader",
            ],
        ),
        surfaces=["sbe-adapters/", "qa/"],
        acceptance=[
            "v1-writer to v2-reader compatibility test",
            "v2-writer to v1-reader compatibility test",
            "v2-writer to v2-reader compatibility test",
            "compile-time additive-only check rejects field rename/type-change",
            "alloc gate unaffected for sbe-adapters",
        ],
        slices=[
            _design_slice("design-sbe", "Design SchemaVersioned additive-only rules."),
            _qa_author_slice(["design-sbe"]),
            _impl_slice(
                "impl-sbe",
                "Implement sbe-adapters version discriminator and additive-only check.",
                depends_on=["qa-author"],
            ),
            _review_slice(
                "review-sbe",
                "Review SBE compatibility matrix and forbidden non-additive migrations.",
                depends_on=["impl-sbe"],
            ),
            _qa_verify_slice(
                "qa-verify-sbe",
                "Run v1/v2 compatibility matrix and allocation gate.",
                depends_on=["review-sbe"],
            ),
        ],
    )


def _over_decomposed_range_plan() -> PlanContract:
    plan = _compact_range_plan()
    slices = [
        *plan.task_slices,
        _slice(
            "review-range-tests",
            "reviewer",
            "Review range-query conformance tests as a separate panel.",
            depends_on=["qa-verify-range"],
        ),
        _qa_verify_slice(
            "qa-verify-range-regression",
            "Run a separate range-query regression gate.",
            depends_on=["review-range-tests"],
        ),
        _slice(
            "historian-range",
            "historian",
            "Write unnecessary historical notes for the range-query implementation.",
            depends_on=["qa-verify-range-regression"],
        ),
    ]
    return plan.model_copy(update={"task_slices": slices})


def _replication_plan_without_dependency() -> PlanContract:
    return _plan(
        feature_id="R-006",
        problem="R-006 Distributed LVC replication over Aeron multicast.",
        design=DesignContract(
            public_api="ReplicatedStore.active and ReplicatedStore.standby factories",
            ownership_boundaries="replication module owns Aeron multicast fan-out",
            concurrency_protocol="active store fans out writes to read-only standby",
            persistence_protocol="replication stream delivers sequence-numbered records",
            hard_constraints=["standby observes writes within 50ms"],
            acceptance_tests=["two-process standby observes writes", "gap detection"],
        ),
        surfaces=["store/", "conformance-tests/", "qa/"],
        acceptance=[
            "two-process integration test",
            "standby observes all writes within 50ms",
            "gap detection via sequence numbers",
        ],
        slices=[
            _design_slice("design-replication", "Design Aeron active/standby replication."),
            _qa_author_slice(["design-replication"]),
            _impl_slice(
                "impl-replication",
                "Implement active and standby replication factories.",
                depends_on=["qa-author"],
            ),
            _review_slice(
                "review-replication",
                "Review active/standby semantics.",
                depends_on=["impl-replication"],
            ),
            _qa_verify_slice(
                "qa-verify-replication",
                "Run two-process replication tests.",
                depends_on=["review-replication"],
            ),
        ],
    )


def _compression_hot_path_plan() -> PlanContract:
    return _plan(
        feature_id="R-004",
        problem="R-004 Journal compression with LZ4.",
        design=DesignContract(
            public_api="CompressionPolicy NONE or LZ4_BLOCK",
            ownership_boundaries="journal owns compression and replay decompression",
            concurrency_protocol="compression on publish path before journal append",
            persistence_protocol="journal header flag marks compressed records",
            hard_constraints=["allocate on publish for LZ4 compression scratch buffers"],
            acceptance_tests=["compressed journals round trip", "alloc gate green"],
        ),
        surfaces=["store/", "qa/"],
        acceptance=[
            "R-002 snapshot prerequisite acknowledged before final implementation",
            "compressed journal records decompress byte-identical",
            "alloc gate green",
        ],
        slices=[
            _design_slice("design-compression", "Design LZ4 journal compression."),
            _qa_author_slice(["design-compression"]),
            _impl_slice(
                "impl-compress-publish",
                "Implement compression on publish hot path for every journal write.",
                depends_on=["qa-author"],
            ),
            _review_slice(
                "review-compression",
                "Review compression policy and allocation behavior.",
                depends_on=["impl-compress-publish"],
            ),
            _qa_verify_slice(
                "qa-verify-compression",
                "Run compression round trip and alloc gate.",
                depends_on=["review-compression"],
            ),
        ],
    )


def _plan(
    *,
    feature_id: str,
    problem: str,
    design: DesignContract,
    surfaces: list[str],
    acceptance: list[str],
    slices: list[TaskSliceContract],
) -> PlanContract:
    if not any(item.task_type == "engineering.qa.verify.usertest" for item in slices):
        scrutinies = [
            item for item in slices if item.task_type == "engineering.qa.verify.scrutiny"
        ]
        if scrutinies:
            slices = [
                *slices,
                _qa_usertest_slice(
                    f"{scrutinies[-1].slice_id}-usertest",
                    depends_on=[scrutinies[-1].slice_id],
                ),
            ]
    return PlanContract(
        feature_id=feature_id,
        project="lvc-standard",
        problem_statement=problem,
        design_contract=design,
        affected_surfaces=surfaces,
        implementation_topology=ImplementationTopology.COUNCIL_DECIDES,
        task_slices=slices,
        acceptance_test_matrix=acceptance,
        risk_register=["planner regression fixture risk"],
    )


def _design_slice(slice_id: str, objective: str) -> TaskSliceContract:
    return _slice(slice_id, "designer", objective, allowed_paths=["docs/", "store/"])


def _impl_slice(
    slice_id: str,
    objective: str,
    *,
    depends_on: list[str] | None = None,
) -> TaskSliceContract:
    return _slice(
        slice_id,
        "implementer",
        objective,
        allowed_paths=["store/", "sbe-adapters/", "conformance-tests/"],
        forbidden_paths=["tests/", "qa/fixtures/", ".git/"],
        depends_on=depends_on,
        verification_commands=[
            ["./gradlew", ":store:test", "--tests", "com.example.FeatureScopedTest"]
        ],
    )


def _review_slice(
    slice_id: str,
    objective: str,
    *,
    depends_on: list[str] | None = None,
) -> TaskSliceContract:
    return _slice(
        slice_id,
        "reviewer",
        objective,
        allowed_paths=["store/", "sbe-adapters/", "conformance-tests/", "docs/"],
        forbidden_paths=["tests/", "qa/fixtures/", ".git/"],
        depends_on=depends_on,
    )


def _qa_author_slice(depends_on: list[str]) -> TaskSliceContract:
    return _slice(
        "qa-author",
        "qa",
        "Write failing acceptance tests and fixtures before implementation.",
        task_type="engineering.qa.author",
        allowed_paths=["tests/", "qa/fixtures/"],
        forbidden_paths=["store/", "sbe-adapters/", "conformance-tests/", "docs/", ".git/"],
        depends_on=depends_on,
        expected_outputs=["QAAuthorContract"],
        verification_commands=[
            ["./gradlew", ":store:test", "--tests", "com.example.FeatureScopedTest"]
        ],
    )


def _qa_verify_slice(
    slice_id: str,
    objective: str,
    *,
    depends_on: list[str],
) -> TaskSliceContract:
    return _slice(
        slice_id,
        "qa",
        objective,
        task_type="engineering.qa.verify.scrutiny",
        allowed_paths=["tests/", "qa/fixtures/"],
        forbidden_paths=["store/", "sbe-adapters/", "conformance-tests/", "docs/", ".git/"],
        depends_on=depends_on,
        expected_outputs=["QAResultContract"],
        verification_commands=[
            ["./gradlew", ":store:checkstyleMain", ":store:checkstyleTest"],
            ["./gradlew", ":store:compileJava"],
            ["./gradlew", ":store:test", "--tests", "*Range*"],
            ["./gradlew", ":benchmarks:jmhSmokeCheck", "-Pjmh.smoke=true"],
        ],
    )


def _qa_usertest_slice(slice_id: str, *, depends_on: list[str]) -> TaskSliceContract:
    return _slice(
        slice_id,
        "qa",
        "Run a focused consumer-style CLI/API user-test harness.",
        task_type="engineering.qa.verify.usertest",
        allowed_paths=["tests/", "qa/fixtures/"],
        forbidden_paths=["store/", "sbe-adapters/", "conformance-tests/", "docs/", ".git/"],
        depends_on=depends_on,
        expected_outputs=["QAResultContract"],
        verification_commands=[
            [
                "./gradlew",
                ":store:test",
                "--tests",
                "com.example.RangeQueryUserFlowTest",
            ]
        ],
    )


def _slice(
    slice_id: str,
    role: str,
    objective: str,
    *,
    task_type: str | None = None,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    expected_outputs: list[str] | None = None,
    verification_commands: list[list[str]] | None = None,
) -> TaskSliceContract:
    return TaskSliceContract(
        slice_id=slice_id,
        role=role,
        task_type=task_type or {
            "designer": "engineering.design",
            "implementer": "engineering.implement",
            "reviewer": "engineering.review",
            "historian": "engineering.history",
        }.get(role, f"engineering.{role}"),
        objective=objective,
        allowed_paths=allowed_paths or ["store/", "sbe-adapters/", "conformance-tests/", "docs/"],
        forbidden_paths=forbidden_paths or [".git/"],
        depends_on=depends_on or [],
        expected_outputs=expected_outputs or [f"{role} contract"],
        verification_commands=verification_commands
        or [["./gradlew", ":store:test", "--tests", "com.example.FeatureScopedTest"]],
    )


def _check(checks: list[CriticCheckResult], check_id: str) -> CriticCheckResult:
    for check in checks:
        if check.check_id == check_id:
            return check
    raise AssertionError(f"missing check {check_id}")
