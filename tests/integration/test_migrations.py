from __future__ import annotations

import json

from pgloom.approvals import request_approval
from pgloom.artifacts import register_artifact
from pgloom.db.postgres import connect
from pgloom.tasks import enqueue_task
from pgloom.workflows import create_workflow
from typer.testing import CliRunner

from pgloom_engineering.cli import app
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
from pgloom_engineering.token_savior import TokenSaviorUsage, record_token_savior_usage


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
              workflow_id, task_id, profile_name, input_tokens, output_tokens, cost_usd
            )
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (workflow["id"], implementer["id"], "codex", 4300, 300, 0.02),
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

    assert get_feature(feature["id"], database_url=database_url)["pr_url"].endswith("/pull/1")
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
