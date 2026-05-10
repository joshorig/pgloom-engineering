from __future__ import annotations

from typing import Any

from pgloom_engineering.contracts import PlanContract, QAAuthorContract


def compact_plan_payload(plan: PlanContract) -> dict[str, Any]:
    """Plan payload for downstream role prompts.

    Planner council reports can contain full raw model responses. They are useful
    audit artifacts, but passing them to every worker causes large repeated
    token spend and can distract role agents from the accepted contract.
    """

    payload = plan.model_dump(mode="json")
    if payload.get("council_reports"):
        payload["council_reports"] = [
            {
                "iteration": item.get("iteration"),
                "critic": _compact_verdict(item.get("critic")),
                "production_grade": _compact_verdict(item.get("production_grade")),
                "planner_substance": _compact_verdict(item.get("planner_substance")),
            }
            for item in payload["council_reports"]
            if isinstance(item, dict)
        ]
    return payload


def compact_qa_author_payload(contract: QAAuthorContract) -> dict[str, Any]:
    """QA handoff payload for implementation prompts.

    Full red-proof excerpts and repair metadata are durable artifacts. The
    implementer needs the tests, matrix, commands, paths, and worktree pointer.
    """

    payload = contract.model_dump(mode="json")
    payload["red_proof"] = [
        {
            "test": item.get("test"),
            "command": item.get("command"),
            "exit_code": item.get("exit_code"),
            "output_excerpt": _short_text(item.get("output_excerpt"), limit=600),
        }
        for item in payload.get("red_proof", [])
        if isinstance(item, dict)
    ]
    payload.pop("diagnostics", None)
    return payload


def compact_task_result_payload(task_result: dict[str, Any]) -> dict[str, Any]:
    """Implementation result payload for reviewer prompts."""

    return {
        key: value
        for key, value in {
            "contract_version": task_result.get("contract_version"),
            "feature_id": task_result.get("feature_id"),
            "task_id": task_result.get("task_id"),
            "changed_files": task_result.get("changed_files"),
            "branch": task_result.get("branch"),
            "worktree_path": task_result.get("worktree_path"),
            "checks": _compact_checks(task_result.get("checks")),
            "blockers": task_result.get("blockers"),
            "artifact_ids": task_result.get("artifact_ids"),
            "procedures_attestation": task_result.get("procedures_attestation"),
            "commands_run": _compact_commands(task_result.get("commands_run")),
        }.items()
        if value not in (None, [], {})
    }


def _compact_verdict(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: value.get(key)
        for key in ["verdict", "score", "rationale", "findings", "blocking_findings"]
        if key in value
    }


def _compact_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    checks = []
    for item in value:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                key: compact
                for key, compact in {
                    "command": item.get("command"),
                    "exit_code": item.get("exit_code"),
                    "status": item.get("status"),
                    "artifact_ids": item.get("artifact_ids"),
                    "stdout_excerpt": _short_text(item.get("stdout_excerpt"), limit=600),
                    "stderr_excerpt": _short_text(item.get("stderr_excerpt"), limit=600),
                }.items()
                if compact not in (None, [], {})
            }
        )
    return checks


def _compact_commands(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    commands = []
    for item in value:
        if not isinstance(item, dict):
            continue
        commands.append(
            {
                key: compact
                for key, compact in {
                    "cmd": item.get("cmd"),
                    "exit_code": item.get("exit_code"),
                    "duration_s": item.get("duration_s"),
                    "artifact_ids": item.get("artifact_ids"),
                }.items()
                if compact not in (None, [], {})
            }
        )
    return commands


def _short_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"
