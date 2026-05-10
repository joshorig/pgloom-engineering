from __future__ import annotations

from typing import Any


def build_repair_brief(prior_iteration: Any) -> dict[str, Any]:
    """Build a small, actionable repair brief for the next planner iteration."""
    if prior_iteration is None:
        return {}
    validator_errors = _validator_errors(prior_iteration)
    critic_payload = _critic_payload(prior_iteration)
    findings = _critic_findings(critic_payload)
    findings.extend(_substance_findings(prior_iteration))
    codes = _codes(validator_errors, findings)
    actions = [_action_for_code(code) for code in codes]
    actions = [action for action in actions if action]
    return {
        "must_fix_codes": codes,
        "required_repairs": list(dict.fromkeys(actions)),
        "validator_errors": validator_errors,
        "critic_findings": findings[:12],
    }


def _validator_errors(prior_iteration: Any) -> list[dict[str, Any]]:
    errors = getattr(prior_iteration, "validator_errors", [])
    return [error for error in errors if isinstance(error, dict)]


def _critic_payload(prior_iteration: Any) -> dict[str, Any]:
    critic = getattr(prior_iteration, "critic", None)
    if critic is None or not hasattr(critic, "model_dump"):
        return {}
    dumped = critic.model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else {}


def _critic_findings(critic_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in critic_payload.get("findings", []):
        if isinstance(item, dict):
            findings.append(item)
    for check in critic_payload.get("per_check_results", []):
        if not isinstance(check, dict) or check.get("passed", False):
            continue
        check_id = check.get("check_id")
        for finding in check.get("findings", []):
            if isinstance(finding, dict):
                payload = dict(finding)
                payload.setdefault("check_id", check_id)
                findings.append(payload)
    return findings


def _substance_findings(prior_iteration: Any) -> list[dict[str, Any]]:
    substance = getattr(prior_iteration, "substance", None)
    if substance is None or not hasattr(substance, "model_dump"):
        return []
    dumped = substance.model_dump(mode="json")
    raw_findings = dumped.get("findings") if isinstance(dumped, dict) else None
    if not isinstance(raw_findings, list):
        return []
    return [finding for finding in raw_findings if isinstance(finding, dict)]


def _codes(
    validator_errors: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[str]:
    codes: list[str] = []
    for item in [*validator_errors, *findings]:
        code = item.get("code") or item.get("check_id")
        if isinstance(code, str) and code:
            codes.append(code)
    return list(dict.fromkeys(codes))


def _action_for_code(code: str) -> str:
    actions = {
        "qa_paths_not_restricted": (
            "Restrict every engineering.qa.author and split QA validator allowed_paths "
            "entry to registered QA/test roots only."
        ),
        "qa_author_paths_not_restricted": (
            "Make the QA author slice write only registered QA/test roots and schedule it "
            "before every implementer."
        ),
        "qa_verify_paths_not_restricted": (
            "Make QA scrutiny/user-test slices write only registered QA/test roots "
            "and schedule them after every reviewer."
        ),
        "qa_verify_not_after_reviewer": (
            "Move engineering.qa.verify.scrutiny after all reviewer slices and make "
            "engineering.qa.verify.usertest depend on scrutiny."
        ),
        "qa_usertest_uses_broad_gate": (
            "Keep smoke, benchmark-smoke, full regression, and bare project checks in "
            "engineering.qa.verify.scrutiny. Make engineering.qa.verify.usertest exercise "
            "a user-facing CLI/API/browser/app or focused consumer-style library flow."
        ),
        "missing_qa_author": (
            "Add one engineering.qa.author slice before implementers for failing tests/fixtures."
        ),
        "missing_qa_verify": (
            "Add engineering.qa.verify.scrutiny after reviewers with lint/build, "
            "feature-specific tests, and benchmark-smoke commands; add "
            "engineering.qa.verify.usertest after scrutiny."
        ),
        "implementer_claims_qa_paths": (
            "Remove registered QA/test roots from implementer allowed_paths; implementers may "
            "read tests but cannot own QA writes."
        ),
        "invalid_role_task_type": (
            "Use the canonical role/task_type mapping: designer engineering.design, "
            "implementer engineering.implement, reviewer engineering.review, qa "
            "engineering.qa.author, engineering.qa.verify.scrutiny, or "
            "engineering.qa.verify.usertest, historian engineering.history."
        ),
        "small_feature_too_many_reviewers": (
            "Compact small plans to design, qa.author, one or two implementers, one reviewer, "
            "and split QA verification. Do not add separate finalization or historian slices."
        ),
        "small_feature_too_many_slices": (
            "Reduce small or single-surface plans to 5-7 slices unless the feature has clear "
            "cross-module risk."
        ),
        "critic_failed_check_without_finding": (
            "Satisfy deterministic contract checks exactly; model critic failures without evidence "
            "are secondary to deterministic validation."
        ),
        "invalid_finalization_policy": (
            "Set finalization_policy to open_final_feature_pr_for_human_merge and do not create a "
            "separate finalization task slice."
        ),
        "planner_broad_gate_without_module_local_command": (
            "Replace broad-only slice verification with module-local build/test commands while "
            "keeping smoke/benchmark-smoke gates for feature QA verification. Full regression "
            "belongs to project-scheduled periodic validation."
        ),
        "planner_non_verification_command": (
            "Remove grep/cat/echo/list-only/dry-run commands as verification proof; use commands "
            "that compile, test, benchmark, or run configured QA gates."
        ),
        "planner_endpoint_harness_guidance_missing": (
            "For endpoint acceptance, make the QA author slice require MockMvc, WebTestClient, "
            "TestRestTemplate, or equivalent HTTP harness coverage."
        ),
        "planner_structured_assertion_guidance_missing": (
            "For payload acceptance, make the QA author slice require structured JSON/YAML "
            "field/path assertions instead of broad string containment."
        ),
        "planner_benchmark_variant_guidance_missing": (
            "For benchmark acceptance, make the QA author slice enumerate all configured "
            "benchmark variants and generate coverage for each variant."
        ),
        "variant_slice_uses_broad_conformance_gate": (
            "If implementer slices are split by SINGLE/DOUBLE/direct/mmap variant, replace "
            "broad conformance-class verification with slice-specific Gradle --tests "
            "Class.method or class filters that the QA-author slice creates. If no "
            "slice-specific test exists, merge the variant slices into one implementer "
            "slice so one worker owns the broad all-variant gate."
        ),
        "qa_benchmark_output_path_not_allowed": (
            "If the QA author slice is expected to author JMH or benchmark artifacts, include "
            "metadata-declared benchmark_roots such as benchmarks/src/jmh/java/ in allowed_paths "
            "or remove benchmark artifacts from QA expected_outputs."
        ),
        "planner_qa_expected_outputs_too_generic": (
            "Replace generic QA author expected_outputs with concrete test files, fixtures, "
            "or benchmark artifacts."
        ),
        "planner_implementation_outputs_too_generic": (
            "Replace generic implementer expected_outputs with concrete APIs, classes, files, "
            "or behavior artifacts."
        ),
        "milestone_validator_signoff_unachievable": (
            "Repair milestone gates so every scrutiny_and_usertest milestone includes both "
            "engineering.qa.verify.scrutiny and engineering.qa.verify.usertest slice ids. "
            "For a small or medium feature, prefer one terminal validation milestone "
            "containing all slices instead of lifecycle-phase milestones."
        ),
        "milestone_signoff_unachievable": (
            "Repair milestone gates so every scrutiny_and_usertest milestone includes both "
            "engineering.qa.verify.scrutiny and engineering.qa.verify.usertest slice ids. "
            "For a small or medium feature, prefer one terminal validation milestone "
            "containing all slices instead of lifecycle-phase milestones."
        ),
        "milestone_signoff_incomplete": (
            "Add the missing split validator slice ids to the milestone requiring signoff, "
            "or collapse the feature into one terminal validation milestone containing all "
            "slices."
        ),
    }
    return actions.get(code, "")
