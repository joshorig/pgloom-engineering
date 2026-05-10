from __future__ import annotations

import getpass
from collections.abc import Iterable
from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect

from pgloom_engineering.command_center.serializers import serialize_row, serialize_rows

_MODEL_USAGE_COST_SQL = """
case
  when coalesce(cost_usd, 0) <> 0 then cost_usd
  when metadata->>'provider' = 'codex' then (
    greatest(
      0,
      input_tokens
        - coalesce((metadata->>'cached_input_tokens')::integer, 0)
        - coalesce((metadata->>'cache_read_input_tokens')::integer, 0)
    ) * 5.0
    + (
      coalesce((metadata->>'cached_input_tokens')::integer, 0)
      + coalesce((metadata->>'cache_read_input_tokens')::integer, 0)
    ) * 0.50
    + output_tokens * 30.0
    + (
      coalesce((metadata->>'reasoning_tokens')::integer, 0)
      + coalesce((metadata->>'reasoning_output_tokens')::integer, 0)
    ) * 30.0
  ) / 1000000.0
  else cost_usd
end
"""


def _fetchall(
    sql: str,
    params: Iterable[Any] = (),
    *,
    database_url: str | None,
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(sql, list(params)).fetchall()
    return [dict(row) for row in rows]


def _fetchone(
    sql: str,
    params: Iterable[Any] = (),
    *,
    database_url: str | None,
) -> dict[str, Any] | None:
    with connect(database_url) as conn:
        row = conn.execute(sql, list(params)).fetchone()
    return dict(row) if row is not None else None


def list_features(*, database_url: str | None, limit: int = 50) -> list[dict[str, Any]]:
    rows = _fetchall(
        f"""
        select
          ef.id as feature_id,
          ef.project,
          coalesce(ef.branch, '-') as branch,
          ef.state as feature_state,
          w.state as workflow_state,
          coalesce(s.paused, false) as paused,
          case
            when coalesce(s.paused, false) then 'paused'
            else coalesce(w.state, ef.state)
          end as state,
          coalesce(ef.pr_url, '-') as pr_url,
          ef.created_at,
          ef.updated_at,
          coalesce(r.runs, 0) as runs,
          greatest(coalesce(r.cost_usd, 0), coalesce(mu.cost_usd, 0)) as cost_usd,
          coalesce(r.roles_seen, '') as roles_seen,
          r.last_blocker
        from engineering_features ef
        left join workflows w on w.id = ef.id
        left join engineering_feature_intervention_state s on s.feature_id = ef.id
        left join lateral (
          select count(*) as runs,
                 coalesce(sum(cost_usd), 0) as cost_usd,
                 string_agg(distinct role, ',' order by role) as roles_seen,
                 (array_agg(blocker_code order by started_at desc)
                    filter (where blocker_code is not null))[1] as last_blocker
          from engineering_worker_runs ewr
          where ewr.feature_id = ef.id
        ) r on true
        left join lateral (
          select coalesce(sum({_MODEL_USAGE_COST_SQL}), 0) as cost_usd
          from model_usage mu
          where mu.workflow_id = ef.id
        ) mu on true
        order by ef.created_at desc
        limit %s
        """,
        (limit,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def get_feature(feature_id: str, *, database_url: str | None) -> dict[str, Any] | None:
    row = _fetchone(
        f"""
        select ef.id,
               ef.project,
               ef.branch,
               ef.pr_url,
               ef.state as feature_state,
               w.state as workflow_state,
               case
                 when coalesce(s.paused, false) then 'paused'
                 else coalesce(w.state, ef.state)
               end as state,
               ef.metadata,
               ef.created_at,
               ef.updated_at,
               coalesce(s.paused, false) as paused,
               coalesce(r.runs, 0) as runs,
               greatest(coalesce(r.cost_usd, 0), coalesce(mu.cost_usd, 0)) as cost_usd,
               coalesce(r.running_seconds, 0) as running_seconds,
               coalesce(r.input_tokens, 0) as input_tokens,
               coalesce(r.cached_input_tokens, 0) as cached_input_tokens,
               coalesce(r.output_tokens, 0) as output_tokens,
               coalesce(r.reasoning_tokens, 0) as reasoning_tokens,
               coalesce(r.token_savior_saved_tokens, 0) as token_savior_saved_tokens,
               coalesce(r.rtk_saved_tokens, 0) as rtk_saved_tokens
        from engineering_features ef
        left join workflows w on w.id = ef.id
        left join engineering_feature_intervention_state s on s.feature_id = ef.id
        left join lateral (
          select count(*) as runs,
                 coalesce(sum(cost_usd), 0) as cost_usd,
                 coalesce(sum(running_seconds), 0) as running_seconds,
                 coalesce(sum(input_tokens), 0) as input_tokens,
                 coalesce(sum(cached_input_tokens), 0) as cached_input_tokens,
                 coalesce(sum(output_tokens), 0) as output_tokens,
                 coalesce(sum(reasoning_tokens), 0) as reasoning_tokens,
                 coalesce(sum(token_savior_saved_tokens), 0) as token_savior_saved_tokens,
                 coalesce(sum(rtk_saved_tokens), 0) as rtk_saved_tokens
          from engineering_worker_runs
          where feature_id = ef.id
        ) r on true
        left join lateral (
          select coalesce(sum({_MODEL_USAGE_COST_SQL}), 0) as cost_usd
          from model_usage
          where workflow_id = ef.id
        ) mu on true
        where ef.id = %s
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_row(row) if row is not None else None


def list_runs(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select *
        from engineering_worker_runs
        where feature_id = %s
        order by coalesce(started_at, created_at), id
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def list_all_runs(*, database_url: str | None, limit: int = 500) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select ewr.*, ef.project
        from engineering_worker_runs ewr
        join engineering_features ef on ef.id = ewr.feature_id
        order by coalesce(ewr.started_at, ewr.created_at) desc, ewr.id desc
        limit %s
        """,
        (limit,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def aggregate_runs(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select role, phase, validator_type, status, count(*) as runs,
               sum(input_tokens) as input_tokens,
               sum(cached_input_tokens) as cached_input_tokens,
               sum(cache_creation_tokens) as cache_creation_tokens,
               sum(output_tokens) as output_tokens,
               sum(reasoning_tokens) as reasoning_tokens,
               sum(running_seconds) as running_seconds,
               sum(model_seconds) as model_seconds,
               sum(verification_seconds) as verification_seconds,
               sum(blocked_seconds) as blocked_seconds,
               sum(cost_usd) as cost_usd,
               sum(token_savior_saved_tokens) as token_savior_saved_tokens,
               sum(rtk_saved_tokens) as rtk_saved_tokens
        from engineering_worker_runs
        where feature_id = %s
        group by role, phase, validator_type, status
        order by role, phase, validator_type, status
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def global_model_usage(*, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        f"""
        select ef.project,
               string_agg(distinct profile_name, ','
                 order by profile_name) as profile_name,
               count(*) as calls,
               sum(input_tokens) as input_tokens,
               sum(output_tokens) as output_tokens,
               sum(
                 coalesce((mu.metadata->>'cached_input_tokens')::integer, 0)
               ) as cached_input_tokens,
               sum(coalesce(
                 (mu.metadata->>'cache_creation_input_tokens')::integer, 0
               )) as cache_creation_tokens,
               sum(
                 coalesce((mu.metadata->>'reasoning_tokens')::integer, 0)
                 + coalesce((mu.metadata->>'reasoning_output_tokens')::integer, 0)
               ) as reasoning_tokens,
               sum({_MODEL_USAGE_COST_SQL.replace("metadata", "mu.metadata")}) as cost_usd,
               string_agg(distinct coalesce(mu.metadata->>'provider', '-'), ','
                 order by coalesce(mu.metadata->>'provider', '-')) as providers,
               string_agg(distinct coalesce(mu.metadata->>'model', '-'), ','
                 order by coalesce(mu.metadata->>'model', '-')) as models
        from model_usage mu
        join engineering_features ef on ef.id = mu.workflow_id
        group by ef.project,
                 coalesce(mu.metadata->>'provider', '-'),
                 coalesce(mu.metadata->>'model', '-')
        order by ef.project,
                 coalesce(mu.metadata->>'provider', '-'),
                 coalesce(mu.metadata->>'model', '-')
        """,
        database_url=database_url,
    )
    return serialize_rows(rows)


def model_usage(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        f"""
        select string_agg(distinct profile_name, ','
                 order by profile_name) as profile_name,
               count(*) as calls,
               sum(input_tokens) as input_tokens,
               sum(output_tokens) as output_tokens,
               sum(coalesce((metadata->>'cached_input_tokens')::integer, 0)) as cached_input_tokens,
               sum(coalesce(
                 (metadata->>'cache_creation_input_tokens')::integer, 0
               )) as cache_creation_tokens,
               sum(
                 coalesce((metadata->>'reasoning_tokens')::integer, 0)
                 + coalesce((metadata->>'reasoning_output_tokens')::integer, 0)
               ) as reasoning_tokens,
               sum({_MODEL_USAGE_COST_SQL}) as cost_usd,
               string_agg(distinct coalesce(metadata->>'provider', '-'), ','
                 order by coalesce(metadata->>'provider', '-')) as providers,
               string_agg(distinct coalesce(metadata->>'model', '-'), ','
                 order by coalesce(metadata->>'model', '-')) as models
        from model_usage
        where workflow_id = %s
        group by coalesce(metadata->>'provider', '-'), coalesce(metadata->>'model', '-')
        order by coalesce(metadata->>'provider', '-'), coalesce(metadata->>'model', '-')
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def global_token_savior(*, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select ef.project,
               coalesce(etsu.profile_name, '-') as profile_name,
               count(*) as rows,
               sum(input_tokens_original) as input_tokens_original,
               sum(input_tokens_after_savior) as input_tokens_after_savior,
               sum(tokens_saved) as tokens_saved,
               avg(reduction_ratio) as reduction_ratio,
               sum(estimated_cost_saved_usd) as estimated_cost_saved_usd
        from engineering_token_savior_usage etsu
        join engineering_features ef on ef.id = etsu.feature_id
        group by ef.project, coalesce(etsu.profile_name, '-')
        order by ef.project, coalesce(etsu.profile_name, '-')
        """,
        database_url=database_url,
    )
    return serialize_rows(rows)


def token_savior(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select profile_name, count(*) as rows,
               sum(input_tokens_original) as input_tokens_original,
               sum(input_tokens_after_savior) as input_tokens_after_savior,
               sum(tokens_saved) as tokens_saved,
               avg(reduction_ratio) as reduction_ratio,
               sum(estimated_cost_saved_usd) as estimated_cost_saved_usd
        from engineering_token_savior_usage
        where feature_id = %s
        group by profile_name
        order by profile_name
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def global_slot_state(*, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        with slot_defs as (
          select name, concurrency
          from slots
          union
          select distinct slot as name, 1 as concurrency
          from tasks
          where slot is not null
        ),
        slot_tasks as (
          select
            t.id,
            t.workflow_id,
            t.state,
            t.task_type,
            t.slot,
            t.lease_owner,
            t.lease_expires_at,
            t.updated_at,
            ef.project
          from tasks t
          left join engineering_features ef on ef.id = t.workflow_id
          where t.slot is not null
        )
        select
          sd.name as slot,
          greatest(
            coalesce(max(sd.concurrency), 1),
            count(distinct st.id) filter (where st.state in ('running', 'leased'))
          ) as max,
          count(distinct st.id) filter (where st.state in ('running', 'leased')) as holding,
          count(distinct st.id) filter (where st.state = 'running') as running,
          count(distinct st.id) filter (where st.state = 'leased') as leased,
          count(distinct st.id) filter (where st.state = 'queued') as queued,
          count(distinct st.id) filter (where st.state = 'blocked') as blocked,
          count(distinct rl.resource_key) filter (where rl.expires_at > now()) as lock_count,
          coalesce(jsonb_agg(jsonb_build_object(
            'project', st.project,
            'workflow_id', st.workflow_id,
            'task_id', st.id,
            'task_type', st.task_type,
            'state', st.state,
            'lease_owner', st.lease_owner,
            'lease_expires_at', st.lease_expires_at,
            'updated_at', st.updated_at
          )) filter (
            where st.id is not null
              and st.state in ('running', 'leased', 'queued', 'blocked')
          ), '[]'::jsonb) as tasks,
          coalesce(jsonb_agg(distinct jsonb_build_object(
            'project', st.project,
            'workflow_id', st.workflow_id,
            'resource_key', rl.resource_key,
            'owner_id', rl.owner_id,
            'task_id', rl.task_id,
            'expires_at', rl.expires_at
          )) filter (
            where rl.resource_key is not null
              and rl.expires_at > now()
          ), '[]'::jsonb) as holds
        from slot_defs sd
        left join slot_tasks st on st.slot = sd.name
        left join resource_locks rl on rl.task_id = st.id
        group by sd.name
        order by sd.name
        """,
        database_url=database_url,
    )
    return serialize_rows(rows)


def slot_state(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        with slot_defs as (
          select name, concurrency
          from slots
          union
          select distinct slot as name, 1 as concurrency
          from tasks
          where workflow_id = %s and slot is not null
        ),
        slot_tasks as (
          select
            t.id,
            t.workflow_id,
            t.state,
            t.task_type,
            t.slot,
            t.lease_owner,
            t.lease_expires_at,
            t.updated_at,
            ef.project
          from tasks t
          left join engineering_features ef on ef.id = t.workflow_id
          where t.workflow_id = %s and t.slot is not null
        )
        select
          sd.name as slot,
          greatest(
            coalesce(max(sd.concurrency), 1),
            count(distinct st.id) filter (where st.state in ('running', 'leased'))
          ) as max,
          count(distinct st.id) filter (where st.state in ('running', 'leased')) as holding,
          count(distinct st.id) filter (where st.state = 'running') as running,
          count(distinct st.id) filter (where st.state = 'leased') as leased,
          count(distinct st.id) filter (where st.state = 'queued') as queued,
          count(distinct st.id) filter (where st.state = 'blocked') as blocked,
          count(distinct rl.resource_key) filter (where rl.expires_at > now()) as lock_count,
          coalesce(jsonb_agg(jsonb_build_object(
            'project', st.project,
            'workflow_id', st.workflow_id,
            'task_id', st.id,
            'task_type', st.task_type,
            'state', st.state,
            'lease_owner', st.lease_owner,
            'lease_expires_at', st.lease_expires_at,
            'updated_at', st.updated_at
          )) filter (
            where st.id is not null
              and st.state in ('running', 'leased', 'queued', 'blocked')
          ), '[]'::jsonb) as tasks,
          coalesce(jsonb_agg(distinct jsonb_build_object(
            'project', st.project,
            'workflow_id', st.workflow_id,
            'resource_key', rl.resource_key,
            'owner_id', rl.owner_id,
            'task_id', rl.task_id,
            'expires_at', rl.expires_at
          )) filter (
            where rl.resource_key is not null
              and rl.expires_at > now()
          ), '[]'::jsonb) as holds
        from slot_defs sd
        left join slot_tasks st on st.slot = sd.name
        left join resource_locks rl on rl.task_id = st.id
        group by sd.name
        order by sd.name
        """,
        (feature_id, feature_id),
        database_url=database_url,
    )
    return serialize_rows(rows)


def artifacts(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select
          a.id,
          a.workflow_id as feature_id,
          a.task_id,
          a.artifact_type as kind,
          coalesce(
            a.metadata->>'name',
            a.metadata->>'display_name',
            nullif(regexp_replace(a.uri, '^.*/', ''), ''),
            a.id
          ) as name,
          a.uri as path,
          a.size_bytes,
          a.sha256,
          a.metadata->>'source_command' as source_command,
          a.metadata->>'evidence_id' as evidence_id,
          a.metadata,
          a.created_at
        from artifacts a
        where a.workflow_id = %s
        order by a.created_at, a.id
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def list_table(
    feature_id: str,
    table: str,
    order: str,
    *,
    database_url: str | None,
) -> list[dict[str, Any]]:
    rows = _fetchall(
        f"select * from {table} where feature_id = %s order by {order}",
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def self_repair(feature_id: str, *, database_url: str | None) -> list[dict[str, Any]]:
    rows = _fetchall(
        """
        select i.*,
               (select count(*) from engineering_self_repair_deliberations d
                where d.issue_id = i.id) as deliberations
        from engineering_self_repair_issues i
        join engineering_feature_children efc on efc.task_id = i.task_id
        where efc.feature_id = %s
        order by i.created_at
        """,
        (feature_id,),
        database_url=database_url,
    )
    return serialize_rows(rows)


def dag(feature_id: str, *, database_url: str | None) -> dict[str, Any]:
    plans = list_table(
        feature_id,
        "engineering_plan_contracts",
        "created_at desc",
        database_url=database_url,
    )
    tasks = list_table(
        feature_id,
        "engineering_task_contracts",
        "created_at, task_id",
        database_url=database_url,
    )
    runs = list_runs(feature_id, database_url=database_url)
    last_run_by_task: dict[str, dict[str, Any]] = {}
    for run in runs:
        task_id = run.get("task_id")
        if task_id:
            last_run_by_task[str(task_id)] = run
    active_plan = next((plan for plan in plans if plan.get("active")), plans[0] if plans else {})
    contract = active_plan.get("contract") or {}
    milestones = _milestones(contract, tasks)
    edges = _edges(contract, tasks)
    return {
        "milestones": milestones,
        "tasks": [
            {
                "id": task["task_id"],
                "role": task["role"],
                "status": task["status"],
                "depends_on": _task_deps(task),
                "milestone_id": _task_milestone(task),
                "task_slice_id": task.get("task_slice_id"),
                "last_run": last_run_by_task.get(str(task["task_id"])),
            }
            for task in tasks
        ],
        "edges": edges,
    }


def create_intervention(
    feature_id: str,
    *,
    action_type: str,
    payload: dict[str, Any],
    actor: str | None,
    database_url: str | None,
) -> dict[str, Any]:
    resolved_actor = actor or f"operator:{getpass.getuser()}@local"
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_operator_interventions(feature_id, actor, action_type, payload)
            values (%s, %s, %s, %s)
            returning *
            """,
            (feature_id, resolved_actor, action_type, jsonb(payload)),
        ).fetchone()
    if row is None:
        raise RuntimeError("intervention insert did not return a row")
    return serialize_row(dict(row))


def _milestones(contract: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = contract.get("milestones")
    if isinstance(raw, list):
        out = []
        for idx, item in enumerate(raw):
            if isinstance(item, dict):
                mid = str(item.get("id") or item.get("milestone_id") or f"m{idx}")
                task_ids = item.get("task_ids") or item.get("tasks") or []
                out.append(
                    {
                        "id": mid,
                        "label": str(item.get("label") or item.get("name") or mid),
                        "task_ids": task_ids or [
                            str(task["task_id"]) for task in tasks if _task_milestone(task) == mid
                        ],
                    }
                )
        if out:
            return out
    grouped: dict[str, list[str]] = {}
    for task in tasks:
        mid = _task_milestone(task)
        grouped.setdefault(mid, []).append(str(task["task_id"]))
    return [{"id": key, "label": key, "task_ids": value} for key, value in grouped.items()]


def _edges(contract: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = contract.get("edges") or contract.get("dependencies")
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, dict):
                src = item.get("from") or item.get("source") or item.get("depends_on")
                dst = item.get("to") or item.get("target") or item.get("task_id")
                if src and dst:
                    out.append({"from": src, "to": dst, "kind": item.get("kind", "dep")})
        if out:
            return out
    out = []
    for task in tasks:
        for dep in _task_deps(task):
            out.append({"from": dep, "to": task["task_id"], "kind": "dep"})
    return out


def _task_milestone(task: dict[str, Any]) -> str:
    if task.get("milestone_id"):
        return str(task["milestone_id"])
    raw_data = task.get("input_contract") or task.get("output_contract") or {}
    data = raw_data if isinstance(raw_data, dict) else {}
    raw_inputs = data.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    return str(
        data.get("milestone_id")
        or data.get("milestone")
        or inputs.get("milestone_id")
        or inputs.get("milestone")
        or "unassigned"
    )


def _task_deps(task: dict[str, Any]) -> list[str]:
    raw_data = task.get("input_contract") or {}
    data = raw_data if isinstance(raw_data, dict) else {}
    raw_inputs = data.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    deps = (
        data.get("depends_on")
        or data.get("dependencies")
        or inputs.get("depends_on")
        or inputs.get("dependencies")
        or []
    )
    if isinstance(deps, list):
        return [str(dep) for dep in deps]
    return []
