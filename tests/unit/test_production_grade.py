from pathlib import Path

from pgloom_engineering.contracts import MilestoneContract
from pgloom_engineering.planner.production_grade import evaluate_production_grade
from tests.unit.test_planner_council import _plan_contract


def test_production_grade_requires_qa_roots_for_verification_commands(tmp_path: Path) -> None:
    tmp_path.joinpath("app-api/src/test/java").mkdir(parents=True)
    plan = _plan_contract()
    plan.task_slices[1].allowed_paths = ["tests/", "qa/fixtures/"]
    plan.task_slices[-1].allowed_paths = ["tests/", "qa/fixtures/"]
    plan.task_slices[-1].verification_commands = [["./gradlew", ":app-api:test"]]

    report = evaluate_production_grade(plan, project_root=tmp_path)

    assert report.verdict == "revise"
    assert any(
        finding.code == "qa_root_missing_for_verification"
        for finding in report.blocking_findings
    )


def test_production_grade_accepts_project_module_test_roots(tmp_path: Path) -> None:
    tmp_path.joinpath("app-api/src/test/java").mkdir(parents=True)
    tmp_path.joinpath("store").mkdir()
    tmp_path.joinpath("docs").mkdir()
    tmp_path.joinpath("tests").mkdir()
    tmp_path.joinpath("qa/fixtures").mkdir(parents=True)
    plan = _plan_contract()
    plan.task_slices[1].allowed_paths = ["app-api/src/test/", "qa/fixtures/"]
    plan.task_slices[-1].allowed_paths = ["app-api/src/test/", "qa/fixtures/"]
    plan.task_slices[-1].verification_commands = [["./gradlew", ":app-api:test"]]

    report = evaluate_production_grade(plan, project_root=tmp_path)

    assert not [
        finding
        for finding in report.blocking_findings
        if finding.code == "qa_root_missing_for_verification"
    ]


def test_production_grade_rejects_unachievable_milestone_signoff() -> None:
    plan = _plan_contract().model_copy(
        update={
            "milestones": [
                MilestoneContract(
                    milestone_id="m1",
                    name="Design and QA",
                    slice_ids=["design", "qa-author"],
                    acceptance_assertions=["acceptance"],
                    validation_contract={"scrutiny": True, "usertest": True},
                    signoff_policy="scrutiny_and_usertest",
                )
            ]
        }
    )

    report = evaluate_production_grade(plan)

    assert report.verdict == "revise"
    assert any(
        finding.code == "milestone_signoff_unachievable"
        for finding in report.blocking_findings
    )


def test_production_grade_rejects_benchmark_output_without_benchmark_root() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = "Write failing JMH benchmark coverage for range restore allocation."
    qa_author.expected_outputs = [
        "benchmarks/src/jmh/java/com/example/RestoreRangeBenchmark.java",
    ]
    qa_author.allowed_paths = ["tests/", "qa/fixtures/"]

    report = evaluate_production_grade(plan)

    assert report.verdict == "revise"
    assert any(
        finding.code == "qa_benchmark_output_path_not_allowed"
        for finding in report.blocking_findings
    )


def test_production_grade_accepts_benchmark_output_with_benchmark_root() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = "Write failing JMH benchmark coverage for range restore allocation."
    qa_author.expected_outputs = [
        "benchmarks/src/jmh/java/com/example/RestoreRangeBenchmark.java",
    ]
    qa_author.allowed_paths = ["tests/", "qa/fixtures/", "benchmarks/src/jmh/java/"]

    report = evaluate_production_grade(plan)

    assert not [
        finding
        for finding in report.blocking_findings
        if finding.code == "qa_benchmark_output_path_not_allowed"
    ]


def test_production_grade_does_not_treat_no_boxing_tests_as_benchmarks() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = (
        "Write failing JUnit range tests with structured int[] accumulator assertions "
        "and no ArrayList<Integer> or boxing."
    )
    qa_author.expected_outputs = [
        "core/src/test/java/com/example/LvcStoreRangeTest.java",
        "conformance-tests/src/test/java/com/example/RangeConformanceSingleTest.java",
    ]
    qa_author.allowed_paths = ["tests/", "qa/fixtures/", "core/src/test/"]
    qa_author.forbidden_paths = ["core/src/main/", "benchmarks/src/main/"]

    report = evaluate_production_grade(plan)

    assert not [
        finding
        for finding in report.blocking_findings
        if finding.code == "qa_benchmark_output_path_not_allowed"
    ]


def test_production_grade_allows_benchmark_variant_fixture_without_benchmark_root() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = (
        "Write failing range tests and add qa/fixtures/r003-benchmark-variants.txt "
        "listing required JMH benchmark variants."
    )
    qa_author.expected_outputs = [
        "core/src/test/java/com/example/LvcStoreRangeTest.java",
        "qa/fixtures/r003-benchmark-variants.txt listing required JMH variants",
    ]
    qa_author.allowed_paths = ["tests/", "qa/fixtures/", "core/src/test/"]

    report = evaluate_production_grade(plan)

    assert not [
        finding
        for finding in report.blocking_findings
        if finding.code == "qa_benchmark_output_path_not_allowed"
    ]


def test_production_grade_rejects_reflective_qa_api_tests() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = (
        "Write failing public API tests for LvcStore range scans using public API "
        "reflection/signature checks and behavior coverage."
    )
    qa_author.expected_outputs = [
        "RangeScanApiTest with Class.forName and Method.invoke signature checks",
        "Behavioral conformance tests",
    ]

    report = evaluate_production_grade(plan)

    assert report.verdict == "revise"
    assert any(
        finding.code == "qa_author_reflective_api_testing"
        and finding.slice_id == qa_author.slice_id
        for finding in report.blocking_findings
    )


def test_production_grade_rejects_variant_slice_with_broad_conformance_gate() -> None:
    plan = _plan_contract()
    plan.task_slices = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.role not in {"implementer", "reviewer"}
    ]
    plan.task_slices.extend(
        [
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-single",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement SINGLE range scans.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": ["SINGLE implementation"],
                    "verification_commands": [
                        [
                            "./gradlew",
                            ":conformance-tests:test",
                            "--tests",
                            "com.example.RangeScanConformanceTest",
                        ]
                    ],
                }
            ),
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-double",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement DOUBLE range scans.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": ["DOUBLE implementation"],
                    "verification_commands": [
                        [
                            "./gradlew",
                            ":conformance-tests:test",
                            "--tests",
                            "com.example.RangeScanConformanceTest",
                        ]
                    ],
                }
            ),
        ]
    )

    report = evaluate_production_grade(plan)

    assert report.verdict == "revise"
    assert any(
        finding.code == "variant_slice_uses_broad_conformance_gate"
        and finding.slice_id == "impl-single"
        for finding in report.blocking_findings
    )


def test_production_grade_accepts_variant_slice_with_method_conformance_gate() -> None:
    plan = _plan_contract()
    plan.task_slices = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.role not in {"implementer", "reviewer"}
    ]
    plan.task_slices.extend(
        [
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-single",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement SINGLE range scans.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": ["SINGLE implementation"],
                    "verification_commands": [
                        [
                            "./gradlew",
                            ":conformance-tests:test",
                            "--tests",
                            "com.example.RangeScanConformanceTest.singleStoreRangeSemantics",
                        ]
                    ],
                }
            ),
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-double",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement DOUBLE range scans.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": ["DOUBLE implementation"],
                    "verification_commands": [
                        [
                            "./gradlew",
                            ":conformance-tests:test",
                            "--tests",
                            "com.example.RangeScanConformanceTest.doubleStoreRangeSemantics",
                        ]
                    ],
                }
            ),
        ]
    )

    report = evaluate_production_grade(plan)

    assert not any(
        finding.code == "variant_slice_uses_broad_conformance_gate"
        for finding in report.blocking_findings
    )


def test_production_grade_rejects_variant_slice_when_outputs_mention_sibling() -> None:
    plan = _plan_contract()
    plan.task_slices = [
        task_slice
        for task_slice in plan.task_slices
        if task_slice.role not in {"implementer", "reviewer"}
    ]
    plan.task_slices.extend(
        [
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-range-api-single",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement SINGLE range scans.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": ["SINGLE direct/mmap range scan implementation"],
                    "verification_commands": [["./gradlew", ":core:test"]],
                }
            ),
            plan.task_slices[0].model_copy(
                update={
                    "slice_id": "impl-range-double-mmap",
                    "role": "implementer",
                    "task_type": "engineering.implement",
                    "objective": "Implement DOUBLE-store and remaining direct/mmap behavior.",
                    "allowed_paths": ["store/src/main/java/"],
                    "forbidden_paths": ["conformance-tests/src/test/java/"],
                    "expected_outputs": [
                        "DOUBLE direct/mmap range scan implementation",
                        "Updated store documentation for SINGLE and DOUBLE range scans",
                    ],
                    "verification_commands": [
                        [
                            "./gradlew",
                            ":conformance-tests:test",
                            "--tests",
                            "com.example.RangeScanConformanceTest",
                        ]
                    ],
                }
            ),
        ]
    )

    report = evaluate_production_grade(plan)

    assert report.verdict == "revise"
    assert any(
        finding.code == "variant_slice_uses_broad_conformance_gate"
        and finding.slice_id == "impl-range-double-mmap"
        for finding in report.blocking_findings
    )
