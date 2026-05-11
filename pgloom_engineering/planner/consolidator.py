from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.contracts import PlanContract, validate_plan_contract
from pgloom_engineering.planner.critic import ModelProvider
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.planner.plan_skeleton import (
    DeterministicPlanSkeleton,
    skeleton_prompt_payload,
)
from pgloom_engineering.planner.plan_summary import candidate_summary
from pgloom_engineering.role_gate_contracts import build_planner_gate_contract


class ProposalLike(Protocol):
    candidate: PlanContract


class Consolidator:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        profile_name: str,
        timeout_seconds: float = 300.0,
        command: list[str] | None = None,
    ) -> None:
        self._provider = provider
        self._profile = CLIModelProfile(
            name=profile_name,
            command=command or ["cat"],
            timeout_seconds=timeout_seconds,
            parse_response="text",
        )
        self.last_model_usage_id: int | None = None

    def merge(
        self,
        *,
        proposals: Sequence[ProposalLike],
        plan_skeleton: DeterministicPlanSkeleton | None = None,
        prior_consolidated: PlanContract | None = None,
        baseline_plan: PlanContract | None = None,
        replan_from_milestone_id: str | None = None,
        frozen_prefix_slice_ids: list[str] | None = None,
        project_context: object | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> PlanContract:
        prompt = self._build_prompt(
            proposals,
            plan_skeleton,
            prior_consolidated,
            baseline_plan,
            replan_from_milestone_id,
            frozen_prefix_slice_ids,
            project_context,
        )
        response = self._provider.invoke(
            profile=self._profile,
            prompt=prompt,
            workflow_id=workflow_id,
            task_id=task_id,
        )
        self.last_model_usage_id = getattr(response, "model_usage_id", None)
        raw = str(getattr(response, "text", ""))
        try:
            payload = extract_json(raw)
            return PlanContract.model_validate(payload)
        except Exception:
            return min(
                (proposal.candidate for proposal in proposals),
                key=lambda candidate: len(validate_plan_contract(candidate)),
            )

    def _build_prompt(
        self,
        proposals: Sequence[ProposalLike],
        plan_skeleton: DeterministicPlanSkeleton | None,
        prior_consolidated: PlanContract | None = None,
        baseline_plan: PlanContract | None = None,
        replan_from_milestone_id: str | None = None,
        frozen_prefix_slice_ids: list[str] | None = None,
        project_context: object | None = None,
    ) -> str:
        base = Path(__file__).with_name("prompts").joinpath("consolidator.md").read_text(
            encoding="utf-8"
        )
        payload = [candidate_summary(proposal.candidate) for proposal in proposals]
        prior_payload = candidate_summary(prior_consolidated) if prior_consolidated else None
        return (
            base
            + "\n\nDETERMINISTIC_PLAN_SKELETON:\n"
            + json.dumps(
                skeleton_prompt_payload(plan_skeleton) if plan_skeleton else {},
                indent=2,
                sort_keys=True,
            )
            + "\n\nCANDIDATE_PLAN_SUMMARIES:\n"
            + json.dumps(payload, indent=2, sort_keys=True)
            + "\n\nROLE_GATE_CONTRACT:\n"
            + json.dumps(
                build_planner_gate_contract(project_context=project_context),
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n\nPRIOR_CONSOLIDATED_BASELINE:\n"
            + json.dumps(prior_payload, indent=2, sort_keys=True)
            + "\n\nINHERIT_BASELINE_MODE:\n"
            + json.dumps(
                _baseline_payload(
                    baseline_plan,
                    replan_from_milestone_id,
                    frozen_prefix_slice_ids,
                ),
                indent=2,
                sort_keys=True,
            )
        )


def _baseline_payload(
    baseline_plan: PlanContract | None,
    replan_from_milestone_id: str | None,
    frozen_prefix_slice_ids: list[str] | None,
) -> dict[str, object]:
    if baseline_plan is None or not replan_from_milestone_id:
        return {"enabled": False}
    frozen = set(frozen_prefix_slice_ids or [])
    return {
        "enabled": True,
        "replan_from_milestone_id": replan_from_milestone_id,
        "frozen_prefix_slice_ids": sorted(frozen),
        "baseline_frozen_slices": [
            item.model_dump(mode="json")
            for item in baseline_plan.task_slices
            if item.slice_id in frozen
        ],
        "instruction": (
            "The final PlanContract must preserve each baseline_frozen_slices object "
            "exactly and replace only the requested milestone plus downstream work."
        ),
    }
