from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.contracts import (
    DesignContract,
    FeatureGoalContract,
    ImplementationTopology,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
)
from pgloom_engineering.planner import CouncilConfig, PlannerCouncil, ProjectContext
from pgloom_engineering.planner.council import _repair_unachievable_milestones
from pgloom_engineering.planner.critic import (
    RUBRIC_CHECKS,
    CriticRunner,
    compute_verdict,
    deterministic_check_results,
    enforce_deterministic_failures,
    reconcile_model_results_with_deterministic_checks,
)
from pgloom_engineering.planner.exceptions import CandidateInvalid, PlannerCouncilExhausted
from pgloom_engineering.planner.panelist import PanelistRunner
from pgloom_engineering.planner.plan_skeleton import build_deterministic_plan_skeleton
from pgloom_engineering.planner.repair_brief import build_repair_brief
from pgloom_engineering.roles.planner import (
    _adaptive_panelist_count,
    _merge_project_context_metadata,
    _project_metadata_qa_write_paths,
    _route_model_command,
)


class FakeProvider:
    def __init__(self, responses: dict[str, list[object]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self._lock = threading.Lock()

    def invoke(
        self,
        *,
        profile: CLIModelProfile,
        prompt: str,
        input_tokens_hint: int | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> Any:
        del prompt, input_tokens_hint, workflow_id, task_id
        with self._lock:
            queue = self.responses[profile.name]
            value = queue.pop(0)
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value)
        return SimpleNamespace(text=text, model_usage_id=None)


class RecordingProvider(FakeProvider):
    def __init__(self, responses: dict[str, list[object]]) -> None:
        super().__init__(responses)
        self.commands: list[tuple[str, list[str]]] = []
        self.prompts: list[tuple[str, str]] = []

    def invoke(
        self,
        *,
        profile: CLIModelProfile,
        prompt: str,
        input_tokens_hint: int | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> Any:
        with self._lock:
            self.commands.append((profile.name, profile.command))
            self.prompts.append((profile.name, prompt))
        return super().invoke(
            profile=profile,
            prompt=prompt,
            input_tokens_hint=input_tokens_hint,
            workflow_id=workflow_id,
            task_id=task_id,
        )


def test_panelist_propose_returns_pydantic_plan_contract() -> None:
    plan = _plan_contract()
    provider = FakeProvider({"planner-panelist": [plan.model_dump(mode="json")]})
    candidate, raw, _usage = PanelistRunner(
        provider=provider,
        profile_name="planner-panelist",
        panelist_id="panelist-0",
    ).propose(feature_goal=_feature_goal(), project_context=_context())
    assert candidate == plan
    assert raw


def test_panelist_propose_raises_candidate_invalid_on_unparseable_output() -> None:
    provider = FakeProvider({"planner-panelist": ["not json"]})
    with pytest.raises(CandidateInvalid) as raised:
        PanelistRunner(
            provider=provider,
            profile_name="planner-panelist",
            panelist_id="panelist-0",
        ).propose(feature_goal=_feature_goal(), project_context=_context())
    assert raised.value.raw_response == "not json"


def test_critic_blocks_on_missing_lifecycle_coverage() -> None:
    plan = _plan_contract(acceptance=["restore round trip only"])
    errors = [{"code": "planner_contract_incomplete", "message": "missing invariant"}]
    results = deterministic_check_results(plan, errors)
    verdict = compute_verdict(results, errors)
    assert verdict == "revise"
    assert any(result.check_id == "check_lifecycle_coverage" for result in results)


def test_critic_blocks_unachievable_milestone_validator_signoff() -> None:
    plan = _plan_contract().model_copy(
        update={
            "milestones": [
                MilestoneContract(
                    milestone_id="m1",
                    name="Design and QA",
                    slice_ids=["design", "qa-author"],
                    acceptance_assertions=["acceptance"],
                    validation_contract={"scrutiny": True, "usertest": True},
                    signoff_policy="scrutiny_and_usertest",
                )
            ]
        }
    )

    results = deterministic_check_results(plan, [])

    milestone_check = next(
        result for result in results if result.check_id == "check_milestones_present"
    )
    assert not milestone_check.passed
    assert milestone_check.findings[0].code == "milestone_validator_signoff_unachievable"


def test_critic_blocks_same_slice_allowed_forbidden_overlap() -> None:
    plan = _plan_contract()
    plan.task_slices[0].forbidden_paths = ["docs/adr/"]

    results = deterministic_check_results(plan, [])

    overlap = next(
        result for result in results if result.check_id == "check_forbidden_path_overlap"
    )
    assert not overlap.passed
    assert overlap.findings[0].code == "slice_allowed_forbidden_overlap"


def test_critic_accepts_clean_plan() -> None:
    plan = _plan_contract()
    provider = FakeProvider(
        {
            "planner-critic": [
                {
                    "rationale": "clean",
                    "per_check_results": [
                        {
                            "check_id": check.check_id,
                            "name": check.name,
                            "passed": True,
                            "severity_if_failed": check.severity_if_failed,
                            "findings": [],
                        }
                        for check in RUBRIC_CHECKS
                    ],
                }
            ]
        }
    )
    verdict = CriticRunner(provider=provider, profile_name="planner-critic").review(
        plan=plan,
        project_context=_context(),
        validator_errors=[],
    )
    assert verdict.verdict == "accept"
    assert verdict.findings == []


def test_critic_reconciles_unsupported_model_failure_with_deterministic_pass() -> None:
    plan = _plan_contract()
    deterministic = deterministic_check_results(plan, [])
    model_results = [
        result.model_copy(
            update={
                "passed": False,
                "findings": [
                    {
                        "severity": result.severity_if_failed,
                        "check_id": result.check_id,
                        "code": "critic_failed_check_without_finding",
                        "message": "model did not explain failure",
                    }
                ],
            }
        )
        if result.check_id == "check_qa_paths_disjoint"
        else result
        for result in deterministic
    ]

    reconciled = reconcile_model_results_with_deterministic_checks(
        model_results=model_results,
        deterministic_results=deterministic,
    )

    assert compute_verdict(reconciled, []) == "accept"
    assert next(
        result for result in reconciled if result.check_id == "check_qa_paths_disjoint"
    ).passed


def test_critic_enforces_deterministic_failure_over_model_pass() -> None:
    plan = _plan_contract()
    plan.task_slices[-1].allowed_paths = ["store/src/main/"]
    deterministic = deterministic_check_results(plan, [])
    model_results = [
        result.model_copy(update={"passed": True, "findings": []})
        for result in deterministic
    ]

    enforced = enforce_deterministic_failures(
        model_results=model_results,
        deterministic_results=deterministic,
    )

    assert compute_verdict(enforced, []) == "revise"
    qa_verify = next(
        result for result in enforced if result.check_id == "check_qa_verify_present"
    )
    assert not qa_verify.passed
    assert qa_verify.findings[0].code == "qa_verify_paths_not_restricted"


def test_critic_rejects_feature_qa_scrutiny_full_regression_gate() -> None:
    plan = _plan_contract()
    scrutiny = next(
        item for item in plan.task_slices if item.task_type == "engineering.qa.verify.scrutiny"
    )
    scrutiny.verification_commands = [["./qa/smoke.sh"], ["./qa/regression.sh"]]

    results = deterministic_check_results(plan, [])

    qa_verify = next(
        result for result in results if result.check_id == "check_qa_verify_present"
    )
    assert not qa_verify.passed
    assert any(
        finding.code == "qa_verify_uses_periodic_regression_gate"
        for finding in qa_verify.findings
    )


def test_critic_rejects_feature_qa_scrutiny_bare_project_check() -> None:
    plan = _plan_contract()
    scrutiny = next(
        item for item in plan.task_slices if item.task_type == "engineering.qa.verify.scrutiny"
    )
    scrutiny.verification_commands = [
        ["./gradlew", "check"],
        ["./qa/smoke.sh"],
        ["./gradlew", ":core:test", "--tests", "*Range*"],
    ]

    results = deterministic_check_results(plan, [])

    qa_verify = next(
        result for result in results if result.check_id == "check_qa_verify_present"
    )
    assert any(
        finding.code == "qa_verify_uses_broad_project_check"
        for finding in qa_verify.findings
    )


def test_critic_rejects_feature_qa_scrutiny_bare_project_test() -> None:
    plan = _plan_contract()
    scrutiny = next(
        item for item in plan.task_slices if item.task_type == "engineering.qa.verify.scrutiny"
    )
    scrutiny.verification_commands = [
        ["./gradlew", "--no-daemon", "--console=plain", "test"],
        ["./qa/smoke.sh"],
        ["./gradlew", ":core:test", "--tests", "*Range*"],
    ]

    results = deterministic_check_results(plan, [])

    qa_verify = next(
        result for result in results if result.check_id == "check_qa_verify_present"
    )
    assert any(
        finding.code == "qa_verify_uses_broad_project_check"
        for finding in qa_verify.findings
    )


def test_critic_accepts_feature_qa_scrutiny_smoke_and_feature_tests() -> None:
    plan = _plan_contract()
    scrutiny = next(
        item for item in plan.task_slices if item.task_type == "engineering.qa.verify.scrutiny"
    )
    scrutiny.verification_commands = [
        ["./gradlew", ":store:test", "--tests", "*Range*"],
        ["./qa/smoke.sh"],
    ]

    results = deterministic_check_results(plan, [])

    qa_verify = next(
        result for result in results if result.check_id == "check_qa_verify_present"
    )
    assert "qa_verify_missing_feature_validation" not in {
        finding.code for finding in qa_verify.findings
    }
    assert "qa_verify_uses_periodic_regression_gate" not in {
        finding.code for finding in qa_verify.findings
    }


def test_critic_matches_assertion_ids_with_descriptions() -> None:
    plan = _plan_contract(
        acceptance=[
            (
                "assertion-lifecycle-stale-invalid: "
                "stale or invalid snapshot precondition fails safely"
            ),
            "assertion-lifecycle-failure-path: partial journal failure hides unacknowledged writes",
        ]
    )
    plan.acceptance_assertions = [
        (
            "assertion-lifecycle-stale-invalid: "
            "stale or invalid snapshot precondition fails safely"
        ),
        "assertion-lifecycle-failure-path: partial journal failure hides unacknowledged writes",
    ]
    plan.milestones[0].acceptance_assertions = list(plan.acceptance_assertions)
    for task_slice in plan.task_slices:
        task_slice.acceptance_assertion_ids = [
            "assertion-lifecycle-stale-invalid",
            "assertion-lifecycle-failure-path",
        ]

    results = deterministic_check_results(plan, [])

    assert not any(
        finding.code == "acceptance_assertion_unclaimed"
        for result in results
        for finding in result.findings
    )


def test_council_run_succeeds_when_first_iteration_clean() -> None:
    plan = _plan_contract()
    provider = FakeProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 3,
            "planner-consolidator": [plan.model_dump(mode="json")],
            "planner-critic": [_accept_verdict()],
        }
    )
    outcome = PlannerCouncil(config=CouncilConfig(), provider=provider).run(
        feature_goal=_feature_goal(),
        project_context=_context(),
    )
    assert outcome.accepted_at_iteration == 1
    assert outcome.final.council_reports


def test_council_uses_profile_specific_commands() -> None:
    plan = _plan_contract()
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 2,
            "planner-consolidator": [plan.model_dump(mode="json")],
            "planner-critic": [_accept_verdict()],
        }
    )
    PlannerCouncil(
        config=CouncilConfig(
            panelist_count=2,
            production_grade_preempts_critic=False,
            command=["default"],
            profile_commands={
                "planner-panelist": ["panelist-cmd"],
                "planner-consolidator": ["consolidator-cmd"],
                "planner-critic": ["critic-cmd"],
            },
        ),
        provider=provider,
    ).run(feature_goal=_feature_goal(), project_context=_context())

    assert provider.commands == [
        ("planner-panelist", ["panelist-cmd"]),
        ("planner-panelist", ["panelist-cmd"]),
        ("planner-consolidator", ["consolidator-cmd"]),
        ("planner-critic", ["critic-cmd"]),
    ]


def test_council_uses_single_panelist_on_revise_iteration() -> None:
    plan = _plan_contract()
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 4,
            "planner-consolidator": [plan.model_dump(mode="json")] * 2,
            "planner-critic": [_revise_verdict(), _accept_verdict()],
        }
    )

    outcome = PlannerCouncil(
        config=CouncilConfig(
            panelist_count=3,
            iter_1_panelist_count=3,
            iter_2_panelist_count=1,
            production_grade_preempts_critic=False,
        ),
        provider=provider,
    ).run(feature_goal=_feature_goal(), project_context=_context())

    assert outcome.accepted_at_iteration == 2
    panelist_prompts = [
        prompt for profile_name, prompt in provider.prompts if profile_name == "planner-panelist"
    ]
    assert len(panelist_prompts) == 4


def test_iter_2_consolidator_receives_prior_baseline() -> None:
    plan = _plan_contract()
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 4,
            "planner-consolidator": [plan.model_dump(mode="json")] * 2,
            "planner-critic": [_revise_verdict(), _accept_verdict()],
        }
    )

    PlannerCouncil(
        config=CouncilConfig(
            panelist_count=3,
            iter_2_panelist_count=1,
            production_grade_preempts_critic=False,
        ),
        provider=provider,
    ).run(feature_goal=_feature_goal(), project_context=_context())

    consolidator_prompts = [
        prompt
        for profile_name, prompt in provider.prompts
        if profile_name == "planner-consolidator"
    ]
    assert len(consolidator_prompts) == 2
    assert "PRIOR_CONSOLIDATED_BASELINE" in consolidator_prompts[1]
    assert '"feature_id": "workflow_r002"' in consolidator_prompts[1]


def test_production_grade_preempts_model_critic_when_clean() -> None:
    plan = _plan_contract()
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 2,
            "planner-consolidator": [plan.model_dump(mode="json")],
            "planner-critic": [],
        }
    )

    outcome = PlannerCouncil(
        config=CouncilConfig(panelist_count=2, production_grade_critic_sample_rate=0),
        provider=provider,
    ).run(feature_goal=_feature_goal(), project_context=_context())

    assert outcome.accepted_at_iteration == 1
    assert all(profile_name != "planner-critic" for profile_name, _ in provider.prompts)
    assert "preempted_model_critic=true" in outcome.iterations[0].critic.rationale
    assert outcome.iterations[0].substance is not None
    assert outcome.final.council_reports[0]["planner_substance"]["score"] <= 100


def test_production_grade_blockers_force_council_revision() -> None:
    plan = _plan_contract()
    impl_index = next(
        index
        for index, task_slice in enumerate(plan.task_slices)
        if task_slice.role == "implementer"
    )
    plan.task_slices[impl_index] = plan.task_slices[impl_index].model_copy(
        update={
            "slice_id": "impl-range-api-single",
            "objective": "Implement SINGLE range scans.",
            "allowed_paths": ["store/src/main/java/"],
            "forbidden_paths": ["conformance-tests/src/test/java/"],
            "expected_outputs": ["SINGLE direct/mmap range scan implementation"],
            "verification_commands": [["./gradlew", ":core:test"]],
        }
    )
    plan.task_slices.insert(
        impl_index + 1,
        plan.task_slices[impl_index].model_copy(
            update={
                "slice_id": "impl-range-double-mmap",
                "objective": "Implement DOUBLE-store and remaining direct/mmap behavior.",
                "expected_outputs": [
                    "DOUBLE direct/mmap range scan implementation",
                    "Updated store documentation for SINGLE and DOUBLE range scans",
                ],
                "verification_commands": [
                    [
                        "./gradlew",
                        ":conformance-tests:test",
                        "--tests",
                        "com.example.RangeScanConformanceTest",
                    ]
                ],
            }
        ),
    )
    for task_slice in plan.task_slices:
        if task_slice.slice_id == "review":
            task_slice.depends_on = ["impl-range-api-single", "impl-range-double-mmap"]
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 2,
            "planner-consolidator": [plan.model_dump(mode="json")],
            "planner-critic": [_accept_verdict()],
        }
    )

    with pytest.raises(PlannerCouncilExhausted) as exc_info:
        PlannerCouncil(
            config=CouncilConfig(panelist_count=2, max_iterations=1),
            provider=provider,
        ).run(feature_goal=_feature_goal(), project_context=_context())

    iteration = exc_info.value.iterations[0]
    assert iteration.critic.verdict == "revise"
    assert any(
        error.get("code") == "variant_slice_uses_broad_conformance_gate"
        and error.get("source") == "planner.production_grade"
        for error in iteration.validator_errors
    )


def test_role_model_routing_updates_claude_model() -> None:
    assert _route_model_command(
        ["claude", "-p", "--model", "sonnet"],
        claude_model="haiku",
        codex_model="gpt-5.5",
        codex_reasoning="medium",
    ) == ["claude", "-p", "--model", "haiku"]


def test_role_model_routing_updates_codex_reasoning() -> None:
    assert _route_model_command(
        ["codex", "exec", "-m", "gpt-5.5", "-c", 'model_reasoning_effort="high"'],
        claude_model="haiku",
        codex_model="gpt-5.5",
        codex_reasoning="medium",
    ) == ["codex", "exec", "-m", "gpt-5.5", "-c", 'model_reasoning_effort="medium"']


def test_adaptive_panelist_count_keeps_small_features_multi_agent() -> None:
    feature_goal = FeatureGoalContract(
        project="demo",
        goal="Add config diagnostics parity for the visualizer.",
    )

    assert _adaptive_panelist_count(feature_goal, 3) == 2


def test_adaptive_panelist_count_escalates_high_risk_features() -> None:
    feature_goal = FeatureGoalContract(
        project="demo",
        goal="Implement snapshot restore with persistence and concurrency invariants.",
    )

    assert _adaptive_panelist_count(feature_goal, 2) == 3


def test_deterministic_skeleton_classifies_small_and_high_risk_features() -> None:
    small = build_deterministic_plan_skeleton(
        FeatureGoalContract(project="demo", goal="Add config diagnostics for one screen."),
        relevant_paths=["src/config.py", "tests/test_config.py"],
        qa_write_paths=["app-api/src/test/", "ui/tests/", "qa/fixtures/"],
    )
    high_risk = build_deterministic_plan_skeleton(
        FeatureGoalContract(project="demo", goal="Add snapshot restore with concurrency."),
        relevant_paths=["store/", "journal/"],
    )

    assert small.task_class == "small"
    assert small.slices[1].allowed_path_hint == "app-api/src/test/, ui/tests/, qa/fixtures/"
    assert [item.slice_id for item in small.slices] == [
        "design",
        "qa-author",
        "impl-primary",
        "review",
        "qa-scrutiny",
        "qa-usertest",
    ]
    assert high_risk.task_class == "high_risk"
    assert [item.slice_id for item in high_risk.slices if item.role == "implementer"] == [
        "impl-core",
        "impl-invariants",
    ]


def test_deterministic_skeleton_splits_lvc_range_hot_path_feature() -> None:
    skeleton = build_deterministic_plan_skeleton(
        FeatureGoalContract(
            project="lvc-standard",
            goal=(
                "Implement R-003 range-query API on stores: zero-allocation "
                "StoreVisitor range scans for SINGLE and DOUBLE stores."
            ),
            requirements=[
                "Add a zero-allocation StoreVisitor interface.",
                (
                    "Implement inclusive ascending and descending range scans for "
                    "SINGLE and DOUBLE stores."
                ),
            ],
        ),
        relevant_paths=[
            "core/src/main/java/com/joshorig/ull/lvc/api/",
            "store/src/main/java/com/joshorig/ull/lvc/store/direct/SingleDirectStore.java",
            "store/src/main/java/com/joshorig/ull/lvc/store/direct/DoubleDirectStore.java",
        ],
    )

    assert skeleton.task_class == "high_risk"
    assert [item.slice_id for item in skeleton.slices if item.role == "implementer"] == [
        "impl-range-api-single",
        "impl-range-double-mmap",
    ]
    assert "zero-allocation hot-path" in skeleton.slices[2].purpose


def test_project_metadata_qa_write_paths_use_qa_roots(tmp_path: Path) -> None:
    paths = _project_metadata_qa_write_paths(
        {
            "qa": {
                "test_roots": ["core/src/test/java", "conformance-tests/src/test/java"],
                "benchmark_roots": ["benchmarks/src/jmh/java"],
                "browser_test_roots": ["ui/tests/e2e"],
                "test_support_paths": ["app-api/build.gradle"],
            }
        },
        tmp_path,
    )

    assert "core/src/test/java/" in paths
    assert "conformance-tests/src/test/java/" in paths
    assert "benchmarks/src/jmh/java/" in paths
    assert "ui/tests/e2e/" in paths
    assert "app-api/build.gradle/" in paths


def test_project_context_merge_preserves_metadata_qa_roots(tmp_path: Path) -> None:
    context = ProjectContext(
        project_root=tmp_path,
        relevant_paths=["store/"],
        qa_write_paths=["tests/", "qa/fixtures/"],
    )

    merged = _merge_project_context_metadata(
        context,
        {
            "relevant_paths": ["core/", "benchmarks/"],
            "qa": {
                "test_roots": ["core/src/test/java"],
                "benchmark_roots": ["benchmarks/src/jmh/java"],
            },
        },
    )

    assert "core/" in merged.relevant_paths
    assert "benchmarks/" in merged.relevant_paths
    assert "core/src/test/java/" in merged.qa_write_paths
    assert "benchmarks/src/jmh/java/" in merged.qa_write_paths
    assert merged.qa_policy_summary["test_roots"] == ["core/src/test/java"]
    assert merged.qa_policy_summary["benchmark_roots"] == ["benchmarks/src/jmh/java"]


def test_council_sends_lens_specific_context_and_skeleton_to_panelists() -> None:
    plan = _plan_contract()
    provider = RecordingProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 2,
            "planner-consolidator": [plan.model_dump(mode="json")],
            "planner-critic": [_accept_verdict()],
        }
    )

    PlannerCouncil(
        config=CouncilConfig(panelist_count=2),
        provider=provider,
    ).run(
        feature_goal=_feature_goal(),
        project_context=ProjectContext(
            project_root=Path("/tmp/lvc-standard"),
            roadmap_excerpt=(
                "API implementation store class\n"
                "QA smoke regression fixture\n"
                "risk concurrency persistence"
            ),
            decisions_excerpt="decision: persistence must preserve journal invariant",
            relevant_paths=["store/", "tests/", "qa/fixtures/"],
            qa_policy_summary={
                "endpoint_acceptance": {"require_http_harness": True},
                "benchmark_variants": ["single", "double"],
            },
        ),
    )

    panelist_prompts = [
        prompt for profile_name, prompt in provider.prompts if profile_name == "planner-panelist"
    ]
    assert len(panelist_prompts) == 2
    assert any('"context_lens": "architecture"' in prompt for prompt in panelist_prompts)
    assert any('"context_lens": "qa"' in prompt for prompt in panelist_prompts)
    assert "DETERMINISTIC_PLAN_SKELETON" in panelist_prompts[0]
    assert '"slice_id": "qa-author"' in panelist_prompts[0]
    assert '"qa_policy_summary"' in panelist_prompts[0]
    assert '"require_http_harness": true' in panelist_prompts[0]


def test_repair_brief_turns_failed_checks_into_actionable_repairs() -> None:
    prior = SimpleNamespace(
        validator_errors=[
            {
                "code": "qa_paths_not_restricted",
                "message": "QA allowed_paths must be restricted.",
            }
        ],
        critic=SimpleNamespace(
            model_dump=lambda mode: {
                "verdict": "revise",
                "findings": [],
                "per_check_results": [
                    {
                        "check_id": "check_qa_verify_present",
                        "passed": False,
                        "findings": [
                            {
                                "severity": "blocking",
                                "check_id": "check_qa_verify_present",
                                "code": "qa_verify_not_after_reviewer",
                                "message": "qa.verify must run after reviewers.",
                            }
                        ],
                    }
                ],
            }
        ),
    )

    brief = build_repair_brief(prior)

    assert brief["must_fix_codes"] == [
        "qa_paths_not_restricted",
        "qa_verify_not_after_reviewer",
    ]
    assert any("Restrict every engineering.qa.author" in item for item in brief["required_repairs"])
    assert any(
        "Move engineering.qa.verify.scrutiny after all reviewer" in item
        for item in brief["required_repairs"]
    )


def test_repair_brief_handles_unachievable_milestone_signoff() -> None:
    prior = SimpleNamespace(
        validator_errors=[],
        critic=SimpleNamespace(
            model_dump=lambda mode: {
                "verdict": "revise",
                "findings": [
                    {
                        "severity": "blocking",
                        "check_id": "check_milestones_present",
                        "code": "milestone_validator_signoff_unachievable",
                        "message": "Milestone gate is impossible.",
                    }
                ],
                "per_check_results": [],
            }
        ),
    )

    brief = build_repair_brief(prior)

    assert brief["must_fix_codes"] == ["milestone_validator_signoff_unachievable"]
    assert any("one terminal validation milestone" in item for item in brief["required_repairs"])


def test_council_repairs_unachievable_milestones_before_critic() -> None:
    plan = _plan_contract().model_copy(
        update={
            "milestones": [
                MilestoneContract(
                    milestone_id="m1",
                    name="Design and QA",
                    slice_ids=["design", "qa-author"],
                    acceptance_assertions=["acceptance"],
                    validation_contract={"scrutiny": True, "usertest": True},
                    signoff_policy="scrutiny_and_usertest",
                ),
                MilestoneContract(
                    milestone_id="m2",
                    name="Validation",
                    slice_ids=["qa-scrutiny", "qa-usertest"],
                    acceptance_assertions=["acceptance"],
                    validation_contract={"scrutiny": True, "usertest": True},
                    depends_on=["m1"],
                    signoff_policy="scrutiny_and_usertest",
                ),
            ]
        }
    )

    repaired = _repair_unachievable_milestones(plan)

    assert [milestone.milestone_id for milestone in repaired.milestones] == ["m1"]
    assert repaired.milestones[0].slice_ids == [
        task_slice.slice_id for task_slice in plan.task_slices
    ]
    assert repaired.milestones[0].signoff_policy == "scrutiny_and_usertest"


def test_council_run_raises_planner_council_exhausted_when_max_iterations_exceeded() -> None:
    plan = _plan_contract(acceptance=["restore round trip only"])
    provider = FakeProvider(
        {
            "planner-panelist": [plan.model_dump(mode="json")] * 6,
            "planner-consolidator": [plan.model_dump(mode="json")] * 2,
            "planner-critic": [_revise_verdict()] * 2,
        }
    )
    with pytest.raises(PlannerCouncilExhausted) as raised:
        PlannerCouncil(config=CouncilConfig(max_iterations=2), provider=provider).run(
            feature_goal=_feature_goal(),
            project_context=_context(),
        )
    assert len(raised.value.iterations) == 2


def test_council_exhausted_preserves_invalid_panelist_diagnostics() -> None:
    provider = FakeProvider(
        {
            "planner-panelist": ["You've hit your limit · resets 6:50pm"],
        }
    )

    with pytest.raises(PlannerCouncilExhausted) as raised:
        PlannerCouncil(
            config=CouncilConfig(max_iterations=1, panelist_count=2, iter_1_panelist_count=1),
            provider=provider,
        ).run(
            feature_goal=_feature_goal(),
            project_context=_context(),
        )

    assert raised.value.iterations == []
    assert len(raised.value.invalid_proposals) == 1
    invalid = raised.value.invalid_proposals[0]
    assert invalid.raw_response == "You've hit your limit · resets 6:50pm"
    assert invalid.parse_error


def _feature_goal() -> FeatureGoalContract:
    return FeatureGoalContract(
        project="lvc-standard",
        goal="Implement snapshot and restore with persistence invariants.",
    )


def _context() -> ProjectContext:
    return ProjectContext(project_root=Path("/tmp/lvc-standard"), relevant_paths=["store/"])


def _accept_verdict() -> dict[str, object]:
    return {
        "rationale": "accepted",
        "per_check_results": [
            {
                "check_id": check.check_id,
                "name": check.name,
                "passed": True,
                "severity_if_failed": check.severity_if_failed,
                "findings": [],
            }
            for check in RUBRIC_CHECKS
        ],
    }


def _revise_verdict() -> dict[str, object]:
    payload = _accept_verdict()
    results = list(payload["per_check_results"])
    assert isinstance(results[0], dict)
    results[0] = {
        **results[0],
        "passed": False,
        "findings": [
            {
                "severity": "blocking",
                "check_id": results[0]["check_id"],
                "code": "unit_test_forced_revision",
                "message": "force revise",
            }
        ],
    }
    payload["per_check_results"] = results
    payload["rationale"] = "revise"
    return payload


def _plan_contract(
    acceptance: list[str] | None = None,
    *,
    feature_id: str = "workflow_r002",
) -> PlanContract:
    return PlanContract(
        feature_id=feature_id,
        project="lvc-standard",
        problem_statement="Snapshot/restore API for store persistence.",
        design_contract=DesignContract(
            public_api="Store.snapshot(Path), Store.restore(Path)",
            ownership_boundaries="store/ owns snapshot format",
            concurrency_protocol="restore swaps after journal cursor reconciliation",
            persistence_protocol="magic/version header and CRC per page",
            acceptance_tests=["round trip", "crc invariant", "partial journal failure"],
        ),
        affected_surfaces=["store/", "qa/"],
        implementation_topology=ImplementationTopology.COUNCIL_DECIDES,
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Design Store.snapshot(Path) and Store.restore(Path) format in store/",
                allowed_paths=["store/", "docs/"],
                forbidden_paths=["benchmarks/"],
                expected_outputs=["DesignContract"],
                verification_commands=[["./qa/smoke.sh"]],
            ),
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write failing snapshot restore tests for lifecycle acceptance.",
                allowed_paths=["tests/", "qa/fixtures/"],
                forbidden_paths=["store/", "core/", "benchmarks/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[["./qa/smoke.sh"]],
            ),
            TaskSliceContract(
                slice_id="impl-store",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement SINGLE and DOUBLE restore cursor reconciliation in store/",
                allowed_paths=["store/", "core/"],
                forbidden_paths=["tests/", "qa/fixtures/", "benchmarks/"],
                depends_on=["qa-author"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["./qa/smoke.sh"]],
            ),
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review store snapshot invariants and CRC failure behavior",
                allowed_paths=["store/", "docs/"],
                forbidden_paths=["tests/", "qa/fixtures/", "benchmarks/"],
                depends_on=["impl-store"],
                expected_outputs=["ReviewVerdictContract"],
                verification_commands=[["./qa/smoke.sh"]],
            ),
            TaskSliceContract(
                slice_id="qa-scrutiny",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective=(
                    "Run feature-specific tests, qa/smoke.sh, and sign off snapshot coverage."
                ),
                allowed_paths=["tests/", "qa/fixtures/"],
                forbidden_paths=["store/", "core/", "benchmarks/", "docs/"],
                depends_on=["review"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["./gradlew", ":store:test"], ["./qa/smoke.sh"]],
            ),
            TaskSliceContract(
                slice_id="qa-usertest",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Run the project user-test harness or record metadata-authorized skip.",
                allowed_paths=["tests/", "qa/fixtures/"],
                forbidden_paths=["store/", "core/", "benchmarks/", "docs/"],
                depends_on=["qa-scrutiny"],
                expected_outputs=["QAResultContract"],
                verification_commands=[
                    [
                        "./gradlew",
                        ":store:test",
                        "--tests",
                        "com.example.SnapshotUserFlowTest",
                    ]
                ],
            ),
        ],
        acceptance_test_matrix=acceptance
        or [
            "stale or invalid snapshot precondition fails safely",
            "CRC invariant failure aborts restore",
            "partial journal failure hides unacknowledged writes",
        ],
        risk_register=["restore cursor mismatch could expose partial writes"],
    )


def test_critic_rejects_qa_usertest_broad_smoke_gate() -> None:
    plan = _plan_contract()
    usertest = next(
        item for item in plan.task_slices if item.task_type == "engineering.qa.verify.usertest"
    )
    usertest.verification_commands = [["./qa/smoke.sh"]]

    results = deterministic_check_results(plan, [])

    qa_verify = next(
        result for result in results if result.check_id == "check_qa_verify_present"
    )
    assert any(
        finding.code == "qa_usertest_uses_broad_gate" for finding in qa_verify.findings
    )


def test_deterministic_critic_rejects_benchmark_output_without_benchmark_root() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = "Write failing JMH benchmark coverage for range restore allocation."
    qa_author.expected_outputs = [
        "benchmarks/src/jmh/java/com/example/RestoreRangeBenchmark.java",
    ]
    qa_author.allowed_paths = ["tests/", "qa/fixtures/"]

    results = deterministic_check_results(plan, validator_errors=[])

    assert any(
        finding.code == "qa_benchmark_output_path_not_allowed"
        for result in results
        for finding in result.findings
    )


def test_deterministic_critic_accepts_metadata_test_support_paths() -> None:
    plan = _plan_contract()
    qa_author = plan.task_slices[1]
    qa_author.objective = "Write failing JMH benchmark and wire the smoke gate."
    qa_author.expected_outputs = [
        "benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java",
        "benchmarks/build.gradle",
    ]
    qa_author.allowed_paths = ["benchmarks/src/jmh/java/", "benchmarks/build.gradle/"]
    qa_author.forbidden_paths = ["core/src/main/"]

    for task_slice in plan.task_slices:
        if task_slice.task_type in {
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }:
            task_slice.allowed_paths = [
                "benchmarks/src/jmh/java/",
                "benchmarks/build.gradle/",
            ]
            task_slice.forbidden_paths = ["core/src/main/"]

    results = deterministic_check_results(
        plan,
        validator_errors=[],
        qa_write_paths=["benchmarks/src/jmh/java", "benchmarks/build.gradle"],
    )

    blocked_codes = {
        finding.code
        for result in results
        if result.check_id
        in {"check_qa_author_present", "check_qa_verify_present", "check_qa_paths_disjoint"}
        for finding in result.findings
    }
    assert "qa_author_paths_not_restricted" not in blocked_codes
    assert "qa_verify_paths_not_restricted" not in blocked_codes
    assert "qa_benchmark_output_path_not_allowed" not in blocked_codes
