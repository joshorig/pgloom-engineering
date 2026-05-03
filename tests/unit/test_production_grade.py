from pathlib import Path

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
