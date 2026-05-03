from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pydantic import BaseModel, Field

from pgloom_engineering.planner import ProjectContext
from pgloom_engineering.planner.token_savior_context import TokenSaviorContextResult


class ProjectPlanningContextCapsule(BaseModel):
    project: str
    project_root: Path
    git_head: str
    query_hash: str
    capsule_version: str
    context: ProjectContext
    packed_context: str
    input_tokens_original: int = Field(ge=0)
    input_tokens_after_savior: int = Field(ge=0)
    tokens_saved: int = Field(ge=0)
    reduction_ratio: float = Field(ge=0, le=1)
    method: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def capsule_query_hash(query: str, *, budget_tokens: int, memory_digest: str = "") -> str:
    payload = f"{query}\n---budget:{budget_tokens}\n---memory:{memory_digest}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_git_head(project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def get_context_capsule(
    *,
    project: str,
    git_head: str,
    query_hash: str,
    capsule_version: str,
    database_url: str | None = None,
) -> ProjectPlanningContextCapsule | None:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            select *
            from engineering_project_context_capsules
            where project = %s
              and git_head = %s
              and query_hash = %s
              and capsule_version = %s
            """,
            (project, git_head, query_hash, capsule_version),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            update engineering_project_context_capsules
            set last_used_at = now()
            where id = %s
            """,
            (row["id"],),
        )
    return _capsule_from_row(dict(row))


def upsert_context_capsule(
    capsule: ProjectPlanningContextCapsule,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    payload = capsule.model_dump(mode="json")
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_project_context_capsules(
              project, project_root, git_head, query_hash, capsule_version,
              context, packed_context, input_tokens_original, input_tokens_after_savior,
              tokens_saved, reduction_ratio, method, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(project, git_head, query_hash, capsule_version) do update set
              context = excluded.context,
              packed_context = excluded.packed_context,
              input_tokens_original = excluded.input_tokens_original,
              input_tokens_after_savior = excluded.input_tokens_after_savior,
              tokens_saved = excluded.tokens_saved,
              reduction_ratio = excluded.reduction_ratio,
              method = excluded.method,
              metadata = excluded.metadata,
              last_used_at = now()
            returning *
            """,
            (
                capsule.project,
                str(capsule.project_root),
                capsule.git_head,
                capsule.query_hash,
                capsule.capsule_version,
                jsonb(payload["context"]),
                capsule.packed_context,
                capsule.input_tokens_original,
                capsule.input_tokens_after_savior,
                capsule.tokens_saved,
                capsule.reduction_ratio,
                capsule.method,
                jsonb(capsule.metadata),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("context capsule upsert did not return a row")
    return dict(row)


def capsule_from_token_savior(
    *,
    project: str,
    git_head: str,
    query_hash: str,
    capsule_version: str,
    result: TokenSaviorContextResult,
    metadata: dict[str, Any] | None = None,
) -> ProjectPlanningContextCapsule:
    return ProjectPlanningContextCapsule(
        project=project,
        project_root=result.context.project_root,
        git_head=git_head,
        query_hash=query_hash,
        capsule_version=capsule_version,
        context=result.context,
        packed_context=result.packed_context,
        input_tokens_original=result.input_tokens_original,
        input_tokens_after_savior=result.input_tokens_after_savior,
        tokens_saved=result.tokens_saved,
        reduction_ratio=result.reduction_ratio,
        method=result.method,
        metadata=metadata or {},
    )


def token_savior_from_capsule(
    capsule: ProjectPlanningContextCapsule,
) -> TokenSaviorContextResult:
    return TokenSaviorContextResult(
        context=capsule.context,
        input_tokens_original=capsule.input_tokens_original,
        input_tokens_after_savior=capsule.input_tokens_after_savior,
        tokens_saved=capsule.tokens_saved,
        reduction_ratio=capsule.reduction_ratio,
        method=f"{capsule.method}:cache_hit",
        packed_context=capsule.packed_context,
    )


def _capsule_from_row(row: dict[str, Any]) -> ProjectPlanningContextCapsule:
    return ProjectPlanningContextCapsule(
        project=str(row["project"]),
        project_root=Path(str(row["project_root"])),
        git_head=str(row["git_head"]),
        query_hash=str(row["query_hash"]),
        capsule_version=str(row["capsule_version"]),
        context=ProjectContext.model_validate(row["context"]),
        packed_context=str(row["packed_context"]),
        input_tokens_original=int(row["input_tokens_original"]),
        input_tokens_after_savior=int(row["input_tokens_after_savior"]),
        tokens_saved=int(row["tokens_saved"]),
        reduction_ratio=float(row["reduction_ratio"]),
        method=str(row["method"]),
        metadata=dict(row["metadata"] or {}),
    )
