from __future__ import annotations

from datetime import UTC, datetime
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
    qa_write_paths: list[str] | None = None,
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
        validation_errors = validate_plan_contract(
            contract,
            origin_contract=origin_contract,
            qa_write_paths=qa_write_paths,
        )
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


def start_worker_run(
    *,
    feature_id: str,
    task_id: str | None,
    role: str,
    phase: str,
    validator_type: str | None = None,
    attempt: int = 1,
    queued_at: datetime | None = None,
    leased_at: datetime | None = None,
    started_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    started = started_at or datetime.now(UTC)
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_worker_runs(
              feature_id, task_id, role, phase, validator_type, attempt, status,
              queued_at, leased_at, started_at, metadata
            )
            values (%s, %s, %s, %s, %s, %s, 'running', %s, %s, %s, %s)
            returning *
            """,
            (
                feature_id,
                task_id,
                role,
                phase,
                validator_type,
                attempt,
                queued_at,
                leased_at,
                started,
                jsonb(metadata or {}),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("worker run insert did not return a row")
    return dict(row)


def finish_worker_run(
    worker_run_id: int,
    *,
    status: str,
    blocker_code: str | None = None,
    commands_run: list[dict[str, Any]] | None = None,
    evidence_ids: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    handoff_id: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        current = conn.execute(
            "select * from engineering_worker_runs where id = %s for update",
            (worker_run_id,),
        ).fetchone()
        if current is None:
            raise RuntimeError(f"worker run not found: {worker_run_id}")
        feature_id = str(current["feature_id"])
        task_id = current["task_id"]
        model_usage = _worker_model_usage(conn, feature_id, task_id)
        token_savior = _worker_token_savior_usage(conn, feature_id, task_id)
        cumulative = _worker_cumulative(conn, feature_id)
        metadata = dict(current["metadata"] or {})
        if metadata_patch:
            metadata.update(metadata_patch)
        metadata["model_usage"] = model_usage["calls"]
        row = conn.execute(
            """
            update engineering_worker_runs
            set status = %s,
                blocker_code = %s,
                finished_at = now(),
                queued_seconds = extract(
                  epoch from (coalesce(leased_at, started_at, now()) - queued_at)
                ),
                leased_seconds = extract(epoch from (started_at - leased_at)),
                running_seconds = extract(epoch from (now() - started_at)),
                input_tokens = %s,
                output_tokens = %s,
                reasoning_tokens = %s,
                cached_input_tokens = %s,
                cache_creation_tokens = %s,
                cost_usd = %s,
                token_savior_original_tokens = %s,
                token_savior_packed_tokens = %s,
                token_savior_saved_tokens = %s,
                token_savior_reduction_ratio = %s,
                rtk_raw_log_tokens = %s,
                rtk_filtered_log_tokens = %s,
                rtk_saved_tokens = %s,
                cumulative_cost_usd = %s,
                cumulative_wall_clock_seconds = %s,
                cumulative_input_tokens = %s,
                cumulative_output_tokens = %s,
                cumulative_tokens_saved = %s,
                cumulative_model_calls = %s,
                commands_run = %s,
                evidence_ids = %s,
                artifact_ids = %s,
                model_usage_ids = %s,
                token_savior_usage_ids = %s,
                handoff_id = %s,
                metadata = %s
            where id = %s
            returning *
            """,
            (
                status,
                blocker_code,
                model_usage["input_tokens"],
                model_usage["output_tokens"],
                model_usage["reasoning_tokens"],
                model_usage["cached_input_tokens"],
                model_usage["cache_creation_tokens"],
                model_usage["cost_usd"],
                token_savior["original_tokens"],
                token_savior["packed_tokens"],
                token_savior["saved_tokens"],
                token_savior["reduction_ratio"],
                token_savior["rtk_raw_log_tokens"],
                token_savior["rtk_filtered_log_tokens"],
                token_savior["rtk_saved_tokens"],
                cumulative["cost_usd"] + float(model_usage["cost_usd"]),
                cumulative["wall_clock_seconds"],
                cumulative["input_tokens"] + int(model_usage["input_tokens"]),
                cumulative["output_tokens"] + int(model_usage["output_tokens"]),
                cumulative["tokens_saved"] + int(token_savior["saved_tokens"]),
                cumulative["model_calls"] + len(model_usage["ids"]),
                jsonb(commands_run or []),
                jsonb(evidence_ids or []),
                jsonb(artifact_ids or []),
                jsonb(model_usage["ids"]),
                jsonb(token_savior["ids"]),
                handoff_id,
                jsonb(metadata),
                worker_run_id,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("worker run update did not return a row")
    return dict(row)


def list_worker_runs(
    feature_id: str, *, database_url: str | None = None
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_worker_runs
            where feature_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_qa_signoff(
    *,
    feature_id: str,
    task_id: str | None,
    plan_contract_id: str | None,
    milestone_id: str | None,
    validator_type: str,
    verdict: str,
    qa_result_contract: dict[str, Any],
    evidence: list[Any] | None = None,
    artifact_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    row_id = new_id("qa_signoff")
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_qa_signoffs(
              id, feature_id, task_id, plan_contract_id, milestone_id,
              validator_type, verdict, qa_result_contract, evidence, artifact_ids, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (feature_id, milestone_id, validator_type)
            do update set
              task_id = excluded.task_id,
              plan_contract_id = excluded.plan_contract_id,
              verdict = excluded.verdict,
              qa_result_contract = excluded.qa_result_contract,
              evidence = excluded.evidence,
              artifact_ids = excluded.artifact_ids,
              metadata = excluded.metadata,
              updated_at = now()
            returning *
            """,
            (
                row_id,
                feature_id,
                task_id,
                plan_contract_id,
                milestone_id,
                validator_type,
                verdict,
                jsonb(qa_result_contract),
                jsonb(evidence or []),
                jsonb(artifact_ids or []),
                jsonb(metadata or {}),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("QA signoff upsert did not return a row")
    return dict(row)


def list_qa_signoffs(
    feature_id: str,
    *,
    milestone_id: str | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    where = ["feature_id = %s"]
    params: list[Any] = [feature_id]
    if milestone_id is not None:
        where.append("milestone_id = %s")
        params.append(milestone_id)
    with connect(database_url) as conn:
        rows = conn.execute(
            f"""
            select *
            from engineering_qa_signoffs
            where {' and '.join(where)}
            order by created_at, id
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def summarize_worker_runs(
    feature_id: str, *, database_url: str | None = None
) -> dict[str, Any]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select role,
                   phase,
                   validator_type,
                   status,
                   count(*) as runs,
                   coalesce(sum(cost_usd), 0) as cost_usd,
                   coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(reasoning_tokens), 0) as reasoning_tokens,
                   coalesce(sum(cached_input_tokens), 0) as cached_input_tokens,
                   coalesce(sum(cache_creation_tokens), 0) as cache_creation_tokens,
                   coalesce(sum(token_savior_saved_tokens + rtk_saved_tokens), 0) as tokens_saved,
                   coalesce(sum(running_seconds), 0) as running_seconds
            from engineering_worker_runs
            where feature_id = %s
            group by role, phase, validator_type, status
            order by role, phase, validator_type, status
            """,
            (feature_id,),
        ).fetchall()
    by_phase = [dict(row) for row in rows]
    return {
        "runs": sum(int(row["runs"]) for row in by_phase),
        "cost_usd": sum(float(row["cost_usd"]) for row in by_phase),
        "input_tokens": sum(int(row["input_tokens"]) for row in by_phase),
        "output_tokens": sum(int(row["output_tokens"]) for row in by_phase),
        "reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in by_phase),
        "cached_input_tokens": sum(int(row["cached_input_tokens"]) for row in by_phase),
        "cache_creation_tokens": sum(int(row["cache_creation_tokens"]) for row in by_phase),
        "tokens_saved": sum(int(row["tokens_saved"]) for row in by_phase),
        "running_seconds": sum(float(row["running_seconds"]) for row in by_phase),
        "by_phase": by_phase,
    }


def _worker_model_usage(conn: Any, feature_id: str, task_id: str | None) -> dict[str, Any]:
    if task_id is None:
        rows = conn.execute(
            """
            select id, profile_name, input_tokens, output_tokens, cost_usd, metadata
            from model_usage
            where workflow_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select id, profile_name, input_tokens, output_tokens, cost_usd, metadata
            from model_usage
            where task_id = %s
            order by created_at, id
            """,
            (task_id,),
        ).fetchall()
    metadata = [dict(row["metadata"] or {}) for row in rows]
    return {
        "ids": [int(row["id"]) for row in rows],
        "calls": [_model_usage_call(row) for row in rows],
        "input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "cost_usd": sum(float(row["cost_usd"]) for row in rows),
        "reasoning_tokens": sum(_metadata_reasoning_tokens(item) for item in metadata),
        "cached_input_tokens": sum(
            _metadata_cached_input_tokens(item) for item in metadata
        ),
        "cache_creation_tokens": sum(
            _metadata_int(item, "cache_creation_input_tokens") for item in metadata
        ),
    }


def _model_usage_call(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row["metadata"] or {})
    return {
        "id": int(row["id"]),
        "profile_name": row.get("profile_name"),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "reasoning_level": metadata.get("reasoning_level"),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "cached_input_tokens": _metadata_cached_input_tokens(metadata),
        "cache_creation_tokens": _metadata_int(metadata, "cache_creation_input_tokens"),
        "reasoning_tokens": _metadata_reasoning_tokens(metadata),
        "prompt_estimated_tokens": _metadata_int(metadata, "prompt_estimated_tokens"),
        "prompt_bytes": _metadata_int(metadata, "prompt_bytes"),
        "stdout_bytes": _metadata_int(metadata, "stdout_bytes"),
        "stderr_bytes": _metadata_int(metadata, "stderr_bytes"),
        "duration_seconds": metadata.get("duration_seconds"),
        "token_count_source": metadata.get("token_count_source"),
        "timed_out": metadata.get("timed_out"),
        "killed": metadata.get("killed"),
    }


def _metadata_cached_input_tokens(metadata: dict[str, Any]) -> int:
    return _metadata_int(metadata, "cached_input_tokens") + _metadata_int(
        metadata, "cache_read_input_tokens"
    )


def _metadata_reasoning_tokens(metadata: dict[str, Any]) -> int:
    return _metadata_int(metadata, "reasoning_tokens") + _metadata_int(
        metadata, "reasoning_output_tokens"
    )


def _worker_token_savior_usage(conn: Any, feature_id: str, task_id: str | None) -> dict[str, Any]:
    if task_id is None:
        rows = conn.execute(
            """
            select id, input_tokens_original, input_tokens_after_savior, tokens_saved,
                   reduction_ratio, metadata
            from engineering_token_savior_usage
            where feature_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select id, input_tokens_original, input_tokens_after_savior, tokens_saved,
                   reduction_ratio, metadata
            from engineering_token_savior_usage
            where feature_id = %s and task_id = %s
            order by created_at, id
            """,
            (feature_id, task_id),
        ).fetchall()
    original = sum(int(row["input_tokens_original"]) for row in rows)
    packed = sum(int(row["input_tokens_after_savior"]) for row in rows)
    saved = sum(int(row["tokens_saved"]) for row in rows)
    rtk_rows = [
        row
        for row in rows
        if isinstance(row["metadata"], dict)
        and row["metadata"].get("method") in {"rtk", "rtk_unavailable", "passthrough"}
    ]
    return {
        "ids": [int(row["id"]) for row in rows],
        "original_tokens": original,
        "packed_tokens": packed,
        "saved_tokens": saved,
        "reduction_ratio": (saved / original) if original else None,
        "rtk_raw_log_tokens": sum(int(row["input_tokens_original"]) for row in rtk_rows),
        "rtk_filtered_log_tokens": sum(int(row["input_tokens_after_savior"]) for row in rtk_rows),
        "rtk_saved_tokens": sum(int(row["tokens_saved"]) for row in rtk_rows),
    }


def _worker_cumulative(conn: Any, feature_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        select coalesce(sum(cost_usd), 0) as cost_usd,
               coalesce(sum(running_seconds), 0) as wall_clock_seconds,
               coalesce(sum(input_tokens), 0) as input_tokens,
               coalesce(sum(output_tokens), 0) as output_tokens,
               coalesce(sum(token_savior_saved_tokens + rtk_saved_tokens), 0) as tokens_saved,
               coalesce(sum(jsonb_array_length(model_usage_ids)), 0) as model_calls
        from engineering_worker_runs
        where feature_id = %s and status <> 'running'
        """,
        (feature_id,),
    ).fetchone()
    if row is None:
        return {
            "cost_usd": 0.0,
            "wall_clock_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens_saved": 0,
            "model_calls": 0,
        }
    return {
        "cost_usd": float(row["cost_usd"]),
        "wall_clock_seconds": float(row["wall_clock_seconds"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "tokens_saved": int(row["tokens_saved"]),
        "model_calls": int(row["model_calls"]),
    }


def _metadata_int(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


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


def record_operator_intervention(
    *,
    feature_id: str,
    actor: str,
    action_type: str,
    payload: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_operator_interventions(feature_id, actor, action_type, payload)
            values (%s, %s, %s, %s)
            returning *
            """,
            (feature_id, actor, action_type, jsonb(payload or {})),
        ).fetchone()
    if row is None:
        raise RuntimeError("operator intervention insert did not return a row")
    return dict(row)


def list_operator_interventions(
    feature_id: str, *, database_url: str | None = None
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_operator_interventions
            where feature_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def feature_is_paused(feature_id: str, *, database_url: str | None = None) -> bool:
    with connect(database_url) as conn:
        row = conn.execute(
            """
            select action_type
            from engineering_operator_interventions
            where feature_id = %s and action_type in ('pause_feature', 'resume_feature')
            order by created_at desc, id desc
            limit 1
            """,
            (feature_id,),
        ).fetchone()
    return bool(row and row["action_type"] == "pause_feature")
