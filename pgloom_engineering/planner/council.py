from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from pgloom_engineering.contracts import FeatureGoalContract, PlanContract, validate_plan_contract
from pgloom_engineering.planner.consolidator import Consolidator
from pgloom_engineering.planner.context_lens import apply_context_lens, lens_for_panelist
from pgloom_engineering.planner.critic import (
    CriticRunner,
    CriticVerdict,
    ModelProvider,
    deterministic_accept_verdict,
)
from pgloom_engineering.planner.exceptions import CandidateInvalid, PlannerCouncilExhausted
from pgloom_engineering.planner.panelist import PanelistRunner
from pgloom_engineering.planner.plan_skeleton import build_deterministic_plan_skeleton
from pgloom_engineering.planner.production_grade import (
    ProductionGradeReport,
    evaluate_production_grade,
)
from pgloom_engineering.planner.substance import (
    PlannerSubstanceReport,
    evaluate_planner_substance,
)


class CouncilConfig(BaseModel):
    panelist_count: int = 3
    max_iterations: int = 3
    panelist_profile: str = "planner-panelist"
    critic_profile: str = "planner-critic"
    consolidator_profile: str = "planner-consolidator"
    timeout_seconds_per_invocation: float = 300.0
    command: list[str] = Field(default_factory=lambda: ["cat"])
    profile_commands: dict[str, list[str]] = Field(default_factory=dict)
    iter_1_panelist_count: int | None = None
    iter_2_panelist_count: int = 1
    consolidator_scoped_inputs_enabled: bool = True
    production_grade_preempts_critic: bool = True
    production_grade_critic_sample_rate: float = 0.1

    @field_validator("panelist_count")
    @classmethod
    def require_multi_agent(cls, value: int) -> int:
        if value < 2:
            raise ValueError("planner council requires at least two panelists")
        return value

    def command_for(self, profile_name: str) -> list[str]:
        return self.profile_commands.get(profile_name, self.command)

    def panelist_count_for_iteration(self, iteration: int) -> int:
        if iteration <= 1:
            return self.iter_1_panelist_count or self.panelist_count
        return max(1, min(self.iter_2_panelist_count, self.panelist_count))


class ProjectContext(BaseModel):
    project_root: Path
    roadmap_excerpt: str = ""
    decisions_excerpt: str = ""
    qa_smoke_path: Path | None = None
    qa_regression_path: Path | None = None
    relevant_paths: list[str] = Field(default_factory=list)
    qa_write_paths: list[str] = Field(default_factory=lambda: ["tests/", "qa/fixtures/"])
    qa_policy_summary: dict[str, Any] = Field(default_factory=dict)
    context_lens: str = "shared"
    lens_focus: list[str] = Field(default_factory=list)


class CouncilProposal(BaseModel):
    panelist_id: str
    candidate: PlanContract
    raw_response: str
    model_usage_id: int | None = None


class CouncilIteration(BaseModel):
    iteration: int
    proposals: list[CouncilProposal]
    consolidated: PlanContract
    critic: CriticVerdict
    validator_errors: list[dict[str, Any]]
    production_grade: ProductionGradeReport | None = None
    substance: PlannerSubstanceReport | None = None


class CouncilOutcome(BaseModel):
    final: PlanContract
    iterations: list[CouncilIteration]
    accepted_at_iteration: int


class PlannerCouncil:
    def __init__(
        self,
        *,
        config: CouncilConfig,
        provider: ModelProvider,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._config = config
        self._provider = provider
        self._clock = clock

    def run(
        self,
        *,
        feature_goal: FeatureGoalContract,
        project_context: ProjectContext,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> CouncilOutcome:
        iterations: list[CouncilIteration] = []
        prior: CouncilIteration | None = None
        for index in range(1, self._config.max_iterations + 1):
            skeleton = build_deterministic_plan_skeleton(
                feature_goal,
                relevant_paths=project_context.relevant_paths,
                qa_write_paths=project_context.qa_write_paths,
            )
            proposals = self._collect_proposals(
                index=index,
                feature_goal=feature_goal,
                project_context=project_context,
                plan_skeleton=skeleton,
                prior_iteration=prior,
                workflow_id=workflow_id,
                task_id=task_id,
            )
            if not proposals:
                raise PlannerCouncilExhausted(iterations)
            consolidated = Consolidator(
                provider=self._provider,
                profile_name=self._config.consolidator_profile,
                timeout_seconds=self._config.timeout_seconds_per_invocation,
                command=self._config.command_for(self._config.consolidator_profile),
            ).merge(
                proposals=proposals,
                plan_skeleton=skeleton,
                prior_consolidated=(
                    prior.consolidated
                    if prior is not None and self._config.consolidator_scoped_inputs_enabled
                    else None
                ),
                workflow_id=workflow_id,
                task_id=task_id,
            )
            validator_errors = validate_plan_contract(consolidated)
            production_grade = evaluate_production_grade(
                consolidated,
                project_root=project_context.project_root,
            )
            substance = evaluate_planner_substance(
                consolidated,
                project_context=project_context,
            )
            if (
                self._config.production_grade_preempts_critic
                and production_grade.verdict == "accept"
                and production_grade.score == 100
                and not validator_errors
                and not _sample_model_critic(
                    feature_key=consolidated.feature_id,
                    iteration=index,
                    sample_rate=self._config.production_grade_critic_sample_rate,
                )
            ):
                critic = deterministic_accept_verdict(
                    plan=consolidated,
                    validator_errors=validator_errors,
                    rationale="production_grade accepted cleanly; model critic preempted",
                    preempted=True,
                )
            else:
                critic = CriticRunner(
                    provider=self._provider,
                    profile_name=self._config.critic_profile,
                    timeout_seconds=self._config.timeout_seconds_per_invocation,
                    command=self._config.command_for(self._config.critic_profile),
                ).review(
                    plan=consolidated,
                    project_context=project_context,
                    validator_errors=validator_errors,
                    workflow_id=workflow_id,
                    task_id=task_id,
                )
            iteration = CouncilIteration(
                iteration=index,
                proposals=proposals,
                consolidated=consolidated,
                critic=critic,
                validator_errors=validator_errors,
                production_grade=production_grade,
                substance=substance,
            )
            iterations.append(iteration)
            if critic.verdict == "accept" and not validator_errors:
                final = consolidated.model_copy(
                    update={"council_reports": _council_reports(iterations)}
                )
                return CouncilOutcome(
                    final=final,
                    iterations=iterations,
                    accepted_at_iteration=index,
                )
            prior = iteration
        raise PlannerCouncilExhausted(iterations)

    def _collect_proposals(
        self,
        *,
        index: int,
        feature_goal: FeatureGoalContract,
        project_context: ProjectContext,
        plan_skeleton: Any,
        prior_iteration: CouncilIteration | None,
        workflow_id: str | None,
        task_id: str | None,
    ) -> list[CouncilProposal]:
        proposals: list[CouncilProposal] = []
        panelist_count = self._config.panelist_count_for_iteration(index)
        for panelist_index in range(panelist_count):
            runner = PanelistRunner(
                provider=self._provider,
                profile_name=self._config.panelist_profile,
                panelist_id=f"panelist-{panelist_index}",
                timeout_seconds=self._config.timeout_seconds_per_invocation,
                command=self._config.command_for(self._config.panelist_profile),
            )
            try:
                lens = lens_for_panelist(panelist_index if index <= 1 else 0)
                candidate, raw, usage_id = runner.propose(
                    feature_goal=feature_goal,
                    project_context=apply_context_lens(project_context, lens),
                    plan_skeleton=plan_skeleton,
                    prior_iteration=prior_iteration,
                    workflow_id=workflow_id,
                    task_id=task_id,
                )
            except CandidateInvalid:
                continue
            proposals.append(
                CouncilProposal(
                    panelist_id=f"panelist-{panelist_index}",
                    candidate=candidate,
                    raw_response=_truncate_raw(raw),
                    model_usage_id=usage_id,
                )
            )
        return proposals


def run_council(
    *,
    feature_goal: FeatureGoalContract,
    project_context: ProjectContext,
    config: CouncilConfig,
    provider: ModelProvider,
) -> CouncilOutcome:
    return PlannerCouncil(config=config, provider=provider).run(
        feature_goal=feature_goal,
        project_context=project_context,
    )


def _council_reports(iterations: list[CouncilIteration]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for iteration in iterations:
        reports.append(
            {
                "iteration": iteration.iteration,
                "proposals": [
                    {
                        "panelist_id": proposal.panelist_id,
                        "raw_response": _truncate_raw(proposal.raw_response),
                        "model_usage_id": proposal.model_usage_id,
                    }
                    for proposal in iteration.proposals
                ],
                "critic": iteration.critic.model_dump(mode="json"),
                "validator_errors": iteration.validator_errors,
                "production_grade": (
                    iteration.production_grade.model_dump(mode="json")
                    if iteration.production_grade is not None
                    else None
                ),
                "planner_substance": (
                    iteration.substance.model_dump(mode="json")
                    if iteration.substance is not None
                    else None
                ),
            }
        )
    return reports


def _truncate_raw(value: str) -> str:
    if len(value) <= 32768:
        return value
    return value[:32768] + "...[truncated]"


def _sample_model_critic(*, feature_key: str, iteration: int, sample_rate: float) -> bool:
    if sample_rate <= 0:
        return False
    if sample_rate >= 1:
        return True
    digest = hashlib.sha256(f"{feature_key}:{iteration}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % 10_000
    return bucket < int(sample_rate * 10_000)
