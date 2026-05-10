from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.contracts import FeatureGoalContract, PlanContract
from pgloom_engineering.planner.critic import ModelProvider
from pgloom_engineering.planner.exceptions import CandidateInvalid
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.planner.plan_skeleton import (
    DeterministicPlanSkeleton,
    skeleton_prompt_payload,
)
from pgloom_engineering.planner.plan_summary import candidate_summary
from pgloom_engineering.planner.repair_brief import build_repair_brief


class PanelistRunner:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        profile_name: str,
        panelist_id: str,
        timeout_seconds: float = 300.0,
        command: list[str] | None = None,
    ) -> None:
        self.panelist_id = panelist_id
        self._provider = provider
        self._profile = CLIModelProfile(
            name=profile_name,
            command=command or ["cat"],
            timeout_seconds=timeout_seconds,
            parse_response="text",
        )

    def propose(
        self,
        *,
        feature_goal: FeatureGoalContract,
        project_context: Any,
        plan_skeleton: DeterministicPlanSkeleton | None = None,
        prior_iteration: Any = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[PlanContract, str, int | None]:
        prompt = self._build_prompt(
            feature_goal,
            project_context,
            prior_iteration,
            plan_skeleton,
        )
        response = self._provider.invoke(
            profile=self._profile,
            prompt=prompt,
            workflow_id=workflow_id,
            task_id=task_id,
        )
        raw = str(getattr(response, "text", ""))
        try:
            payload = extract_json(raw)
            candidate = PlanContract.model_validate(payload)
        except Exception as exc:
            raise CandidateInvalid(
                raw,
                str(exc),
                model_usage_id=getattr(response, "model_usage_id", None),
            ) from exc
        return candidate, raw, getattr(response, "model_usage_id", None)

    def _build_prompt(
        self,
        feature_goal: FeatureGoalContract,
        project_context: Any,
        prior_iteration: Any,
        plan_skeleton: DeterministicPlanSkeleton | None,
    ) -> str:
        base = Path(__file__).with_name("prompts").joinpath("panelist.md").read_text(
            encoding="utf-8"
        )
        return (
            base
            + "\n\nFEATURE_GOAL:\n"
            + json.dumps(feature_goal.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n\nPROJECT_CONTEXT:\n"
            + json.dumps(_dump_context(project_context), indent=2, sort_keys=True, default=str)
            + "\n\nDETERMINISTIC_PLAN_SKELETON:\n"
            + json.dumps(
                skeleton_prompt_payload(plan_skeleton) if plan_skeleton else {},
                indent=2,
                sort_keys=True,
            )
            + "\n\nPRIOR_ITERATION:\n"
            + json.dumps(_dump_iteration(prior_iteration), indent=2, sort_keys=True, default=str)
        )


def _dump_context(project_context: Any) -> dict[str, Any]:
    if hasattr(project_context, "model_dump"):
        dumped = project_context.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    return {}


def _dump_iteration(prior_iteration: Any) -> dict[str, Any]:
    if prior_iteration is None:
        return {}
    payload: dict[str, Any] = {
        "validator_errors": getattr(prior_iteration, "validator_errors", []),
        "repair_brief": build_repair_brief(prior_iteration),
    }
    consolidated = getattr(prior_iteration, "consolidated", None)
    if isinstance(consolidated, PlanContract):
        payload["consolidated_summary"] = candidate_summary(consolidated)
    critic = getattr(prior_iteration, "critic", None)
    if critic is not None and hasattr(critic, "model_dump"):
        critic_payload = critic.model_dump(mode="json")
        payload["critic"] = {
            "verdict": critic_payload.get("verdict"),
            "rationale": critic_payload.get("rationale"),
            "findings": critic_payload.get("findings", []),
            "failed_checks": [
                {
                    "check_id": item.get("check_id"),
                    "findings": item.get("findings", []),
                }
                for item in critic_payload.get("per_check_results", [])
                if isinstance(item, dict) and not item.get("passed", False)
            ],
        }
    return payload
