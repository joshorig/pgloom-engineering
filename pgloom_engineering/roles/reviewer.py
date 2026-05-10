from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom.harness.result import HandlerResult
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.config import get_settings
from pgloom_engineering.contract_store import (
    get_active_plan_contract,
    get_task_contract,
    list_task_handoffs,
)
from pgloom_engineering.contracts import PlanContract, ReviewVerdictContract, TaskContract
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import get_project
from pgloom_engineering.qa_author_runtime import (
    command_for_worktree,
    isolate_codex_worktree_context,
    route_model_command,
)
from pgloom_engineering.qa_runtime import command_with_env, qa_env
from pgloom_engineering.role_context import build_role_context, record_role_context_usage
from pgloom_engineering.role_payloads import compact_plan_payload, compact_task_result_payload


class ReviewerHandler:
    def __init__(self, *, provider: EngineeringCLIModelProvider | None = None) -> None:
        self._provider = provider

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        payload = task.get("payload") or {}
        raw_verdict = payload.get("review_verdict_contract")
        if raw_verdict is not None:
            verdict = ReviewVerdictContract.model_validate(
                normalize_review_payload(raw_verdict)
            )
            return _done(task, verdict)

        database_url = payload.get("database_url")
        task_id = str(task.get("id"))
        feature_id = str(payload.get("feature_id") or task.get("workflow_id"))
        task_row = get_task_contract(task_id, database_url=database_url)
        plan_row = get_active_plan_contract(feature_id, database_url=database_url)
        if task_row is None or plan_row is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.review_context_missing",
                blocker_reason="review task requires active plan and TaskContract",
            )
        task_contract = TaskContract.model_validate(task_row["input_contract"])
        plan = PlanContract.model_validate(plan_row["contract"])
        task_result = _dependency_task_result(
            task_contract,
            task_id=task_id,
            database_url=database_url,
        )
        if task_result is None:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.review_handoff_missing",
                blocker_reason="review task requires an implementation TaskResultContract",
            )

        settings = get_settings()
        worktree = str(task_result.get("worktree_path") or task_result.get("worktree") or "")
        command = (
            command_for_worktree(settings.reviewer_command, Path(worktree))
            if worktree
            else list(settings.reviewer_command)
        )
        profile = CLIModelProfile(
            name=settings.reviewer_profile,
            command=command_with_env(
                isolate_codex_worktree_context(
                    route_model_command(
                        command,
                        claude_model=settings.reviewer_claude_model,
                        codex_model=settings.reviewer_codex_model,
                        codex_reasoning=settings.reviewer_codex_reasoning,
                    ),
                    worktree=Path(worktree) if worktree else Path("."),
                    context_root=getattr(settings, "role_model_context_root", Path(".")),
                    enabled=bool(
                        getattr(settings, "role_model_context_isolation_enabled", False)
                        or getattr(
                            settings,
                            "reviewer_model_context_isolation_enabled",
                            False,
                        )
                    )
                    and bool(worktree),
                ),
                qa_env({}, project_root=None),
            ),
            timeout_seconds=settings.reviewer_invocation_timeout_seconds,
        )
        provider = self._provider or EngineeringCLIModelProvider(database_url=database_url)
        try:
            project = get_project(plan.project, database_url=database_url)
        except Exception:
            project = None
        role_context = None
        if project is not None:
            role_context = build_role_context(
                role="reviewer",
                project=project,
                plan=plan,
                task_contract=task_contract,
                workflow_id=feature_id,
                database_url=database_url,
            )
        response = provider.invoke(
            profile=profile,
            prompt=build_reviewer_prompt(
                plan=plan,
                task_contract=task_contract,
                task_result=task_result,
                task_id=task_id,
                role_context=role_context.prompt_payload() if role_context else None,
            ),
            workflow_id=feature_id,
            task_id=task_id,
        )
        token_savior_usage_ids: list[int] = []
        if role_context is not None:
            usage_id = record_role_context_usage(
                role_context,
                feature_id=feature_id,
                workflow_id=feature_id,
                task_id=task_id,
                profile_name=profile.name,
                model_usage_id=response.model_usage_id,
                database_url=database_url,
            )
            if usage_id is not None:
                token_savior_usage_ids.append(usage_id)
        try:
            verdict = ReviewVerdictContract.model_validate(
                normalize_review_payload(extract_json(response.text))
            )
        except Exception as exc:
            return HandlerResult(
                status="blocked",
                blocker_code="engineering.review_contract_invalid",
                blocker_reason=str(exc),
                result={"raw_response": response.text},
            )
        verdict = verdict.model_copy(update={"feature_id": feature_id, "task_id": task_id})
        done = _done(task, verdict)
        done.result["token_savior_usage_ids"] = token_savior_usage_ids
        return done


def build_reviewer_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    task_result: dict[str, Any],
    task_id: str,
    role_context: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "role": "production_reviewer",
            "instructions": [
                (
                    "Review the implementation against the plan, task contract, "
                    "changed files, checks, and blockers."
                ),
                (
                    "Do not run verification commands, Gradle test suites, JMH, "
                    "or other long commands; QA scrutiny owns command execution. "
                    "Use the provided command evidence and targeted source/diff "
                    "inspection only."
                ),
                (
                    "Keep source inspection narrow: use rg for symbol discovery "
                    "and read only the smallest relevant ranges."
                ),
                (
                    "Approve only when the implementation is scoped, verified, "
                    "and ready for QA verification."
                ),
                (
                    "Do not block solely because QA-owned commands such as "
                    "qa/smoke.sh, browser replay, or benchmark-smoke gates have "
                    "not run yet when the plan includes downstream QA scrutiny or "
                    "user-test slices that will execute them. Treat missing "
                    "downstream validation evidence as advisory unless source "
                    "inspection shows the implementation or gate wiring is wrong."
                ),
                (
                    "Use verdict=coder_repair for implementation defects; do not "
                    "return reject because it is not a valid contract value."
                ),
                "Return only a ReviewVerdictContract JSON object.",
            ],
            "plan_contract": compact_plan_payload(plan),
            "role_context": role_context or {},
            "task_contract": task_contract.model_dump(mode="json"),
            "task_result_contract": compact_task_result_payload(task_result),
            "required_response": {
                "contract_version": "engineering.contracts.v1",
                "feature_id": plan.feature_id,
                "task_id": task_id,
                "panel": ["automated-reviewer"],
                "verdict": "approve",
                "rationale": "",
                "findings": [],
            },
        },
        indent=2,
        sort_keys=True,
    )


def normalize_review_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("ReviewVerdictContract"), dict):
        return _normalize_review_fields(payload["ReviewVerdictContract"])
    if isinstance(payload, dict) and isinstance(payload.get("review_verdict_contract"), dict):
        return _normalize_review_fields(payload["review_verdict_contract"])
    return _normalize_review_fields(payload)


def _normalize_review_fields(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    if normalized.get("verdict") in {"revise", "reject", "rejected", "fail", "failed"}:
        normalized["verdict"] = "coder_repair"
    findings = normalized.get("findings")
    if isinstance(findings, list):
        normalized["findings"] = [
            json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
            for item in findings
        ]
    return normalized


def _dependency_task_result(
    task_contract: TaskContract,
    *,
    task_id: str,
    database_url: str | None,
) -> dict[str, Any] | None:
    for handoff in reversed(
        list_task_handoffs(
            task_id,
            handoff_type="task_result",
            database_url=database_url,
        )
    ):
        contract = handoff.get("contract")
        if isinstance(contract, dict):
            return contract
    for dependency_id in reversed(task_contract.dependencies):
        row = get_task_contract(dependency_id, database_url=database_url)
        if row is None:
            continue
        output = row.get("output_contract")
        if isinstance(output, dict) and output:
            return output
    return None


def _done(task: dict[str, Any], verdict: ReviewVerdictContract) -> HandlerResult:
    return HandlerResult.done(
        {
            "role": "reviewer",
            "task_id": task.get("id"),
            "review": "multi_agent",
            "verdict": verdict.verdict,
            "review_verdict_contract": verdict.model_dump(mode="json"),
        }
    )
