from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from pgloom.db.postgres import connect
from pgloom.tasks import enqueue_task
from pgloom.workflows import create_workflow

from pgloom_engineering.contract_store import (
    list_handoffs,
    list_plan_contracts,
    list_recovery_actions,
    list_task_contracts,
)
from pgloom_engineering.contracts import FeatureGoalContract
from pgloom_engineering.features import attach_task, create_feature
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner import CouncilConfig, PlannerCouncil, ProjectContext
from pgloom_engineering.planner.council import CouncilOutcome
from pgloom_engineering.planner.critic import RUBRIC_CHECKS
from pgloom_engineering.planner.token_savior_context import TokenSaviorContextResult
from pgloom_engineering.projects import ProjectConfig, register_project
from pgloom_engineering.roles.planner import PlannerHandler
from tests.unit.test_planner_council import _plan_contract


class FakeCouncil:
    def __init__(self, outcome: CouncilOutcome | None = None, exc: Exception | None = None) -> None:
        self.outcome = outcome
        self.exc = exc

    def run(self, **kwargs: Any) -> CouncilOutcome:
        del kwargs
        if self.exc:
            raise self.exc
        assert self.outcome is not None
        return self.outcome


def test_planner_role_gate_defers_implementer_slices(database_url: str, tmp_path: Path) -> None:
    workflow, planner = _setup_planner_task(database_url, tmp_path, implementer_gate="disabled")
    plan = _plan_contract(feature_id=workflow["id"])
    outcome = CouncilOutcome(final=plan, iterations=[], accepted_at_iteration=1)

    result = PlannerHandler(council=cast(PlannerCouncil, FakeCouncil(outcome))).handle(planner)

    assert result.status == "done"
    assert result.result["deferred_slices"] == [
        {
            "slice_id": "impl-store",
            "role": "implementer",
            "reason": "role gated to disabled in engineering_projects.metadata.role_gates",
            "role_gate": {
                "project": "lvc-standard",
                "role": "implementer",
                "status": "disabled",
                "source": "engineering_projects.metadata.role_gates",
                "reason": "role gated to disabled in engineering_projects.metadata.role_gates",
            },
        }
    ]
    plans = list_plan_contracts(workflow["id"], database_url=database_url)
    assert plans[0]["status"] == "valid"
    contracts = list_task_contracts(workflow["id"], database_url=database_url)
    assert {row["role"] for row in contracts} == {"designer", "reviewer", "qa"}
    assert {_task_type(row) for row in contracts if row["role"] == "qa"} == {
        "engineering.qa.author",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }
    handoffs = list_handoffs(workflow["id"], database_url=database_url)
    assert len([row for row in handoffs if row["handoff_type"] == "plan_to_task"]) == 5
    recoveries = list_recovery_actions(workflow["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.role_gate_disabled"
    assert recoveries[0]["status"] == "deferred"
    outcome = json.loads(recoveries[0]["outcome"])
    assert outcome["role_gate"] == {
        "project": "lvc-standard",
        "role": "implementer",
        "status": "disabled",
        "source": "engineering_projects.metadata.role_gates",
        "reason": "role gated to disabled in engineering_projects.metadata.role_gates",
    }


def test_planner_enqueues_implementer_when_role_gate_enabled(
    database_url: str,
    tmp_path: Path,
) -> None:
    workflow, planner = _setup_planner_task(database_url, tmp_path, implementer_gate="enabled")
    plan = _plan_contract(feature_id=workflow["id"])
    outcome = CouncilOutcome(final=plan, iterations=[], accepted_at_iteration=1)

    result = PlannerHandler(council=cast(PlannerCouncil, FakeCouncil(outcome))).handle(planner)

    assert result.status == "done"
    assert result.result["deferred_slices"] == []
    contracts = list_task_contracts(workflow["id"], database_url=database_url)
    assert {row["role"] for row in contracts} == {"designer", "implementer", "reviewer", "qa"}
    assert {
        row["input_contract"]["role_gate"]["status"] for row in contracts
    } == {"enabled"}
    assert {
        row["input_contract"]["role_gate"]["source"] for row in contracts
    } == {"engineering_projects.metadata.role_gates"}
    assert {_task_type(row) for row in contracts if row["role"] == "qa"} == {
        "engineering.qa.author",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }
    recoveries = list_recovery_actions(workflow["id"], database_url=database_url)
    assert not [
        row for row in recoveries if row["blocker_code"] == "engineering.role_gate_disabled"
    ]


def test_planner_dispatch_preserves_implementer_method_scoped_commands(
    database_url: str,
    tmp_path: Path,
) -> None:
    workflow, planner = _setup_planner_task(
        database_url,
        tmp_path,
        implementer_gate="enabled",
        project_metadata={
            "qa": {
                "feature_smoke_commands": [
                    {
                        "match_terms": ["snapshot"],
                        "replaces": [":store:test"],
                        "commands": [
                            [
                                "./gradlew",
                                ":store:test",
                                "--tests",
                                "com.example.SnapshotRestoreTest",
                            ]
                        ],
                    }
                ]
            }
        },
    )
    plan = _plan_contract(feature_id=workflow["id"])
    implementer = next(
        item for item in plan.task_slices if item.task_type == "engineering.implement"
    )
    method_command = [
        "./gradlew",
        ":store:test",
        "--tests",
        "com.example.SnapshotRestoreTest.restoreRoundTrip",
    ]
    implementer.verification_commands = [method_command]
    outcome = CouncilOutcome(final=plan, iterations=[], accepted_at_iteration=1)

    result = PlannerHandler(council=cast(PlannerCouncil, FakeCouncil(outcome))).handle(planner)

    assert result.status == "done"
    contracts = list_task_contracts(workflow["id"], database_url=database_url)
    impl_contract = next(
        row for row in contracts if row["input_contract"]["task_type"] == "engineering.implement"
    )
    assert impl_contract["input_contract"]["verification_commands"] == [method_command]


def test_planner_persistence_uses_metadata_qa_write_paths(
    database_url: str,
    tmp_path: Path,
) -> None:
    workflow, planner = _setup_planner_task(
        database_url,
        tmp_path,
        implementer_gate="enabled",
        project_metadata={"qa": {"test_support_paths": ["benchmarks/build.gradle"]}},
    )
    plan = _plan_contract(feature_id=workflow["id"])
    for task_slice in plan.task_slices:
        if task_slice.task_type in {
            "engineering.qa.author",
            "engineering.qa.verify.scrutiny",
            "engineering.qa.verify.usertest",
        }:
            task_slice.allowed_paths = ["tests/", "benchmarks/build.gradle/"]
            task_slice.forbidden_paths = ["core/", "store/", "docs/"]
    outcome = CouncilOutcome(final=plan, iterations=[], accepted_at_iteration=1)

    result = PlannerHandler(council=cast(PlannerCouncil, FakeCouncil(outcome))).handle(planner)

    assert result.status == "done"
    plans = list_plan_contracts(workflow["id"], database_url=database_url)
    assert plans[0]["status"] == "valid"
    assert plans[0]["validation_errors"] == []


def test_live_planner_records_model_usage_and_token_savior_rows(
    database_url: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workflow, planner = _setup_planner_task(database_url, tmp_path, implementer_gate="enabled")
    plan = _plan_contract(feature_id=workflow["id"])
    script = tmp_path / "fake_planner_cli.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                f"plan = {plan.model_dump_json()!r}",
                f"checks = {json.dumps(_accept_checks())!r}",
                "prompt = sys.stdin.read()",
                "if 'VALIDATOR_ERRORS:' in prompt:",
                "    print(json.dumps({'rationale': 'accepted', "
                "'per_check_results': json.loads(checks)}))",
                "else:",
                "    print(plan)",
            ]
        ),
        encoding="utf-8",
    )

    def fake_token_savior(**kwargs: Any) -> TokenSaviorContextResult:
        del kwargs
        return TokenSaviorContextResult(
            context=ProjectContext(
                project_root=tmp_path,
                roadmap_excerpt="packed roadmap context",
                decisions_excerpt="packed decisions context",
                relevant_paths=["store/", "qa/"],
            ),
            input_tokens_original=1000,
            input_tokens_after_savior=400,
            tokens_saved=600,
            reduction_ratio=0.6,
            method="test_token_savior",
            packed_context="packed roadmap context",
        )

    monkeypatch.setattr(
        "pgloom_engineering.roles.planner.build_token_savior_project_context",
        fake_token_savior,
    )
    council = PlannerCouncil(
        config=CouncilConfig(
            panelist_count=2,
            max_iterations=1,
            command=[sys.executable, str(script)],
        ),
        provider=EngineeringCLIModelProvider(database_url=database_url),
    )

    result = PlannerHandler(council=council).handle(planner)

    assert result.status == "done"
    with connect(database_url) as conn:
        usage_rows = conn.execute(
            """
            select id, profile_name, input_tokens, output_tokens, metadata
            from model_usage
            where workflow_id = %s
            order by id
            """,
            (workflow["id"],),
        ).fetchall()
        token_rows = conn.execute(
            """
            select model_usage_id, profile_name, input_tokens_original,
                   input_tokens_after_savior, tokens_saved, metadata
            from engineering_token_savior_usage
            where feature_id = %s
            order by id
            """,
            (workflow["id"],),
        ).fetchall()
        generic_token_rows = conn.execute(
            """
            select model_usage_id, profile_name, input_tokens_original,
                   input_tokens_after, tokens_saved, metadata
            from token_savings
            where scope_id = %s
            order by id
            """,
            (workflow["id"],),
        ).fetchall()
        capsule_rows = conn.execute(
            """
            select project, method, input_tokens_original, input_tokens_after_savior,
                   tokens_saved, metadata
            from engineering_project_context_capsules
            where project = %s
            """,
            ("lvc-standard",),
        ).fetchall()
        memory_rows = conn.execute(
            """
            select workflow_id, key, value
            from memory_entries
            where workflow_id in (%s, %s)
            order by workflow_id, key
            """,
            (workflow["id"], "project:lvc-standard"),
        ).fetchall()
    assert [row["profile_name"] for row in usage_rows] == [
        "planner-panelist",
        "planner-panelist",
        "planner-consolidator",
        "planner-critic",
    ]
    assert all(int(row["input_tokens"]) > 0 for row in usage_rows)
    assert {row["profile_name"] for row in token_rows} == {
        "planner-panelist",
        "planner-consolidator",
        "planner-critic",
    }
    assert len(token_rows) == 4
    assert len(generic_token_rows) == len(token_rows)
    assert sum(int(row["tokens_saved"]) for row in generic_token_rows) == sum(
        int(row["tokens_saved"]) for row in token_rows
    )
    assert sum(int(row["input_tokens_original"]) for row in generic_token_rows) == sum(
        int(row["input_tokens_original"]) for row in token_rows
    )
    assert sum(int(row["input_tokens_after"]) for row in generic_token_rows) == sum(
        int(row["input_tokens_after_savior"]) for row in token_rows
    )
    assert {row["metadata"]["source_table"] for row in generic_token_rows} == {
        "engineering_token_savior_usage"
    }
    assert {int(row["tokens_saved"]) for row in token_rows} == {600}
    assert all(row["model_usage_id"] is not None for row in token_rows)
    assert {row["metadata"]["role"] for row in token_rows} == {
        "panelist",
        "consolidator",
        "critic",
    }
    assert len(capsule_rows) == 1
    assert capsule_rows[0]["method"] == "test_token_savior"
    assert int(capsule_rows[0]["tokens_saved"]) == 600
    memory_keys = {(row["workflow_id"], row["key"]) for row in memory_rows}
    assert (
        workflow["id"],
        f"feature:{workflow['id']}:accepted_plan_summary",
    ) in memory_keys
    assert (
        "project:lvc-standard",
        "project:lvc-standard:planning_guardrails",
    ) in memory_keys
    assert any(":benchmarks:jmhSmokeCheck" in row["value"] for row in memory_rows)
    assert not any("./qa/smoke.sh" in row["value"] for row in memory_rows)


def _setup_planner_task(
    database_url: str,
    root: Path,
    *,
    implementer_gate: str,
    project_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = {
        "role_gates": {"planner": "enabled", "implementer": implementer_gate},
        **(project_metadata or {}),
    }
    project = register_project(
        ProjectConfig(
            name="lvc-standard",
            root=root,
            metadata=metadata,
        ),
        database_url=database_url,
    )
    workflow = create_workflow(domain="engineering", name="r002", database_url=database_url)
    feature_goal = FeatureGoalContract(
        project="lvc-standard",
        goal="Implement snapshot restore for store persistence.",
    )
    create_feature(
        workflow_id=workflow["id"],
        project="lvc-standard",
        metadata={"feature_goal_contract": feature_goal.model_dump(mode="json")},
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload={
            "database_url": database_url,
            "feature_goal_contract": feature_goal.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
        },
        database_url=database_url,
    )
    attach_task(workflow["id"], planner["id"], role="planner", database_url=database_url)
    with connect(database_url) as conn:
        row = conn.execute("select * from tasks where id = %s", (planner["id"],)).fetchone()
        assert row is not None
        return workflow, dict(row)


def _task_type(row: dict[str, Any]) -> str:
    return str(dict(row["input_contract"])["task_type"])


def _accept_checks() -> list[dict[str, Any]]:
    return [
        {
            "check_id": check.check_id,
            "name": check.name,
            "passed": True,
            "severity_if_failed": check.severity_if_failed,
            "findings": [],
        }
        for check in RUBRIC_CHECKS
    ]
