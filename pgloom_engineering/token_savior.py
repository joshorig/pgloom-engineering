from __future__ import annotations

from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pydantic import BaseModel, Field


class TokenSaviorUsage(BaseModel):
    feature_id: str
    workflow_id: str | None = None
    task_id: str | None = None
    model_usage_id: int | None = None
    profile_name: str | None = None
    input_tokens_original: int = Field(ge=0)
    input_tokens_after_savior: int = Field(ge=0)
    tokens_saved: int = Field(ge=0)
    reduction_ratio: float = Field(ge=0, le=1)
    estimated_cost_saved_usd: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


def record_token_savior_usage(
    usage: TokenSaviorUsage,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_token_savior_usage(
              feature_id, workflow_id, task_id, model_usage_id, profile_name,
              input_tokens_original, input_tokens_after_savior, tokens_saved,
              reduction_ratio, estimated_cost_saved_usd, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                usage.feature_id,
                usage.workflow_id,
                usage.task_id,
                usage.model_usage_id,
                usage.profile_name,
                usage.input_tokens_original,
                usage.input_tokens_after_savior,
                usage.tokens_saved,
                usage.reduction_ratio,
                usage.estimated_cost_saved_usd,
                jsonb(usage.metadata),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("token-savior usage insert did not return a row")
    return dict(row)


def list_token_savior_usage(
    feature_id: str,
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select *
            from engineering_token_savior_usage
            where feature_id = %s
            order by created_at, id
            """,
            (feature_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def summarize_token_savior_usage(
    feature_id: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn:
        row = conn.execute(
            """
            select
              coalesce(sum(input_tokens_original), 0) as input_tokens_original,
              coalesce(sum(input_tokens_after_savior), 0) as input_tokens_after_savior,
              coalesce(sum(tokens_saved), 0) as tokens_saved,
              coalesce(sum(estimated_cost_saved_usd), 0) as estimated_cost_saved_usd
            from engineering_token_savior_usage
            where feature_id = %s
            """,
            (feature_id,),
        ).fetchone()
    original = int(row["input_tokens_original"]) if row else 0
    saved = int(row["tokens_saved"]) if row else 0
    return {
        "input_tokens_original": original,
        "input_tokens_after_savior": int(row["input_tokens_after_savior"]) if row else 0,
        "tokens_saved": saved,
        "reduction_ratio": saved / original if original else 0.0,
        "estimated_cost_saved_usd": float(row["estimated_cost_saved_usd"]) if row else 0.0,
    }
