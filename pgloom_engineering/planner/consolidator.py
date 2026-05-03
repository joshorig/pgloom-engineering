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

    def merge(
        self,
        *,
        proposals: Sequence[ProposalLike],
        plan_skeleton: DeterministicPlanSkeleton | None = None,
        prior_consolidated: PlanContract | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> PlanContract:
        prompt = self._build_prompt(proposals, plan_skeleton, prior_consolidated)
        response = self._provider.invoke(
            profile=self._profile,
            prompt=prompt,
            workflow_id=workflow_id,
            task_id=task_id,
        )
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
            + "\n\nPRIOR_CONSOLIDATED_BASELINE:\n"
            + json.dumps(prior_payload, indent=2, sort_keys=True)
        )
