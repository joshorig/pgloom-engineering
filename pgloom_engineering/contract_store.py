from __future__ import annotations

from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pgloom.ids import new_id

from pgloom_engineering.contracts import (
    PlanContract,
    RecoveryDecisionContract,
    TaskContract,
    contract_hash,
    contract_payload,
    validate_plan_contract,
)


def create_plan_contract(
    contract: PlanContract,
    *,
    planner_task_id: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    payload = contract_payload(contract)
    row_id = new_id("plan")
    with connect(database_url) as conn, conn.transaction():
        active_origin = conn.execute(
            """
            select id, contract
            from engineering_plan_contracts
            where feature_id = %s and active is true
            order by created_at desc
            limit 1
            """,
            (contract.feature_id,),
        ).fetchone()
        origin_contract = dict(active_origin["contract"]) if active_origin else None
        validation_errors = validate_plan_contract(contract, origin_contract=origin_contract)
        status = "valid" if not validation_errors else "invalid"
        if status == "valid":
            conn.execute(
                """
                update engineering_plan_contracts
                set active = false,
                    status = case when status = 'valid' then 'superseded' else status end,
                    updated_at = now()
                where feature_id = %s and active is true
                """,
                (contract.feature_id,),
            )
        row = conn.execute(
            """
            insert into engineering_plan_contracts(
              id, feature_id, planner_task_id, version, status, active, contract_hash,
              contract, validation_errors, council_reports
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                row_id,
                contract.feature_id,
                planner_task_id,
                contract.contract_version,
                status,
                status == "valid",
                contract_hash(payload),
                jsonb(payload),
                jsonb(validation_errors),
                jsonb(contract.council_reports),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("plan contract insert did not return a row")
    return dict(row)


def get_active_plan_contract(
    feature_id: str, *, database_url: str | None = None
) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        row = conn.execute(
            """
            select *
            from engineering_plan_contracts
            where feature_id = %s and active is true
            order by created_at desc
            limit 1
            """,
            (feature_id,),
        ).fetchone()
    return dict(row) if row else None


def list_plan_contracts(
    feature_id: str, *, database_url: str | None = None
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_plan_contracts
            where feature_id = %s
            order by created_at desc, id desc
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_task_contract(
    task_id: str,
    contract: TaskContract,
    *,
    output_contract: dict[str, Any] | None = None,
    status: str = "active",
    validation_errors: list[dict[str, Any]] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    input_payload = contract_payload(contract)
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_task_contracts(
              task_id, feature_id, plan_contract_id, role, contract_version,
              input_contract, input_contract_hash, output_contract, status, validation_errors
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (task_id) do update set
              feature_id = excluded.feature_id,
              plan_contract_id = excluded.plan_contract_id,
              role = excluded.role,
              contract_version = excluded.contract_version,
              input_contract = excluded.input_contract,
              input_contract_hash = excluded.input_contract_hash,
              output_contract = excluded.output_contract,
              status = excluded.status,
              validation_errors = excluded.validation_errors,
              updated_at = now()
            returning *
            """,
            (
                task_id,
                contract.feature_id,
                contract.plan_contract_id,
                contract.role,
                contract.contract_version,
                jsonb(input_payload),
                contract_hash(input_payload),
                jsonb(output_contract or {}),
                status,
                jsonb(validation_errors or []),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("task contract upsert did not return a row")
    return dict(row)


def list_task_contracts(
    feature_id: str, *, database_url: str | None = None
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_task_contracts
            where feature_id = %s
            order by created_at, task_id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_task_contract(
    task_id: str, *, database_url: str | None = None
) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        row = conn.execute(
            """
            select *
            from engineering_task_contracts
            where task_id = %s
            """,
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def record_handoff(
    *,
    feature_id: str,
    from_task_id: str | None,
    to_task_id: str | None,
    handoff_type: str,
    contract: dict[str, Any],
    status: str = "ready",
    database_url: str | None = None,
) -> dict[str, Any]:
    row_id = new_id("handoff")
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_handoffs(
              id, feature_id, from_task_id, to_task_id, handoff_type, contract, status
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (row_id, feature_id, from_task_id, to_task_id, handoff_type, jsonb(contract), status),
        ).fetchone()
    if row is None:
        raise RuntimeError("handoff insert did not return a row")
    return dict(row)


def list_handoffs(feature_id: str, *, database_url: str | None = None) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_handoffs
            where feature_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_task_handoffs(
    task_id: str,
    *,
    handoff_type: str | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["to_task_id = %s"]
    params: list[Any] = [task_id]
    if handoff_type is not None:
        conditions.append("handoff_type = %s")
        params.append(handoff_type)
    with connect(database_url) as conn:
        rows = conn.execute(
            f"""
            select *
            from engineering_handoffs
            where {' and '.join(conditions)}
            order by created_at, id
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def record_recovery_action(
    decision: RecoveryDecisionContract,
    *,
    status: str = "open",
    outcome: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    row_id = new_id("recovery")
    payload = contract_payload(decision)
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_recovery_actions(
              id, feature_id, task_id, blocker_code, action, status, attempt, max_attempts,
              decision_contract, outcome
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                row_id,
                decision.feature_id,
                decision.task_id,
                decision.blocker_code,
                decision.action,
                status,
                decision.attempt,
                decision.max_attempts,
                jsonb(payload),
                outcome,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("recovery action insert did not return a row")
    return dict(row)


def list_recovery_actions(
    feature_id: str, *, database_url: str | None = None
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_recovery_actions
            where feature_id = %s
            order by created_at desc, id desc
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]
