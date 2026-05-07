from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from pgloom.context import count_tokens
from pgloom.memory_postgres import PostgresMemoryStore
from pydantic import BaseModel, Field

from pgloom_engineering.config import get_settings
from pgloom_engineering.contracts import PlanContract, TaskContract
from pgloom_engineering.planner.context_capsule import (
    capsule_from_token_savior,
    capsule_query_hash,
    current_git_head,
    get_context_capsule,
    token_savior_from_capsule,
    upsert_context_capsule,
)
from pgloom_engineering.planner.token_savior_context import (
    TokenSaviorContextResult,
    build_token_savior_project_context,
)
from pgloom_engineering.projects import ProjectConfig
from pgloom_engineering.token_savior import TokenSaviorUsage, record_token_savior_usage


class RoleContext(BaseModel):
    role: str
    query: str
    packed_context: str
    memory_digest: str = ""
    relevant_paths: list[str] = Field(default_factory=list)
    qa_write_paths: list[str] = Field(default_factory=list)
    token_savior: TokenSaviorContextResult | None = None

    def prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract": "engineering.role_context.v1",
            "role": self.role,
            "query": self.query,
            "packed_context": self.packed_context,
            "memory_digest": self.memory_digest,
            "relevant_paths": self.relevant_paths,
            "qa_write_paths": self.qa_write_paths,
        }
        if self.token_savior is not None:
            payload["token_savior"] = {
                "method": self.token_savior.method,
                "input_tokens_original": self.token_savior.input_tokens_original,
                "input_tokens_after_savior": self.token_savior.input_tokens_after_savior,
                "tokens_saved": self.token_savior.tokens_saved,
                "reduction_ratio": self.token_savior.reduction_ratio,
            }
        return payload


def build_role_context(
    *,
    role: str,
    project: ProjectConfig,
    plan: PlanContract,
    task_contract: TaskContract,
    workflow_id: str,
    database_url: str | None = None,
) -> RoleContext:
    settings = get_settings()
    project_name = str(getattr(project, "name", plan.project))
    query = role_context_query(role=role, plan=plan, task_contract=task_contract)
    memory_digest = build_memory_digest(
        project_name=project_name,
        project_root=project.root,
        query=query,
        workflow_id=workflow_id,
        database_url=database_url,
        budget_tokens=int(getattr(settings, "role_context_memory_budget_tokens", 800)),
    )
    if not bool(getattr(settings, "role_context_token_savior_enabled", False)):
        return RoleContext(
            role=role,
            query=query,
            packed_context=memory_digest,
            memory_digest=memory_digest,
        )

    token_savior = _cached_or_build_token_savior(
        project=project,
        project_name=project_name,
        query=query,
        memory_digest=memory_digest,
        database_url=database_url,
    )
    context = token_savior.context
    return RoleContext(
        role=role,
        query=query,
        packed_context=token_savior.packed_context,
        memory_digest=memory_digest,
        relevant_paths=context.relevant_paths,
        qa_write_paths=context.qa_write_paths,
        token_savior=token_savior,
    )


def record_role_context_usage(
    context: RoleContext,
    *,
    feature_id: str,
    workflow_id: str | None,
    task_id: str | None,
    profile_name: str,
    model_usage_id: int | None,
    database_url: str | None,
) -> int | None:
    token_savior = context.token_savior
    if token_savior is None or token_savior.input_tokens_original == 0:
        return None
    try:
        row = record_token_savior_usage(
            TokenSaviorUsage(
                feature_id=feature_id,
                workflow_id=workflow_id,
                task_id=task_id,
                model_usage_id=model_usage_id,
                profile_name=profile_name,
                input_tokens_original=token_savior.input_tokens_original,
                input_tokens_after_savior=token_savior.input_tokens_after_savior,
                tokens_saved=token_savior.tokens_saved,
                reduction_ratio=token_savior.reduction_ratio,
                metadata={
                    "role": context.role,
                    "method": token_savior.method,
                    "query": context.query,
                    "capsule": "role_context",
                },
            ),
            database_url=database_url,
        )
    except Exception:
        return None
    return int(row["id"])


def role_context_query(
    *,
    role: str,
    plan: PlanContract,
    task_contract: TaskContract,
) -> str:
    parts = [
        role,
        plan.project,
        plan.problem_statement,
        task_contract.objective,
        " ".join(plan.acceptance_test_matrix),
        " ".join(task_contract.expected_outputs),
        " ".join(task_contract.allowed_paths),
        " ".join(plan.affected_surfaces),
    ]
    return "\n".join(part for part in parts if part)


def build_memory_digest(
    *,
    project_name: str,
    project_root: Path,
    query: str,
    workflow_id: str,
    database_url: str | None,
    budget_tokens: int,
) -> str:
    sections = [
        token_savior_memory_digest(project_root, query),
        pgloom_memory_digest(project_name, query, workflow_id, database_url),
    ]
    digest = "\n\n".join(section for section in sections if section.strip())
    if count_tokens(digest) <= budget_tokens:
        return digest
    return digest[: budget_tokens * 4] + "\n...[memory digest truncated]"


def token_savior_memory_digest(project_root: Path, query: str) -> str:
    token_savior_src = Path("/Volumes/devssd/repos/oss/token-savior/src")
    if token_savior_src.exists():
        sys.path.insert(0, str(token_savior_src))
    try:
        memory_db = __import__("token_savior.memory_db", fromlist=["memory_db"])
        observations = memory_db.get_recent_index(
            str(project_root),
            limit=8,
            type_filter=["guardrail", "ruled_out", "convention", "warning", "decision"],
        )
        summaries = memory_db.session_summary_search(
            str(project_root),
            _memory_search_query(query),
            limit=4,
        )
    except Exception:
        return ""
    lines: list[str] = []
    if observations:
        lines.append("# Token Savior memory observations")
        for obs in observations:
            title = obs.get("title") or ""
            obs_type = obs.get("type") or "note"
            symbol = obs.get("symbol") or ""
            lines.append(f"- [{obs_type}] {title} {f'({symbol})' if symbol else ''}".strip())
    if summaries:
        lines.append("# Token Savior session summaries")
        for summary in summaries:
            completed = summary.get("completed") or summary.get("excerpt") or ""
            if completed:
                lines.append(f"- {str(completed)[:240]}")
    return "\n".join(lines)


def pgloom_memory_digest(
    project_name: str,
    query: str,
    workflow_id: str,
    database_url: str | None,
) -> str:
    try:
        store = PostgresMemoryStore(database_url=database_url)
        project_scope = f"project:{project_name}"
        rows = [
            *store.search(workflow_id or None, query, limit=5),
            *store.search(project_scope, query, limit=8),
            *store.search(None, f"{project_name} {query}", limit=5),
        ]
    except Exception:
        return ""
    seen: set[tuple[str, str]] = set()
    lines = ["# pgloom memory"]
    for row in rows:
        key = (row.workflow_id, row.key)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {row.key}: {row.value[:240]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _cached_or_build_token_savior(
    *,
    project: ProjectConfig,
    project_name: str,
    query: str,
    memory_digest: str,
    database_url: str | None,
) -> TokenSaviorContextResult:
    settings = get_settings()
    git_head = current_git_head(project.root)
    budget_tokens = int(getattr(settings, "role_context_token_savior_budget_tokens", 2500))
    query_hash = capsule_query_hash(
        query,
        budget_tokens=budget_tokens,
        memory_digest=memory_digest,
    )
    capsule_version = str(getattr(settings, "role_context_capsule_version", "role-context.v1"))
    if bool(getattr(settings, "role_context_capsule_cache_enabled", False)):
        try:
            cached = get_context_capsule(
                project=project_name,
                git_head=git_head,
                query_hash=query_hash,
                capsule_version=capsule_version,
                database_url=database_url,
            )
        except Exception:
            cached = None
        if cached is not None:
            return token_savior_from_capsule(cached)

    token_savior = build_token_savior_project_context(
        project_root=project.root,
        query=query,
        budget_tokens=budget_tokens,
        memory_digest=memory_digest,
    )
    if bool(getattr(settings, "role_context_capsule_cache_enabled", False)):
        try:
            upsert_context_capsule(
                capsule_from_token_savior(
                    project=project_name,
                    git_head=git_head,
                    query_hash=query_hash,
                    capsule_version=capsule_version,
                    result=token_savior,
                    metadata={"cache": "miss", "role_context": True},
                ),
                database_url=database_url,
            )
        except Exception:
            pass
    return token_savior


def _memory_search_query(query: str) -> str:
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", query)]
    useful = [
        word
        for word in words
        if len(word) >= 4
        and word
        not in {
            "with",
            "that",
            "this",
            "from",
            "into",
            "task",
            "role",
            "project",
            "feature",
        }
    ]
    return " ".join(dict.fromkeys(useful[:12])) or query[:200]
