from __future__ import annotations

from pathlib import Path

from pgloom_engineering.contracts import DesignContract, PlanContract
from pgloom_engineering.projects import load_projects_file
from pgloom_engineering.roles.planner import _feature_scoped_verification_commands


def test_lvc_range_feature_smoke_avoids_broad_conformance_test_gate() -> None:
    registry = Path("docs/evals/project-registry.yaml")
    project = next(
        item for item in load_projects_file(registry) if item.name == "lvc-standard"
    )
    metadata = project.metadata
    qa = metadata["qa"]
    rule = next(
        item
        for item in qa["feature_smoke_commands"]
        if item["id"] == "range_scan_jmh_smoke"
    )

    assert not any(":conformance-tests:test" in command for command in rule["commands"])
    assert ":conformance-tests:test" in rule["replaces"]
    assert "RangeScanConformanceTest" in rule["replaces"]


def test_lvc_range_feature_smoke_replaces_broad_conformance_with_focused_gates() -> None:
    registry = Path("docs/evals/project-registry.yaml")
    project = next(
        item for item in load_projects_file(registry) if item.name == "lvc-standard"
    )
    plan = PlanContract(
        feature_id="wf_range",
        project="lvc-standard",
        problem_statement="Implement StoreVisitor range scans.",
        design_contract=DesignContract(acceptance_tests=["RangeScanBenchmark smoke"]),
        affected_surfaces=["core/", "store/"],
        task_slices=[],
        acceptance_test_matrix=["ascendingRange behavior"],
        acceptance_assertions=["StoreVisitor range scan benchmark smoke"],
    )

    commands = _feature_scoped_verification_commands(
        [
            [
                "./gradlew",
                ":conformance-tests:test",
                "--tests",
                "com.joshorig.ull.lvc.conformance.RangeScanConformanceTest",
            ]
        ],
        plan=plan,
        task_objective="Run R-003 range scrutiny.",
        task_type="engineering.qa.verify.scrutiny",
        project_metadata=project.metadata,
    )

    flattened = [" ".join(command) for command in commands]
    assert not any(":conformance-tests:test" in command for command in flattened)
    assert any(
        ":core:test --tests com.joshorig.ull.lvc.api.RangeScanApiTest" in command
        for command in flattened
    )
    assert any(":benchmarks:jmhSmokeCheck" in command for command in flattened)
