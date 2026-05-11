from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pgloom_engineering.planner import ProjectContext
from pgloom_engineering.planner.repair_brief import build_repair_brief
from pgloom_engineering.planner.substance import (
    evaluate_planner_substance,
    planner_qa_policy_summary,
)
from tests.unit.test_planner_council import _plan_contract


def test_substance_flags_broad_only_implementer_verification() -> None:
    plan = _plan_contract()
    impl = next(item for item in plan.task_slices if item.slice_id == "impl-store")
    impl.verification_commands = [["./qa/smoke.sh"]]

    report = evaluate_planner_substance(
        plan,
        project_context=ProjectContext(project_root=Path(".")),
    )

    assert report.verdict == "accept"
    assert any(
        finding.code == "planner_broad_gate_without_module_local_command"
        and finding.slice_id == "impl-store"
        for finding in report.findings
    )
    assert report.category_scores["verification_specificity"] < 100


def test_substance_flags_endpoint_harness_guidance_gap() -> None:
    plan = _plan_contract()
    plan.problem_statement = "Add /api/config endpoint diagnostics coverage."
    plan.acceptance_test_matrix = ["GET /api/config returns structured JSON payload."]
    context = ProjectContext(
        project_root=Path("."),
        qa_policy_summary={
            "endpoint_acceptance": {"require_http_harness": True},
            "payload_assertions": {"prefer_structured_json_paths": True},
        },
    )

    report = evaluate_planner_substance(plan, project_context=context)

    codes = {finding.code for finding in report.findings}
    assert "planner_endpoint_harness_guidance_missing" in codes
    assert "planner_structured_assertion_guidance_missing" in codes


def test_substance_accepts_explicit_endpoint_harness_and_structured_assertions() -> None:
    plan = _plan_contract()
    plan.problem_statement = "Add /api/config endpoint diagnostics coverage."
    plan.acceptance_test_matrix = ["GET /api/config returns structured JSON payload."]
    qa_author = plan.task_slices[1]
    qa_author.objective = (
        "Write failing MockMvc endpoint tests with structured JsonPath payload assertions."
    )
    qa_author.expected_outputs = [
        "app-api/src/test/java/ConfigEndpointDiagnosticsTest.java",
    ]
    context = ProjectContext(
        project_root=Path("."),
        qa_policy_summary={
            "endpoint_acceptance": {"require_http_harness": True},
            "payload_assertions": {"prefer_structured_json_paths": True},
        },
    )

    report = evaluate_planner_substance(plan, project_context=context)

    codes = {finding.code for finding in report.findings}
    assert "planner_endpoint_harness_guidance_missing" not in codes
    assert "planner_structured_assertion_guidance_missing" not in codes


def test_substance_flags_benchmark_variant_guidance_gap() -> None:
    plan = _plan_contract()
    plan.problem_statement = "Add restore JMH benchmark coverage."
    plan.acceptance_test_matrix = ["Restore benchmark covers SINGLE DOUBLE direct mmap variants."]
    context = ProjectContext(
        project_root=Path("."),
        qa_policy_summary={"benchmark_variants": ["single", "double", "direct", "mmap"]},
    )

    report = evaluate_planner_substance(plan, project_context=context)

    assert any(
        finding.code == "planner_benchmark_variant_guidance_missing"
        for finding in report.findings
    )


def test_planner_qa_policy_summary_extracts_generic_metadata() -> None:
    summary = planner_qa_policy_summary(
        {
            "qa": {
                "quality_gates": ["gate"],
                "avoid_patterns": ["avoid"],
                "required_gates": [{"id": "smoke"}],
                "feature_smoke_commands": [{"id": "range", "commands": [["./gradlew"]]}],
                "benchmark_variants": ["single"],
                "test_roots": ["core/src/test/java"],
                "benchmark_roots": ["benchmarks/src/jmh/java"],
                "semantic_conventions": {
                    "endpoint_acceptance": {"require_http_harness": True},
                    "payload_assertions": {"prefer_structured_json_paths": True},
                    "range_prefix_behavior": {"key_prefix_filter_required": True},
                },
            }
        }
    )

    assert summary["endpoint_acceptance"]["require_http_harness"] is True
    assert summary["payload_assertions"]["prefer_structured_json_paths"] is True
    assert summary["range_prefix_behavior"]["key_prefix_filter_required"] is True
    assert summary["feature_smoke_commands"][0]["id"] == "range"
    assert summary["benchmark_variants"] == ["single"]
    assert summary["test_roots"] == ["core/src/test/java"]
    assert summary["benchmark_roots"] == ["benchmarks/src/jmh/java"]


def test_repair_brief_includes_substance_findings() -> None:
    plan = _plan_contract()
    impl = next(item for item in plan.task_slices if item.slice_id == "impl-store")
    impl.verification_commands = [["./qa/smoke.sh"]]
    substance = evaluate_planner_substance(
        plan,
        project_context=ProjectContext(project_root=Path(".")),
    )
    prior = SimpleNamespace(
        validator_errors=[],
        critic=None,
        substance=substance,
    )

    brief = build_repair_brief(prior)

    assert "planner_broad_gate_without_module_local_command" in brief["must_fix_codes"]
    assert any("module-local" in item for item in brief["required_repairs"])
