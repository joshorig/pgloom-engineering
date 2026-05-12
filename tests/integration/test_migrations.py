from __future__ import annotations

import json

from pgloom.approvals import request_approval
from pgloom.artifacts import register_artifact
from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pgloom.tasks import enqueue_task
from pgloom.workflows import create_workflow
from typer.testing import CliRunner

from pgloom_engineering.cli import app
from pgloom_engineering.contract_store import (
    create_plan_contract,
    finish_worker_run,
    list_handoffs,
    list_plan_contracts,
    list_qa_signoffs,
    list_recovery_actions,
    list_task_contracts,
    list_worker_runs,
    record_qa_signoff,
    start_worker_run,
    upsert_task_contract,
)
from pgloom_engineering.contracts import (
    DesignContract,
    PlanContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.db.migrations import check
from pgloom_engineering.features import (
    attach_task,
    create_feature,
    get_feature,
    get_feature_aggregate,
    list_feature_tasks,
    list_features,
    update_feature_state,
)
from pgloom_engineering.roles.planner import PlannerHandler
from pgloom_engineering.token_savior import TokenSaviorUsage, record_token_savior_usage
from pgloom_engineering.worker import run_once as run_worker_once


def test_engineering_migrations_and_feature_round_trip(database_url: str) -> None:
    assert check(database_url)["ok"]
    workflow = create_workflow(domain="engineering", name="demo", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        branch="feature/demo",
        database_url=database_url,
    )
    assert feature["id"] == workflow["id"]
    assert feature["project"] == "pgloom"


def test_feature_lifecycle_aggregate_and_token_savior(database_url: str) -> None:
    workflow = create_workflow(domain="engineering", name="feature-a", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        branch="feature/a",
        metadata={"ticket": "A"},
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="claude",
        database_url=database_url,
    )
    implementer = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.implement",
        slot="codex",
        database_url=database_url,
    )
    attach_task(feature["id"], planner["id"], role="planner", database_url=database_url)
    attach_task(feature["id"], implementer["id"], role="implementer", database_url=database_url)
    request_approval(
        workflow_id=workflow["id"],
        task_id=implementer["id"],
        domain="engineering",
        prompt="Approve implementation",
        database_url=database_url,
    )
    register_artifact(
        workflow_id=workflow["id"],
        task_id=implementer["id"],
        artifact_type="log",
        uri="file:///tmp/impl.log",
        database_url=database_url,
    )
    with connect(database_url) as conn, conn.transaction():
        model_usage = conn.execute(
            """
            insert into model_usage(
              workflow_id, task_id, profile_name, input_tokens, output_tokens, cost_usd,
              metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                workflow["id"],
                implementer["id"],
                "codex",
                4300,
                300,
                0.02,
                jsonb(
                    {
                        "cached_input_tokens": 900,
                        "reasoning_output_tokens": 70,
                        "prompt_estimated_tokens": 800,
                    }
                ),
            ),
        ).fetchone()
    assert model_usage is not None
    record_token_savior_usage(
        TokenSaviorUsage(
            feature_id=feature["id"],
            workflow_id=workflow["id"],
            task_id=implementer["id"],
            model_usage_id=model_usage["id"],
            profile_name="codex",
            input_tokens_original=12000,
            input_tokens_after_savior=4300,
            tokens_saved=7700,
            reduction_ratio=7700 / 12000,
            estimated_cost_saved_usd=0.0231,
        ),
        database_url=database_url,
    )

    updated = update_feature_state(
        feature["id"],
        state="review",
        pr_url="https://github.com/joshorig/pgloom/pull/1",
        metadata_patch={"owner": "devmini"},
        database_url=database_url,
    )
    assert updated is not None
    assert updated["state"] == "review"
    assert updated["metadata"] == {"ticket": "A", "owner": "devmini"}

    persisted = get_feature(feature["id"], database_url=database_url)
    assert persisted is not None
    assert persisted["pr_url"].endswith("/pull/1")
    assert [row["id"] for row in list_features(project="pgloom", database_url=database_url)] == [
        feature["id"]
    ]
    assert list_features(project="other", database_url=database_url) == []
    feature_tasks = list_feature_tasks(feature["id"], database_url=database_url)
    assert [row["role"] for row in feature_tasks] == ["planner", "implementer"]

    aggregate = get_feature_aggregate(feature["id"], database_url=database_url)
    assert aggregate is not None
    assert aggregate["feature"]["id"] == feature["id"]
    assert aggregate["workflow"]["name"] == "feature-a"
    assert len(aggregate["tasks"]) == 2
    assert len(aggregate["approvals"]) == 1
    assert len(aggregate["artifacts"]) == 1
    assert aggregate["model_usage"]["summary"]["input_tokens"] == 4300
    assert aggregate["token_savior"]["summary"]["tokens_saved"] == 7700
    assert aggregate["agent_topology"]["planning"] == "multi_agent"

    worker_run = start_worker_run(
        feature_id=feature["id"],
        task_id=implementer["id"],
        role="implementer",
        phase="implement",
        database_url=database_url,
    )
    finished_run = finish_worker_run(
        int(worker_run["id"]),
        status="done",
        commands_run=[{"cmd": ["pytest", "-q"], "exit_code": 0, "duration_s": 1.2}],
        evidence_ids=["evidence-1"],
        artifact_ids=["artifact-1"],
        database_url=database_url,
    )
    assert finished_run["input_tokens"] == 4300
    assert finished_run["cached_input_tokens"] == 900
    assert finished_run["reasoning_tokens"] == 70
    assert finished_run["token_savior_saved_tokens"] == 7700
    assert finished_run["metadata"]["model_usage"][0]["prompt_estimated_tokens"] == 800
    assert list_worker_runs(feature["id"], database_url=database_url)[0]["status"] == "done"

    aggregate = get_feature_aggregate(feature["id"], database_url=database_url)
    assert aggregate is not None
    assert aggregate["worker_run_summary"]["runs"] == 1
    assert aggregate["worker_run_summary"]["cached_input_tokens"] == 900
    assert aggregate["model_usage"]["summary"]["prompt_estimated_tokens"] == 800
    assert aggregate["worker_runs"][0]["commands_run"][0]["cmd"] == ["pytest", "-q"]

    signoff = record_qa_signoff(
        feature_id=feature["id"],
        task_id=implementer["id"],
        plan_contract_id=None,
        milestone_id="m1",
        validator_type="scrutiny",
        verdict="pass",
        qa_result_contract={"verdict": "pass", "validator_type": "scrutiny"},
        evidence=[{"kind": "test_run", "verdict": "pass"}],
        artifact_ids=["artifact-1"],
        database_url=database_url,
    )
    assert signoff["validator_type"] == "scrutiny"
    assert list_qa_signoffs(
        feature["id"], milestone_id="m1", database_url=database_url
    )[0]["verdict"] == "pass"

    json_result = CliRunner().invoke(
        app,
        ["feature", "show", feature["id"], "--json", "--database-url", database_url],
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["token_savior"]["summary"]["tokens_saved"] == 7700

    list_result = CliRunner().invoke(
        app,
        ["feature", "list", "--project", "pgloom", "--json", "--database-url", database_url],
    )
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)[0]["id"] == feature["id"]


def test_feature_cascades_when_workflow_is_deleted(database_url: str) -> None:
    workflow = create_workflow(
        domain="engineering",
        name="feature-delete",
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="claude",
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="planner", database_url=database_url)
    record_token_savior_usage(
        TokenSaviorUsage(
            feature_id=feature["id"],
            workflow_id=workflow["id"],
            task_id=task["id"],
            input_tokens_original=100,
            input_tokens_after_savior=40,
            tokens_saved=60,
            reduction_ratio=0.6,
        ),
        database_url=database_url,
    )

    with connect(database_url) as conn, conn.transaction():
        conn.execute("delete from workflows where id = %s", (workflow["id"],))

    assert get_feature(feature["id"], database_url=database_url) is None


def test_plan_contract_round_trip_and_planner_dispatch(database_url: str) -> None:
    workflow = create_workflow(domain="engineering", name="contracted", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload={
            "database_url": database_url,
            "plan_contract": _plan_contract(workflow["id"]).model_dump(mode="json"),
        },
        database_url=database_url,
    )
    attach_task(feature["id"], planner["id"], role="planner", database_url=database_url)

    result = PlannerHandler().handle({**planner, "payload": planner["payload"]})

    assert result.status == "done"
    plan_rows = list_plan_contracts(feature["id"], database_url=database_url)
    assert len(plan_rows) == 1
    assert plan_rows[0]["status"] == "valid"
    task_contracts = list_task_contracts(feature["id"], database_url=database_url)
    assert [row["role"] for row in task_contracts] == ["qa", "implementer", "qa", "qa"]
    handoffs = list_handoffs(feature["id"], database_url=database_url)
    assert len(handoffs) == 4

    aggregate = get_feature_aggregate(feature["id"], database_url=database_url)
    assert aggregate is not None
    assert aggregate["active_plan_contract"]["id"] == plan_rows[0]["id"]
    assert len(aggregate["task_contracts"]) == 4
    assert len(aggregate["handoffs"]) == 4


def test_invalid_plan_contract_is_persisted_with_errors(database_url: str) -> None:
    workflow = create_workflow(domain="engineering", name="invalid", database_url=database_url)
    create_feature(workflow_id=workflow["id"], project="pgloom", database_url=database_url)
    plan = _plan_contract(workflow["id"])
    plan.task_slices[0].verification_commands = []

    row = create_plan_contract(plan, database_url=database_url)

    assert row["status"] == "invalid"
    assert row["active"] is False
    assert {error["code"] for error in row["validation_errors"]} == {
        "missing_verification_commands"
    }


def test_feature_create_cli_enqueues_multi_agent_planner(database_url: str) -> None:
    register_result = CliRunner().invoke(
        app,
        [
            "project",
            "register",
            "--name",
            "pgloom",
            "--root",
            ".",
            "--github-repo",
            "joshorig/pgloom",
            "--implementation-topology",
            "parallel_candidates",
            "--smoke-command",
            "pytest -q",
            "--json",
            "--database-url",
            database_url,
        ],
    )
    assert register_result.exit_code == 0
    registered = json.loads(register_result.output)
    assert registered["agent_topology"]["planning"] == "multi_agent"
    assert registered["agent_topology"]["review"] == "multi_agent"
    assert registered["agent_topology"]["implementation"] == "parallel_candidates"

    result = CliRunner().invoke(
        app,
        [
            "feature",
            "create",
            "--project",
            "pgloom",
            "--goal",
            "Add contract-backed delivery.",
            "--json",
            "--database-url",
            database_url,
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["feature"]["metadata"]["agent_topology"]["planning"] == "multi_agent"
    assert payload["feature"]["metadata"]["agent_topology"]["review"] == "multi_agent"
    assert (
        payload["feature"]["metadata"]["agent_topology"]["implementation"]
        == "parallel_candidates"
    )
    assert payload["feature"]["metadata"]["project"]["github_repo"] == "joshorig/pgloom"
    assert payload["planner_task"]["payload"]["requires_multi_agent_council"] is True

    show_result = CliRunner().invoke(
        app,
        ["project", "show", "pgloom", "--json", "--database-url", database_url],
    )
    assert show_result.exit_code == 0
    assert json.loads(show_result.output)["smoke_command"] == ["pytest", "-q"]

    disable_result = CliRunner().invoke(
        app,
        ["project", "disable", "pgloom", "--json", "--database-url", database_url],
    )
    assert disable_result.exit_code == 0
    assert json.loads(disable_result.output)["state"] == "disabled"

    disabled_list = CliRunner().invoke(
        app,
        ["project", "list", "--state", "disabled", "--json", "--database-url", database_url],
    )
    assert disabled_list.exit_code == 0
    assert [row["name"] for row in json.loads(disabled_list.output)] == ["pgloom"]

    enable_result = CliRunner().invoke(
        app,
        ["project", "enable", "pgloom", "--json", "--database-url", database_url],
    )
    assert enable_result.exit_code == 0
    assert json.loads(enable_result.output)["state"] == "active"


def test_feature_create_requires_registered_project(database_url: str) -> None:
    result = CliRunner().invoke(
        app,
        [
            "feature",
            "create",
            "--project",
            "missing",
            "--goal",
            "Should fail.",
            "--database-url",
            database_url,
        ],
    )

    assert result.exit_code != 0
    assert "Project not registered" in result.output


def test_worker_blocks_disabled_project_before_handler(database_url: str) -> None:
    _register_pgloom_project(database_url)
    CliRunner().invoke(
        app,
        ["project", "disable", "pgloom", "--database-url", database_url],
    )
    workflow = create_workflow(domain="engineering", name="disabled", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload={"plan_contract": _plan_contract(workflow["id"]).model_dump(mode="json")},
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="planner", database_url=database_url)

    result = run_worker_once(slot="planner", worker_id="test-worker", database_url=database_url)

    assert result["status"] == "blocked"
    recoveries = list_recovery_actions(feature["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.project_disabled"


def test_worker_blocks_non_planner_without_task_contract(database_url: str) -> None:
    _register_pgloom_project(database_url)
    workflow = create_workflow(
        domain="engineering",
        name="missing-contract",
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.implement",
        slot="implementer",
        payload={"feature_id": feature["id"]},
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="implementer", database_url=database_url)

    result = run_worker_once(slot="implementer", worker_id="test-worker", database_url=database_url)

    assert result["status"] == "blocked"
    recoveries = list_recovery_actions(feature["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.task_contract_missing"


def test_worker_blocks_stale_task_contract(database_url: str) -> None:
    _register_pgloom_project(database_url)
    workflow = create_workflow(
        domain="engineering",
        name="stale-contract",
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    first_plan = create_plan_contract(_plan_contract(feature["id"]), database_url=database_url)
    second = _plan_contract(feature["id"])
    second.supersedes_plan_id = first_plan["id"]
    second.supersession_rationale = "Replace active plan during test."
    create_plan_contract(second, database_url=database_url)
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.implement",
        slot="implementer",
        payload={"feature_id": feature["id"]},
        database_url=database_url,
    )
    upsert_task_contract(
        task["id"],
        _task_contract(feature["id"], first_plan["id"], role="implementer"),
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="implementer", database_url=database_url)

    result = run_worker_once(slot="implementer", worker_id="test-worker", database_url=database_url)

    assert result["status"] == "blocked"
    recoveries = list_recovery_actions(feature["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.stale_task_contract"


def test_worker_blocks_review_without_result_handoff(database_url: str) -> None:
    _register_pgloom_project(database_url)
    workflow = create_workflow(
        domain="engineering",
        name="handoff-missing",
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    plan = create_plan_contract(_plan_contract(feature["id"]), database_url=database_url)
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.review",
        slot="reviewer",
        payload={"feature_id": feature["id"]},
        database_url=database_url,
    )
    upsert_task_contract(
        task["id"],
        _task_contract(feature["id"], plan["id"], role="reviewer", task_type="engineering.review"),
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="reviewer", database_url=database_url)

    result = run_worker_once(slot="reviewer", worker_id="test-worker", database_url=database_url)

    assert result["status"] == "blocked"
    recoveries = list_recovery_actions(feature["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.handoff_missing"


def test_worker_blocks_invalid_implementer_output(database_url: str) -> None:
    _register_pgloom_project(database_url)
    workflow = create_workflow(
        domain="engineering",
        name="invalid-output",
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        database_url=database_url,
    )
    plan = create_plan_contract(_plan_contract(feature["id"]), database_url=database_url)
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.implement",
        slot="implementer",
        payload={"feature_id": feature["id"]},
        database_url=database_url,
    )
    upsert_task_contract(
        task["id"],
        _task_contract(feature["id"], plan["id"], role="implementer"),
        database_url=database_url,
    )
    attach_task(feature["id"], task["id"], role="implementer", database_url=database_url)

    result = run_worker_once(slot="implementer", worker_id="test-worker", database_url=database_url)

    assert result["status"] == "blocked"
    recoveries = list_recovery_actions(feature["id"], database_url=database_url)
    assert recoveries[0]["blocker_code"] == "engineering.qa_handoff_missing"


def _plan_contract(feature_id: str) -> PlanContract:
    return PlanContract(
        feature_id=feature_id,
        project="pgloom",
        problem_statement="Deliver contract-backed autonomous flow.",
        design_contract=DesignContract(
            public_api="pgloom-engineering feature create/show",
            ownership_boundaries="Only pgloom_engineering may change.",
            acceptance_tests=["pytest"],
        ),
        affected_surfaces=["pgloom_engineering", "tests"],
        task_slices=[
            TaskSliceContract(
                slice_id="slice-1",
                role="qa",
                task_type="engineering.qa.author",
                objective="Author failing tests for the contracted feature.",
                allowed_paths=["tests"],
                forbidden_paths=["pgloom"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[["pytest"]],
            ),
            TaskSliceContract(
                slice_id="slice-2",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement the contracted feature.",
                allowed_paths=["pgloom_engineering"],
                forbidden_paths=["pgloom"],
                depends_on=["slice-1"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["pytest"]],
            ),
            TaskSliceContract(
                slice_id="slice-3",
                role="qa",
                task_type="engineering.qa.verify.scrutiny",
                objective="Verify the contracted feature.",
                allowed_paths=["tests"],
                forbidden_paths=["pgloom"],
                depends_on=["slice-2"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["pytest"]],
            ),
            TaskSliceContract(
                slice_id="slice-4",
                role="qa",
                task_type="engineering.qa.verify.usertest",
                objective="Run the contracted feature user-test.",
                allowed_paths=["tests"],
                forbidden_paths=["pgloom"],
                depends_on=["slice-3"],
                expected_outputs=["QAResultContract"],
                verification_commands=[["python", "-m", "pgloom_engineering", "feature", "show"]],
            ),
        ],
        acceptance_test_matrix=["pytest covers contract persistence and dispatch"],
        council_reports=[{"member": "planner-a", "verdict": "approve"}],
    )


def _task_contract(
    feature_id: str,
    plan_contract_id: str,
    *,
    role: str,
    task_type: str = "engineering.implement",
) -> TaskContract:
    return TaskContract(
        feature_id=feature_id,
        plan_contract_id=plan_contract_id,
        role=role,
        task_type=task_type,
        objective="Run contracted task.",
        allowed_paths=["pgloom_engineering"],
        forbidden_paths=["pgloom"],
        expected_outputs=["TaskResultContract"],
        verification_commands=[["pytest"]],
    )


def _register_pgloom_project(database_url: str) -> None:
    result = CliRunner().invoke(
        app,
        [
            "project",
            "register",
            "--name",
            "pgloom",
            "--root",
            ".",
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 0
