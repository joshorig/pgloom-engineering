from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pgloom.artifacts import register_artifact
from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pgloom.tasks import enqueue_task
from pgloom.workflows import create_workflow

from pgloom_engineering.command_center import store
from pgloom_engineering.command_center.auth import assert_loopback_bind, is_loopback_host
from pgloom_engineering.command_center.realtime import WebSocketHub
from pgloom_engineering.command_center.serializers import serialize_row, usd_to_micros
from pgloom_engineering.contract_store import (
    finish_worker_run,
    record_handoff,
    start_worker_run,
    upsert_task_contract,
)
from pgloom_engineering.contracts import TaskContract
from pgloom_engineering.features import create_feature


def test_usd_to_micros_uses_integer_wire_unit() -> None:
    assert usd_to_micros(Decimal("8.864220")) == 8_864_220
    assert usd_to_micros(Decimal("0.000001")) == 1


def test_serialize_row_converts_usd_and_timestamps() -> None:
    row = {
        "cost_usd": Decimal("0.031200"),
        "created_at": datetime(2026, 5, 10, 12, 34, 56, tzinfo=UTC),
    }
    assert serialize_row(row) == {
        "cost_usd_micros": 31_200,
        "created_at": "2026-05-10T12:34:56Z",
    }


def test_loopback_bind_is_v1_only() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    with pytest.raises(ValueError, match="127.0.0.1"):
        assert_loopback_bind("0.0.0.0")


def test_realtime_hub_overflow_publishes_resync() -> None:
    asyncio.run(_assert_realtime_hub_overflow_publishes_resync())


async def _assert_realtime_hub_overflow_publishes_resync() -> None:
    hub = WebSocketHub(max_queue_size=1)
    async with hub.subscribe() as queue:
        hub.publish({"kind": "worker_run.update", "row_id": 1})
        hub.publish({"kind": "worker_run.update", "row_id": 2})
        event = await queue.get()
    assert event == {"kind": "resync", "reason": "websocket queue overflow"}


def test_worker_run_finish_persists_model_route_cost_and_time(database_url: str) -> None:
    workflow = create_workflow(domain="engineering", name="cc", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="demo",
        database_url=database_url,
    )
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.implement",
        slot="implementer",
        database_url=database_url,
    )
    with connect(database_url) as conn, conn.transaction():
        conn.execute(
            """
            insert into model_usage(
              workflow_id, task_id, profile_name, input_tokens, output_tokens, cost_usd,
              metadata
            )
            values (%s, %s, %s, %s, %s, 0, %s)
            """,
            (
                workflow["id"],
                task["id"],
                "implementer",
                100,
                12,
                jsonb(
                    {
                        "provider": "codex",
                        "model": "gpt-5.5",
                        "reasoning_level": "medium",
                        "cached_input_tokens": 90,
                        "reasoning_output_tokens": 3,
                        "duration_seconds": 1.25,
                    }
                ),
            ),
        )
    artifact = register_artifact(
        workflow_id=workflow["id"],
        task_id=task["id"],
        artifact_type="command-log",
        uri="/tmp/command.log",
        database_url=database_url,
    )

    run = start_worker_run(
        feature_id=feature["id"],
        task_id=task["id"],
        role="implementer",
        phase="implement",
        database_url=database_url,
    )
    finished = finish_worker_run(
        int(run["id"]),
        status="done",
        commands_run=[
            {
                "cmd": ["./gradlew", ":core:test"],
                "exit_code": 0,
                "duration_s": 2.5,
                "artifact_ids": [artifact["id"]],
            }
        ],
        artifact_ids=[artifact["id"]],
        database_url=database_url,
    )

    assert finished["model_provider"] == "codex"
    assert finished["model"] == "gpt-5.5"
    assert finished["reasoning_level"] == "medium"
    assert finished["model_profile"] == "implementer"
    assert finished["model_seconds"] == pytest.approx(1.25)
    assert finished["verification_seconds"] == pytest.approx(2.5)
    assert float(finished["cost_usd"]) == pytest.approx(0.000545)
    feature_row = store.get_feature(feature["id"], database_url=database_url)
    assert feature_row is not None
    assert feature_row["cost_usd_micros"] == 545
    assert store.model_usage(feature["id"], database_url=database_url)[0]["cost_usd_micros"] == 545
    stored_artifact = store.artifacts(feature["id"], database_url=database_url)[0]
    assert stored_artifact["source_command"] == "./gradlew :core:test"
    assert stored_artifact["metadata"]["source_worker_run_id"] == finished["id"]


def test_command_center_exposes_persisted_milestones_handoffs_slots_and_artifacts(
    database_url: str,
) -> None:
    workflow = create_workflow(domain="engineering", name="cc", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="demo",
        database_url=database_url,
    )
    task = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.qa.verify.usertest",
        slot="qa-usertest",
        database_url=database_url,
    )
    with connect(database_url) as conn, conn.transaction():
        conn.execute(
            """
            insert into engineering_plan_contracts(
              id, feature_id, version, status, active, contract_hash, contract
            )
            values ('plan-1', %s, 'engineering.contracts.v1', 'valid', true, 'hash', %s)
            """,
            (feature["id"], jsonb({"milestones": [{"milestone_id": "m2"}]})),
        )
    upsert_task_contract(
        task["id"],
        TaskContract(
            feature_id=feature["id"],
            plan_contract_id="plan-1",
            role="qa",
            task_type="engineering.qa.verify.usertest",
            objective="Exercise the feature through the CLI.",
            inputs={"milestone_id": "m2", "task_slice_id": "qa-usertest"},
            allowed_paths=["qa/"],
            forbidden_paths=[],
        ),
        database_url=database_url,
    )
    handoff = record_handoff(
        feature_id=feature["id"],
        from_task_id=None,
        to_task_id=task["id"],
        handoff_type="plan_to_task",
        contract={
            "inputs": {
                "task_slice_id": "qa-usertest",
                "objective": "Exercise the feature through the CLI.",
            }
        },
        database_url=database_url,
    )
    register_artifact(
        workflow_id=workflow["id"],
        task_id=task["id"],
        artifact_type="command-log",
        uri="/tmp/qa.log",
        metadata={"name": "qa.log", "source_command": "./qa/smoke.sh", "evidence_id": "ev-1"},
        database_url=database_url,
    )
    with connect(database_url) as conn, conn.transaction():
        conn.execute(
            """
            insert into slots(name, concurrency, metadata)
            values ('qa-usertest', 3, '{}'::jsonb)
            on conflict (name) do update set concurrency = excluded.concurrency
            """
        )
        conn.execute(
            """
            insert into resource_locks(resource_key, owner_id, task_id, expires_at)
            values ('engineering:demo:full_app_run', 'worker-1', %s, now() + interval '10 minutes')
            """,
            (task["id"],),
        )

    dag = store.dag(feature["id"], database_url=database_url)
    assert dag["tasks"][0]["milestone_id"] == "m2"
    assert dag["tasks"][0]["task_slice_id"] == "qa-usertest"
    assert handoff["title"] == "qa-usertest"
    assert handoff["summary"] == "Exercise the feature through the CLI."
    assert store.slot_state(feature["id"], database_url=database_url)[0]["max"] == 3
    assert store.slot_state(feature["id"], database_url=database_url)[0]["holding"] == 1
    artifact = store.artifacts(feature["id"], database_url=database_url)[0]
    assert artifact["kind"] == "command-log"
    assert artifact["name"] == "qa.log"
    assert artifact["source_command"] == "./qa/smoke.sh"
    assert artifact["evidence_id"] == "ev-1"
