from __future__ import annotations

from typing import Any


def build_repair_brief(prior_iteration: Any) -> dict[str, Any]:
    """Build a small, actionable repair brief for the next planner iteration."""
    if prior_iteration is None:
        return {}
    validator_errors = _validator_errors(prior_iteration)
    critic_payload = _critic_payload(prior_iteration)
    findings = _critic_findings(critic_payload)
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
            "Restrict every engineering.qa.author and engineering.qa.verify allowed_paths "
            "entry to registered QA/test roots only."
        ),
        "qa_author_paths_not_restricted": (
            "Make the QA author slice write only registered QA/test roots and schedule it "
            "before every implementer."
        ),
        "qa_verify_paths_not_restricted": (
            "Make the QA verify slice write only registered QA/test roots and schedule it "
            "after every reviewer."
        ),
        "qa_verify_not_after_reviewer": (
            "Move engineering.qa.verify after all reviewer slices and depend on the final reviewer."
        ),
        "missing_qa_author": (
            "Add one engineering.qa.author slice before implementers for failing tests/fixtures."
        ),
        "missing_qa_verify": (
            "Add one engineering.qa.verify slice after reviewers with smoke and full "
            "regression commands."
        ),
        "implementer_claims_qa_paths": (
            "Remove registered QA/test roots from implementer allowed_paths; implementers may "
            "read tests but cannot own QA writes."
        ),
        "invalid_role_task_type": (
            "Use the canonical role/task_type mapping: designer engineering.design, "
            "implementer engineering.implement, reviewer engineering.review, qa "
            "engineering.qa.author or engineering.qa.verify, historian engineering.history."
        ),
        "small_feature_too_many_reviewers": (
            "Compact small plans to design, qa.author, one or two implementers, one reviewer, "
            "and qa.verify. Do not add separate finalization or historian slices."
        ),
        "small_feature_too_many_slices": (
            "Reduce small or single-surface plans to 4-6 slices unless the feature has clear "
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
    }
    return actions.get(code, "")
