from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from pgloom_engineering.contract_store import (
    create_council_run,
    finish_council_run,
    record_council_panelist,
)
from pgloom_engineering.contracts import (
    FeatureGoalContract,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
    canonical_acceptance_assertion_id,
    validate_plan_contract,
)
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


class InvalidCouncilProposal(BaseModel):
    panelist_id: str
    raw_response: str
    parse_error: str
    model_usage_id: int | None = None


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
        baseline_plan: dict[str, Any] | PlanContract | None = None,
        replan_from_milestone_id: str | None = None,
        frozen_prefix_slice_ids: list[str] | None = None,
        database_url: str | None = None,
    ) -> CouncilOutcome:
        iterations: list[CouncilIteration] = []
        invalid_proposals: list[InvalidCouncilProposal] = []
        prior: CouncilIteration | None = None
        baseline = _baseline_plan_contract(baseline_plan)
        council_id = _create_planner_council_run(
            feature_id=workflow_id,
            task_id=task_id,
            config=self._config,
            purpose=(
                "replan_from_milestone"
                if replan_from_milestone_id
                else "initial_plan"
            ),
            database_url=database_url,
        )
        for index in range(1, self._config.max_iterations + 1):
            skeleton = build_deterministic_plan_skeleton(
                feature_goal,
                relevant_paths=project_context.relevant_paths,
                qa_write_paths=project_context.qa_write_paths,
            )
            proposals, invalid = self._collect_proposals(
                index=index,
                feature_goal=feature_goal,
                project_context=project_context,
                plan_skeleton=skeleton,
                prior_iteration=prior,
                workflow_id=workflow_id,
                task_id=task_id,
                baseline_plan=baseline,
                replan_from_milestone_id=replan_from_milestone_id,
                frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                council_id=council_id,
                database_url=database_url,
            )
            invalid_proposals.extend(invalid)
            if not proposals:
                _finish_planner_council_run(
                    council_id,
                    status="failed",
                    iterations_used=len(iterations),
                    critic_verdict=None,
                    database_url=database_url,
                )
                raise PlannerCouncilExhausted(iterations, invalid_proposals)
            consolidator = Consolidator(
                provider=self._provider,
                profile_name=self._config.consolidator_profile,
                timeout_seconds=self._config.timeout_seconds_per_invocation,
                command=self._config.command_for(self._config.consolidator_profile),
            )
            consolidated = consolidator.merge(
                proposals=proposals,
                plan_skeleton=skeleton,
                prior_consolidated=(
                    prior.consolidated
                    if prior is not None and self._config.consolidator_scoped_inputs_enabled
                    else None
                ),
                baseline_plan=baseline,
                replan_from_milestone_id=replan_from_milestone_id,
                frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                project_context=project_context,
                workflow_id=workflow_id,
                task_id=task_id,
            )
            _record_planner_council_panelist(
                council_id,
                iteration=index - 1,
                panelist_kind="consolidator",
                panelist_ordinal=0,
                model_usage_id=consolidator.last_model_usage_id,
                database_url=database_url,
            )
            consolidated = _repair_unachievable_milestones(consolidated)
            consolidated = _normalize_project_feature_smoke_commands(
                consolidated,
                project_context=project_context,
            )
            consolidated = _normalize_acceptance_assertion_claims(consolidated)
            validator_errors = validate_plan_contract(
                consolidated,
                qa_write_paths=project_context.qa_write_paths,
            )
            production_grade = evaluate_production_grade(
                consolidated,
                project_root=project_context.project_root,
                qa_write_paths=project_context.qa_write_paths,
                project_metadata={"qa": project_context.qa_policy_summary},
            )
            validator_errors.extend(_production_grade_validator_errors(production_grade))
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
                    qa_write_paths=project_context.qa_write_paths,
                    baseline_plan=baseline,
                    frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                )
                _record_planner_council_panelist(
                    council_id,
                    iteration=index - 1,
                    panelist_kind="critic",
                    panelist_ordinal=0,
                    model_usage_id=None,
                    vote=critic.verdict,
                    database_url=database_url,
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
                    baseline_plan=baseline,
                    replan_from_milestone_id=replan_from_milestone_id,
                    frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                    workflow_id=workflow_id,
                    task_id=task_id,
                )
                _record_planner_council_panelist(
                    council_id,
                    iteration=index - 1,
                    panelist_kind="critic",
                    panelist_ordinal=0,
                    model_usage_id=critic.model_usage_id,
                    vote=critic.verdict,
                    database_url=database_url,
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
                _finish_planner_council_run(
                    council_id,
                    status="passed",
                    iterations_used=index,
                    critic_verdict=critic.verdict,
                    database_url=database_url,
                )
                return CouncilOutcome(
                    final=final,
                    iterations=iterations,
                    accepted_at_iteration=index,
                )
            prior = iteration
        _finish_planner_council_run(
            council_id,
            status="failed",
            iterations_used=len(iterations),
            critic_verdict=iterations[-1].critic.verdict if iterations else None,
            database_url=database_url,
        )
        raise PlannerCouncilExhausted(iterations, invalid_proposals)

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
        baseline_plan: PlanContract | None,
        replan_from_milestone_id: str | None,
        frozen_prefix_slice_ids: list[str] | None,
        council_id: str | None,
        database_url: str | None,
    ) -> tuple[list[CouncilProposal], list[InvalidCouncilProposal]]:
        panelist_count = self._config.panelist_count_for_iteration(index)
        if panelist_count <= 1:
            proposal, invalid = self._collect_panelist_proposal(
                panelist_index=0,
                iteration=index,
                feature_goal=feature_goal,
                project_context=project_context,
                plan_skeleton=plan_skeleton,
                prior_iteration=prior_iteration,
                workflow_id=workflow_id,
                task_id=task_id,
                baseline_plan=baseline_plan,
                replan_from_milestone_id=replan_from_milestone_id,
                frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                council_id=council_id,
                database_url=database_url,
            )
            return ([proposal] if proposal is not None else [], [invalid] if invalid else [])
        proposals_by_index: dict[int, CouncilProposal] = {}
        invalid_by_index: dict[int, InvalidCouncilProposal] = {}
        with ThreadPoolExecutor(max_workers=panelist_count) as executor:
            futures = {
                executor.submit(
                    self._collect_panelist_proposal,
                    panelist_index=panelist_index,
                    iteration=index,
                    feature_goal=feature_goal,
                    project_context=project_context,
                    plan_skeleton=plan_skeleton,
                    prior_iteration=prior_iteration,
                    workflow_id=workflow_id,
                    task_id=task_id,
                    baseline_plan=baseline_plan,
                    replan_from_milestone_id=replan_from_milestone_id,
                    frozen_prefix_slice_ids=frozen_prefix_slice_ids,
                    council_id=council_id,
                    database_url=database_url,
                ): panelist_index
                for panelist_index in range(panelist_count)
            }
            for future in as_completed(futures):
                proposal, invalid = future.result()
                if proposal is not None:
                    proposals_by_index[futures[future]] = proposal
                if invalid is not None:
                    invalid_by_index[futures[future]] = invalid
        return (
            [proposals_by_index[index] for index in sorted(proposals_by_index)],
            [invalid_by_index[index] for index in sorted(invalid_by_index)],
        )

    def _collect_panelist_proposal(
        self,
        *,
        panelist_index: int,
        iteration: int,
        feature_goal: FeatureGoalContract,
        project_context: ProjectContext,
        plan_skeleton: Any,
        prior_iteration: CouncilIteration | None,
        workflow_id: str | None,
        task_id: str | None,
        baseline_plan: PlanContract | None,
        replan_from_milestone_id: str | None,
        frozen_prefix_slice_ids: list[str] | None,
        council_id: str | None,
        database_url: str | None,
    ) -> tuple[CouncilProposal | None, InvalidCouncilProposal | None]:
        runner = PanelistRunner(
            provider=self._provider,
            profile_name=self._config.panelist_profile,
            panelist_id=f"panelist-{panelist_index}",
            timeout_seconds=self._config.timeout_seconds_per_invocation,
            command=self._config.command_for(self._config.panelist_profile),
        )
        usage_id: int | None = None
        try:
            lens = lens_for_panelist(panelist_index if iteration <= 1 else 0)
            candidate, raw, usage_id = runner.propose(
                feature_goal=feature_goal,
                project_context=apply_context_lens(project_context, lens),
                plan_skeleton=plan_skeleton,
                prior_iteration=prior_iteration,
                workflow_id=workflow_id,
                task_id=task_id,
                baseline_plan=baseline_plan,
                replan_from_milestone_id=replan_from_milestone_id,
                frozen_prefix_slice_ids=frozen_prefix_slice_ids,
            )
        except CandidateInvalid as exc:
            panelist_id = f"panelist-{panelist_index}"
            return None, InvalidCouncilProposal(
                panelist_id=panelist_id,
                raw_response=_truncate_raw(exc.raw_response),
                parse_error=exc.parse_error,
                model_usage_id=getattr(exc, "model_usage_id", None),
            )
        panelist_id = f"panelist-{panelist_index}"
        _record_planner_council_panelist(
            council_id,
            iteration=iteration - 1,
            panelist_kind="panelist",
            panelist_ordinal=panelist_index,
            model_usage_id=usage_id,
            database_url=database_url,
        )
        return CouncilProposal(
            panelist_id=panelist_id,
            candidate=candidate,
            raw_response=_truncate_raw(raw),
            model_usage_id=usage_id,
        ), None


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


def _create_planner_council_run(
    *,
    feature_id: str | None,
    task_id: str | None,
    config: CouncilConfig,
    purpose: str,
    database_url: str | None,
) -> str | None:
    if not feature_id:
        return None
    row = create_council_run(
        feature_id=str(feature_id),
        task_id=str(task_id) if task_id else None,
        role="planner",
        purpose=purpose,
        iteration_max=config.max_iterations,
        database_url=database_url,
    )
    return str(row["id"])


def _finish_planner_council_run(
    council_id: str | None,
    *,
    status: str,
    iterations_used: int,
    critic_verdict: str | None,
    database_url: str | None,
) -> None:
    if council_id is None:
        return
    finish_council_run(
        council_id,
        status=status,
        iterations_used=iterations_used,
        critic_verdict=critic_verdict,
        database_url=database_url,
    )


def _record_planner_council_panelist(
    council_id: str | None,
    *,
    iteration: int,
    panelist_kind: str,
    panelist_ordinal: int,
    model_usage_id: int | None,
    vote: str | None = None,
    database_url: str | None,
) -> None:
    if council_id is None:
        return
    record_council_panelist(
        council_id=council_id,
        iteration=iteration,
        panelist_kind=panelist_kind,
        panelist_ordinal=panelist_ordinal,
        model_usage_id=model_usage_id,
        vote=vote,
        database_url=database_url,
    )


def _baseline_plan_contract(value: dict[str, Any] | PlanContract | None) -> PlanContract | None:
    if value is None:
        return None
    if isinstance(value, PlanContract):
        return value
    if isinstance(value, dict):
        try:
            return PlanContract.model_validate(value)
        except Exception:
            return None
    return None


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


def _production_grade_validator_errors(
    report: ProductionGradeReport,
) -> list[dict[str, Any]]:
    return [
        {
            "code": finding.code,
            "message": finding.message,
            "slice_id": finding.slice_id,
            "source": "planner.production_grade",
        }
        for finding in report.blocking_findings
    ]


def _repair_unachievable_milestones(plan: PlanContract) -> PlanContract:
    """Collapse impossible validator-gated milestones into one executable gate."""
    task_type_by_id = {task_slice.slice_id: task_slice.task_type for task_slice in plan.task_slices}
    if not plan.milestones or not _has_unachievable_milestone(plan, task_type_by_id):
        return plan
    slice_ids = [task_slice.slice_id for task_slice in plan.task_slices]
    slice_types = set(task_type_by_id.values())
    if "engineering.qa.verify.scrutiny" not in slice_types:
        return plan
    if "engineering.qa.verify.usertest" not in slice_types:
        return plan
    return plan.model_copy(
        update={
            "milestones": [
                MilestoneContract(
                    milestone_id="m1",
                    name="Feature validation",
                    slice_ids=slice_ids,
                    acceptance_assertions=list(plan.acceptance_assertions),
                    validation_contract={"scrutiny": True, "usertest": True},
                    signoff_policy="scrutiny_and_usertest",
                )
            ]
        }
    )


def _has_unachievable_milestone(
    plan: PlanContract,
    task_type_by_id: dict[str, str],
) -> bool:
    for milestone in plan.milestones:
        slice_types = {task_type_by_id.get(slice_id) for slice_id in milestone.slice_ids}
        if (
            milestone.signoff_policy == "scrutiny_and_usertest"
            and "engineering.qa.verify.scrutiny" not in slice_types
            and "engineering.qa.verify.usertest" not in slice_types
        ):
            return True
        if (
            milestone.signoff_policy == "scrutiny_and_usertest"
            and (
                "engineering.qa.verify.scrutiny" not in slice_types
                or "engineering.qa.verify.usertest" not in slice_types
            )
        ):
            return True
        if (
            milestone.signoff_policy == "scrutiny_only"
            and "engineering.qa.verify.scrutiny" not in slice_types
        ):
            return True
    return False


def _normalize_acceptance_assertion_claims(plan: PlanContract) -> PlanContract:
    """Promote slice-level assertion claims into the plan/milestone indexes."""
    known = {
        canonical_acceptance_assertion_id(assertion)
        for assertion in plan.acceptance_assertions
    }
    missing_by_label: dict[str, str] = {}
    milestone_by_slice = {
        slice_id: milestone
        for milestone in plan.milestones
        for slice_id in milestone.slice_ids
    }
    milestone_missing: dict[str, dict[str, str]] = {}
    for task_slice in plan.task_slices:
        milestone = milestone_by_slice.get(task_slice.slice_id)
        milestone_known = (
            {
                canonical_acceptance_assertion_id(assertion)
                for assertion in milestone.acceptance_assertions
            }
            if milestone is not None
            else set()
        )
        for assertion in task_slice.acceptance_assertion_ids:
            canonical = canonical_acceptance_assertion_id(assertion)
            if canonical not in known:
                missing_by_label.setdefault(canonical, assertion)
            if milestone is not None and canonical not in milestone_known:
                milestone_missing.setdefault(milestone.milestone_id, {}).setdefault(
                    canonical,
                    assertion,
                )
    if not missing_by_label and not milestone_missing:
        return plan
    updated_milestones = [
        milestone.model_copy(
            update={
                "acceptance_assertions": [
                    *milestone.acceptance_assertions,
                    *milestone_missing.get(milestone.milestone_id, {}).values(),
                ]
            }
        )
        if milestone.milestone_id in milestone_missing
        else milestone
        for milestone in plan.milestones
    ]
    return plan.model_copy(
        update={
            "acceptance_assertions": [
                *plan.acceptance_assertions,
                *missing_by_label.values(),
            ],
            "milestones": updated_milestones,
        }
    )


def _normalize_project_feature_smoke_commands(
    plan: PlanContract,
    *,
    project_context: ProjectContext,
) -> PlanContract:
    rules = project_context.qa_policy_summary.get("feature_smoke_commands")
    if not isinstance(rules, list) or not rules:
        return plan
    task_slices: list[TaskSliceContract] = []
    changed = False
    for task_slice in plan.task_slices:
        if task_slice.task_type != "engineering.qa.verify.scrutiny":
            task_slices.append(task_slice)
            continue
        commands = _normalize_slice_feature_smoke_commands(
            task_slice.verification_commands,
            plan=plan,
            task_objective=task_slice.objective,
            task_type=task_slice.task_type,
            rules=rules,
        )
        if commands == task_slice.verification_commands:
            task_slices.append(task_slice)
            continue
        changed = True
        task_slices.append(task_slice.model_copy(update={"verification_commands": commands}))
    if not changed:
        return plan
    return plan.model_copy(update={"task_slices": task_slices})


def _normalize_slice_feature_smoke_commands(
    commands: list[list[str]],
    *,
    plan: PlanContract,
    task_objective: str,
    task_type: str,
    rules: list[Any],
) -> list[list[str]]:
    feature_text = " ".join(
        [
            plan.problem_statement,
            task_objective,
            " ".join(plan.acceptance_assertions),
            " ".join(plan.acceptance_test_matrix),
            " ".join(plan.design_contract.acceptance_tests),
        ]
    ).lower()
    normalized: list[list[str]] = []
    matched_rule_commands: list[list[str]] = []
    for command in commands:
        replacement = _feature_smoke_replacement(command, rules, feature_text)
        if replacement:
            normalized.extend(replacement)
            matched_rule_commands.extend(replacement)
        else:
            normalized.append(command)
    if task_type == "engineering.qa.verify.scrutiny" and not matched_rule_commands:
        for rule in rules:
            if _feature_smoke_rule_matches(rule, feature_text):
                matched_rule_commands.extend(_feature_smoke_rule_commands(rule))
    return _drop_redundant_gradle_wildcard_test_filters(
        _dedupe_commands([*normalized, *matched_rule_commands])
    )


def _feature_smoke_replacement(
    command: list[str],
    rules: list[Any],
    feature_text: str,
) -> list[list[str]]:
    command_text = " ".join(command)
    for rule in rules:
        if not _feature_smoke_rule_matches(rule, feature_text):
            continue
        replaces = [str(item) for item in rule.get("replaces", []) if isinstance(item, str)]
        if replaces and not any(item in command_text for item in replaces):
            continue
        parsed = _feature_smoke_rule_commands(rule)
        if parsed:
            return parsed
    return []


def _feature_smoke_rule_matches(rule: Any, feature_text: str) -> bool:
    if not isinstance(rule, dict):
        return False
    match_terms = [
        str(term).lower()
        for term in rule.get("match_terms", [])
        if isinstance(term, str)
    ]
    return not match_terms or any(term in feature_text for term in match_terms)


def _feature_smoke_rule_commands(rule: Any) -> list[list[str]]:
    if not isinstance(rule, dict):
        return []
    raw_commands = rule.get("commands")
    if not isinstance(raw_commands, list):
        return []
    return [
        [str(part) for part in item]
        for item in raw_commands
        if isinstance(item, list) and item
    ]


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped


def _drop_redundant_gradle_wildcard_test_filters(
    commands: list[list[str]],
) -> list[list[str]]:
    exact_test_tasks = {
        _gradle_test_task_key(command)
        for command in commands
        if _gradle_test_filter(command) and "*" not in (_gradle_test_filter(command) or "")
    }
    if not exact_test_tasks:
        return commands
    filtered: list[list[str]] = []
    for command in commands:
        test_filter = _gradle_test_filter(command)
        if (
            test_filter
            and "*" in test_filter
            and _gradle_test_task_key(command) in exact_test_tasks
        ):
            continue
        filtered.append(command)
    return filtered


def _gradle_test_filter(command: list[str]) -> str | None:
    try:
        index = command.index("--tests")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    value = command[index + 1]
    return value if isinstance(value, str) and value.strip() else None


def _gradle_test_task_key(command: list[str]) -> tuple[str, ...] | None:
    if not command:
        return None
    executable = Path(command[0]).name
    if executable not in {"gradle", "gradlew"} and command[0] != "./gradlew":
        return None
    task_parts: list[str] = []
    for part in command[1:]:
        if part == "--tests":
            break
        if part.startswith("-"):
            continue
        task_parts.append(part)
    return tuple(task_parts) if task_parts else None


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
