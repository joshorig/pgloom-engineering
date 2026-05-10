from __future__ import annotations

from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect

from pgloom_engineering.contract_store import (
    list_handoffs,
    list_operator_interventions,
    list_plan_contracts,
    list_recovery_actions,
    list_task_contracts,
    list_worker_runs,
    summarize_worker_runs,
)
from pgloom_engineering.projects import default_agent_topology
from pgloom_engineering.token_savior import list_token_savior_usage, summarize_token_savior_usage


def create_feature(
    *,
    workflow_id: str,
    project: str,
    state: str = "open",
    branch: str | None = None,
    metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_features(id, project, branch, state, metadata)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (workflow_id, project, branch, state, jsonb(metadata or {})),
        ).fetchone()
    if row is None:
        raise RuntimeError("feature insert did not return a row")
    return dict(row)


def get_feature(feature_id: str, *, database_url: str | None = None) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        row = conn.execute(
            "select * from engineering_features where id = %s",
            (feature_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_features(
    *,
    project: str | None = None,
    state: str | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if project is not None:
        conditions.append("project = %s")
        params.append(project)
    if state is not None:
        conditions.append("state = %s")
        params.append(state)
    where_sql = f"where {' and '.join(conditions)}" if conditions else ""
    with connect(database_url) as conn:
        rows = conn.execute(
            f"""
            select *
            from engineering_features
            {where_sql}
            order by updated_at desc, created_at desc, id
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def update_feature_state(
    feature_id: str,
    *,
    state: str,
    pr_url: str | None = None,
    abort_reason: str | None = None,
    abort_detail: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    with connect(database_url) as conn, conn.transaction():
        current = conn.execute(
            "select metadata from engineering_features where id = %s for update",
            (feature_id,),
        ).fetchone()
        if current is None:
            return None
        metadata = dict(current["metadata"] or {})
        if metadata_patch:
            metadata.update(metadata_patch)
        row = conn.execute(
            """
            update engineering_features
            set state = %s,
                pr_url = coalesce(%s, pr_url),
                abort_reason = case
                  when %s::text is null then abort_reason
                  else %s::text
                end,
                abort_detail = case
                  when %s::text is null then abort_detail
                  else %s::text
                end,
                aborted_at = case
                  when %s::text is not null or (%s = 'aborted' and aborted_at is null)
                  then now()
                  else aborted_at
                end,
                metadata = %s,
                updated_at = now()
            where id = %s
            returning *
            """,
            (
                state,
                pr_url,
                abort_reason,
                abort_reason,
                abort_detail,
                abort_detail,
                abort_reason,
                state,
                jsonb(metadata),
                feature_id,
            ),
        ).fetchone()
    return dict(row) if row is not None else None


def attach_task(
    feature_id: str,
    task_id: str,
    *,
    role: str,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_feature_children(feature_id, task_id, role)
            values (%s, %s, %s)
            on conflict (feature_id, task_id) do update set role = excluded.role
            returning *
            """,
            (feature_id, task_id, role),
        ).fetchone()
    if row is None:
        raise RuntimeError("feature child insert did not return a row")
    return dict(row)


def list_feature_tasks(
    feature_id: str,
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select c.role, t.*
            from engineering_feature_children c
            join tasks t on t.id = c.task_id
            where c.feature_id = %s
            order by c.created_at, t.created_at, t.id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_feature_aggregate(
    feature_id: str,
    *,
    events_limit: int = 20,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        feature = conn.execute(
            "select * from engineering_features where id = %s",
            (feature_id,),
        ).fetchone()
        if feature is None:
            return None
        workflow = conn.execute(
            "select * from workflows where id = %s",
            (feature_id,),
        ).fetchone()
        tasks = conn.execute(
            """
            select c.role, t.*
            from engineering_feature_children c
            join tasks t on t.id = c.task_id
            where c.feature_id = %s
            order by c.created_at, t.created_at, t.id
            """,
            (feature_id,),
        ).fetchall()
        task_ids = [row["id"] for row in tasks]
        approvals = _rows_for_task_ids(
            conn,
            task_ids,
            """
            select *
            from approvals
            where task_id = any(%s)
            order by created_at, id
            """,
        )
        artifacts = _rows_for_task_ids(
            conn,
            task_ids,
            """
            select *
            from artifacts
            where task_id = any(%s)
            order by created_at, id
            """,
        )
        recent_events = _rows_for_task_ids(
            conn,
            task_ids,
            """
            select *
            from task_events
            where task_id = any(%s)
            order by created_at desc, id desc
            limit %s
            """,
            extra_params=[events_limit],
        )
        model_usage = _model_usage(conn, feature_id, task_ids)
    token_savior_rows = list_token_savior_usage(feature_id, database_url=database_url)
    token_savior_summary = summarize_token_savior_usage(feature_id, database_url=database_url)
    plan_contracts = list_plan_contracts(feature_id, database_url=database_url)
    task_contracts = list_task_contracts(feature_id, database_url=database_url)
    handoffs = list_handoffs(feature_id, database_url=database_url)
    recovery_actions = list_recovery_actions(feature_id, database_url=database_url)
    operator_interventions = list_operator_interventions(feature_id, database_url=database_url)
    worker_runs = list_worker_runs(feature_id, database_url=database_url)
    worker_run_summary = summarize_worker_runs(feature_id, database_url=database_url)
    feature_dict = dict(feature)
    raw_metadata = feature_dict.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "feature": feature_dict,
        "workflow": dict(workflow) if workflow is not None else None,
        "tasks": [dict(row) for row in tasks],
        "approvals": approvals,
        "artifacts": artifacts,
        "recent_events": recent_events,
        "agent_topology": metadata.get("agent_topology")
        or default_agent_topology().model_dump(mode="json"),
        "plan_contracts": plan_contracts,
        "active_plan_contract": next((row for row in plan_contracts if row.get("active")), None),
        "task_contracts": task_contracts,
        "handoffs": handoffs,
        "recovery_actions": recovery_actions,
        "operator_interventions": operator_interventions,
        "worker_runs": worker_runs,
        "worker_run_summary": worker_run_summary,
        "model_usage": model_usage,
        "token_savior": {
            "summary": token_savior_summary,
            "by_task": token_savior_rows,
        },
    }


def _rows_for_task_ids(
    conn: Any,
    task_ids: list[str],
    sql: str,
    *,
    extra_params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    rows = conn.execute(sql, [task_ids, *(extra_params or [])]).fetchall()
    return [dict(row) for row in rows]


def _model_usage(conn: Any, feature_id: str, task_ids: list[str]) -> dict[str, Any]:
    if task_ids:
        rows = conn.execute(
            """
            select profile_name,
                   coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(cost_usd), 0) as cost_usd,
                   coalesce(sum((metadata->>'cached_input_tokens')::bigint), 0)
                     + coalesce(sum((metadata->>'cache_read_input_tokens')::bigint), 0)
                     as cached_input_tokens,
                   coalesce(sum((metadata->>'cache_creation_input_tokens')::bigint), 0)
                     as cache_creation_tokens,
                   coalesce(sum((metadata->>'reasoning_tokens')::bigint), 0)
                     + coalesce(sum((metadata->>'reasoning_output_tokens')::bigint), 0)
                     as reasoning_tokens,
                   coalesce(sum((metadata->>'prompt_estimated_tokens')::bigint), 0)
                     as prompt_estimated_tokens
            from model_usage
            where workflow_id = %s or task_id = any(%s)
            group by profile_name
            order by profile_name
            """,
            (feature_id, task_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select profile_name,
                   coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(cost_usd), 0) as cost_usd,
                   coalesce(sum((metadata->>'cached_input_tokens')::bigint), 0)
                     + coalesce(sum((metadata->>'cache_read_input_tokens')::bigint), 0)
                     as cached_input_tokens,
                   coalesce(sum((metadata->>'cache_creation_input_tokens')::bigint), 0)
                     as cache_creation_tokens,
                   coalesce(sum((metadata->>'reasoning_tokens')::bigint), 0)
                     + coalesce(sum((metadata->>'reasoning_output_tokens')::bigint), 0)
                     as reasoning_tokens,
                   coalesce(sum((metadata->>'prompt_estimated_tokens')::bigint), 0)
                     as prompt_estimated_tokens
            from model_usage
            where workflow_id = %s
            group by profile_name
            order by profile_name
            """,
            (feature_id,),
        ).fetchall()
    by_profile = [dict(row) for row in rows]
    return {
        "summary": {
            "input_tokens": sum(int(row["input_tokens"]) for row in by_profile),
            "output_tokens": sum(int(row["output_tokens"]) for row in by_profile),
            "cost_usd": sum(float(row["cost_usd"]) for row in by_profile),
            "cached_input_tokens": sum(
                int(row["cached_input_tokens"]) for row in by_profile
            ),
            "cache_creation_tokens": sum(
                int(row["cache_creation_tokens"]) for row in by_profile
            ),
            "reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in by_profile),
            "prompt_estimated_tokens": sum(
                int(row["prompt_estimated_tokens"]) for row in by_profile
            ),
        },
        "by_profile": by_profile,
    }
