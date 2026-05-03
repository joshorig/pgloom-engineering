from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pgloom.models.cli import CLIModelProfile

from pgloom_engineering.contracts import FeatureGoalContract, validate_plan_contract
from pgloom_engineering.path_policy import discover_qa_write_paths
from pgloom_engineering.planner import CouncilConfig, PlannerCouncil
from pgloom_engineering.planner.context_capsule import (
    capsule_from_token_savior,
    capsule_query_hash,
    current_git_head,
    get_context_capsule,
    token_savior_from_capsule,
    upsert_context_capsule,
)
from pgloom_engineering.planner.critic import CriticVerdict
from pgloom_engineering.planner.exceptions import PlannerCouncilExhausted
from pgloom_engineering.planner.token_savior_context import (
    TokenSaviorContextResult,
    _filter_relevant_qa_write_paths,
    build_token_savior_project_context,
)
from pgloom_engineering.roles.planner import _build_memory_digest

EVAL_CONTEXT_CAPSULE_VERSION = "planning-context-capsule.v2"


@dataclass
class DirectResult:
    text: str
    model_usage_id: int | None = None


class DirectProvider:
    def __init__(
        self,
        *,
        backend: str,
        output_dir: Path,
        model: str,
        reasoning: str,
        mechanical_model: str | None,
        mechanical_reasoning: str | None,
        claude_max_budget_usd: str,
    ) -> None:
        self.backend = backend
        self.output_dir = output_dir
        self.model = model
        self.reasoning = reasoning
        self.mechanical_model = mechanical_model
        self.mechanical_reasoning = mechanical_reasoning
        self.claude_max_budget_usd = claude_max_budget_usd
        self.counts: dict[str, int] = {}
        self.usage_path = output_dir / "model_usage.jsonl"

    def invoke(
        self,
        *,
        profile: CLIModelProfile,
        prompt: str,
        input_tokens_hint: int | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> DirectResult:
        del input_tokens_hint, workflow_id, task_id
        count = self.counts.get(profile.name, 0) + 1
        self.counts[profile.name] = count
        prompt_path = self.output_dir / f"{profile.name}-{count:02d}.prompt.txt"
        response_path = self.output_dir / f"{profile.name}-{count:02d}.response.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        command = _command(
            self.backend,
            profile.name,
            model=self._model_for_profile(profile.name),
            reasoning=self._reasoning_for_profile(profile.name),
            claude_max_budget_usd=self.claude_max_budget_usd,
        )
        started = time.monotonic()
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd="/Volumes/devssd/repos/oss/pgloom-engineering",
            timeout=600,
            check=False,
        )
        elapsed_seconds = time.monotonic() - started
        raw_response = completed.stdout + completed.stderr
        response_path.write_text(raw_response, encoding="utf-8")
        usage = _usage_record(
            backend=self.backend,
            profile_name=profile.name,
            call_index=count,
            command=command,
            model=self._model_for_profile(profile.name),
            reasoning=self._reasoning_for_profile(profile.name),
            elapsed_seconds=elapsed_seconds,
            prompt=prompt,
            response=raw_response,
        )
        with self.usage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(usage, sort_keys=True) + "\n")
        result_text = _model_result_text(self.backend, completed.stdout)
        if completed.returncode != 0:
            raise RuntimeError(
                f"{self.backend} failed for {profile.name}: {completed.stderr or completed.stdout}"
            )
        return DirectResult(text=result_text)

    def _model_for_profile(self, profile_name: str) -> str:
        if profile_name in {"planner-consolidator", "planner-critic"}:
            return self.mechanical_model or self.model
        return self.model

    def _reasoning_for_profile(self, profile_name: str) -> str:
        if profile_name in {"planner-consolidator", "planner-critic"}:
            return self.mechanical_reasoning or self.reasoning
        return self.reasoning


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model = args.model or ("sonnet" if args.backend == "claude" else "gpt-5.4-mini")
    reasoning = args.reasoning or "none"
    goal = _feature_goal_from_args(args)
    project_root = Path(args.project_root) if args.project_root else _default_project_root(goal)
    database_url = args.database_url or os.environ.get("PGLOOM_TEST_DATABASE_URL")
    savior = _project_context(goal, project_root, args.context_budget_tokens, database_url)
    context = savior.context
    (output_dir / "token_savior_context.txt").write_text(
        savior.packed_context,
        encoding="utf-8",
    )
    provider = DirectProvider(
        backend=args.backend,
        output_dir=output_dir,
        model=model,
        reasoning=reasoning,
        mechanical_model=args.mechanical_model,
        mechanical_reasoning=args.mechanical_reasoning,
        claude_max_budget_usd=args.claude_max_budget_usd,
    )
    council = PlannerCouncil(
        config=CouncilConfig(
            panelist_count=args.panelists,
            max_iterations=args.max_iterations,
            panelist_profile="planner-panelist",
            critic_profile="planner-critic",
            consolidator_profile="planner-consolidator",
            timeout_seconds_per_invocation=600,
        ),
        provider=provider,
    )
    try:
        outcome = council.run(
            feature_goal=goal,
            project_context=context,
            workflow_id=args.workflow_id or f"{goal.project}-{args.roadmap_id or 'live'}",
            task_id=f"planner-{args.backend}",
        )
    except PlannerCouncilExhausted as exc:
        payload = {
            "backend": args.backend,
            "model": model,
            "mechanical_model": args.mechanical_model,
            "reasoning": reasoning if args.backend == "codex" else None,
            "mechanical_reasoning": args.mechanical_reasoning
            if args.backend == "codex"
            else None,
            "status": "exhausted",
            "token_savior": savior.model_dump(mode="json", exclude={"context", "packed_context"}),
            "iterations": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in exc.iterations
            ],
        }
        (output_dir / "outcome.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(json.dumps(_summary(payload), indent=2, sort_keys=True))
        return 2
    validation_errors = validate_plan_contract(outcome.final)
    final = outcome.final.model_dump(mode="json")
    payload = {
        "backend": args.backend,
        "model": model,
        "mechanical_model": args.mechanical_model,
        "reasoning": reasoning if args.backend == "codex" else None,
        "mechanical_reasoning": args.mechanical_reasoning if args.backend == "codex" else None,
        "status": "accepted",
        "accepted_at_iteration": outcome.accepted_at_iteration,
        "token_savior": savior.model_dump(mode="json", exclude={"context", "packed_context"}),
        "validation_errors": validation_errors,
        "final": final,
        "iterations": [item.model_dump(mode="json") for item in outcome.iterations],
    }
    (output_dir / "outcome.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(json.dumps(_summary(payload), indent=2, sort_keys=True))
    return 0 if not validation_errors else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["claude", "codex"], required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--panelists", type=int, default=2)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--model")
    parser.add_argument("--mechanical-model")
    parser.add_argument("--reasoning")
    parser.add_argument("--mechanical-reasoning")
    parser.add_argument("--roadmap-id", choices=["r002", "r003"])
    parser.add_argument("--feature-goal")
    parser.add_argument("--project-root")
    parser.add_argument("--workflow-id")
    parser.add_argument("--context-budget-tokens", type=int, default=3000)
    parser.add_argument("--claude-max-budget-usd", default="5.00")
    parser.add_argument("--database-url")
    return parser.parse_args()


def _command(
    backend: str,
    profile_name: str,
    *,
    model: str,
    reasoning: str,
    claude_max_budget_usd: str,
) -> list[str]:
    if backend == "claude":
        return [
            "claude",
            "-p",
            "--model",
            model,
            "--output-format",
            "json",
            "--max-budget-usd",
            claude_max_budget_usd,
        ]
    profile_reasoning = reasoning if profile_name != "planner-critic" else reasoning
    return [
        "codex",
        "exec",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort=\"{profile_reasoning}\"",
        "-s",
        "read-only",
        "-C",
        "/Volumes/devssd/repos/oss/pgloom-engineering",
        "--ephemeral",
        "--json",
        "-",
    ]


def _usage_record(
    *,
    backend: str,
    profile_name: str,
    call_index: int,
    command: list[str],
    model: str,
    reasoning: str,
    elapsed_seconds: float,
    prompt: str,
    response: str,
) -> dict[str, Any]:
    actual_total_tokens = _actual_total_tokens(response)
    claude_usage = _claude_usage(response) if backend == "claude" else {}
    codex_usage = _codex_usage(response) if backend == "codex" else {}
    if claude_usage.get("total_tokens") is not None:
        actual_total_tokens = int(claude_usage["total_tokens"])
    if codex_usage.get("total_tokens") is not None:
        actual_total_tokens = int(codex_usage["total_tokens"])
    return {
        "backend": backend,
        "profile_name": profile_name,
        "call_index": call_index,
        "model": model,
        "reasoning": reasoning if backend == "codex" else None,
        "command": command,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "estimated_input_tokens": _estimate_tokens(prompt),
        "estimated_output_tokens": _estimate_tokens(response),
        "estimated_total_tokens": _estimate_tokens(prompt) + _estimate_tokens(response),
        "actual_total_tokens": actual_total_tokens,
        "actual_input_tokens": claude_usage.get("input_tokens") or codex_usage.get("input_tokens"),
        "actual_output_tokens": (
            claude_usage.get("output_tokens") or codex_usage.get("output_tokens")
        ),
        "cache_creation_input_tokens": claude_usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": claude_usage.get("cache_read_input_tokens")
        or codex_usage.get("cached_input_tokens"),
        "reasoning_output_tokens": codex_usage.get("reasoning_output_tokens"),
        "total_cost_usd": claude_usage.get("total_cost_usd"),
        "actual_usage_source": _actual_usage_source(
            backend, actual_total_tokens, claude_usage, codex_usage
        ),
    }


def _actual_total_tokens(response: str) -> int | None:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)\s*$", response, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _claude_usage(response: str) -> dict[str, Any]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cache_creation = _int_or_none(usage.get("cache_creation_input_tokens"))
    cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
    token_parts = [input_tokens, output_tokens, cache_creation, cache_read]
    total_tokens = sum(item for item in token_parts if item is not None)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens": total_tokens,
        "total_cost_usd": payload.get("total_cost_usd"),
    }


def _codex_usage(response: str) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for line in response.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") != "turn.completed":
            continue
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage = raw_usage
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cached_input_tokens = _int_or_none(usage.get("cached_input_tokens"))
    reasoning_output_tokens = _int_or_none(usage.get("reasoning_output_tokens"))
    if input_tokens is None and output_tokens is None:
        return {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": (input_tokens or 0) + (output_tokens or 0),
    }


def _model_result_text(backend: str, stdout: str) -> str:
    if backend == "codex":
        result = _codex_result_text(stdout)
        return result if result is not None else stdout
    if backend != "claude":
        return stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, str) else stdout


def _codex_result_text(stdout: str) -> str | None:
    result: str | None = None
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        if payload.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                result = text
    return result


def _actual_usage_source(
    backend: str,
    actual_total_tokens: int | None,
    claude_usage: dict[str, Any],
    codex_usage: dict[str, Any],
) -> str | None:
    if claude_usage.get("total_tokens") is not None:
        return "claude_json_usage"
    if codex_usage.get("total_tokens") is not None:
        return "codex_json_usage"
    if backend == "codex" and actual_total_tokens is not None:
        return "codex_cli_tokens_used"
    return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _feature_goal_from_args(args: argparse.Namespace) -> FeatureGoalContract:
    if args.feature_goal:
        return FeatureGoalContract.model_validate(
            json.loads(Path(args.feature_goal).read_text(encoding="utf-8"))
        )
    return _feature_goal(args.roadmap_id or "r002")


def _feature_goal(roadmap_id: str) -> FeatureGoalContract:
    if roadmap_id == "r003":
        return FeatureGoalContract(
            project="lvc-standard",
            goal=(
                "Implement R-003 range-query API on stores: zero-allocation "
                "store.forEach(fromKey, toKey, visitor), ascendingRange, and descendingRange "
                "for SINGLE and DOUBLE stores."
            ),
            requirements=[
                "Add a zero-allocation StoreVisitor interface.",
                "Implement ascending and descending range scans for SINGLE and DOUBLE stores.",
                (
                    "Bounds semantics must be inclusive and cover empty, single-key, "
                    "full-keyspace, and reverse ranges."
                ),
                (
                    "Optional key-prefix filter must avoid Consumer boxing or allocation "
                    "on the visitor hot path."
                ),
            ],
            constraints=[
                "Alloc gate must stay green on the visitor hot path.",
                "Do not introduce sorted-by-value queries, composite keys, or secondary indexes.",
                "Reuse the preallocated-pool convention from com.ull.lvc.pool where applicable.",
            ],
            acceptance_criteria=[
                "Range tests cover empty range, single-key range, full-keyspace, and reverse scan.",
                "SINGLE and DOUBLE store variants are both covered.",
                "qa/smoke.sh passes and allocation gate stays green.",
            ],
        )
    return FeatureGoalContract(
        project="lvc-standard",
        goal=(
            "Implement scheduled snapshot + atomic restore for SINGLE and DOUBLE stores so "
            "cold-start time collapses from journal replay to mmap'd reload, while preserving "
            "atomic publishChecked semantics on restore."
        ),
        requirements=[
            "Store.snapshot(Path) writes a snapshot with magic+version header and per-page CRC.",
            (
                "Store.restore(Path) atomically swaps in the snapshot and reconciles with "
                "the guaranteed journal cursor."
            ),
            (
                "Restore must not surface staged-but-unjournaled writes until the journal "
                "cursor is reconciled."
            ),
            "SINGLE and DOUBLE store implementations must both support snapshot and restore.",
        ],
        constraints=[
            "Zero allocation on the publish hot path stays invariant.",
            "qa/smoke.sh must still pass the :benchmarks:jmhSmokeCheck alloc gate.",
            "Restore latency under 10ms for a 1M-key snapshot.",
        ],
        acceptance_criteria=[
            "Round-trip integration test: write keys, snapshot, kill JVM, restore, read keys.",
            "Crash-mid-journal test: only journal-acknowledged writes visible after restore.",
            "Alloc gate qa/smoke.sh passes with snapshot enabled.",
            "JMH benchmark restore-latency-1m-keys < 10ms p99.",
            (
                "CRC mismatch on a page during restore aborts restore with structured "
                "invariant failure."
            ),
        ],
    )


def _default_project_root(goal: FeatureGoalContract) -> Path:
    roots = {
        "lvc-standard": Path("/Volumes/devssd/repos/ull/lvc-standard"),
        "trade-research-platform": Path("/Volumes/devssd/repos/apps/trade-research-platform"),
        "dag-framework": Path("/Volumes/devssd/repos/ull/dag_framework"),
    }
    return roots.get(goal.project, Path("."))


def _project_context(
    goal: FeatureGoalContract,
    root: Path,
    budget_tokens: int,
    database_url: str | None,
) -> TokenSaviorContextResult:
    query = " ".join([goal.goal, *goal.requirements, *goal.acceptance_criteria])
    workflow_id = f"eval:{goal.project}:{hashlib.sha256(query.encode()).hexdigest()[:12]}"
    memory_digest = _build_memory_digest(
        project_name=goal.project,
        project_root=root,
        query=query,
        workflow_id=workflow_id,
        database_url=database_url,
        budget_tokens=800,
    )
    git_head = current_git_head(root)
    query_hash = capsule_query_hash(
        query,
        budget_tokens=budget_tokens,
        memory_digest=memory_digest,
    )
    if database_url:
        cached = get_context_capsule(
            project=goal.project,
            git_head=git_head,
            query_hash=query_hash,
            capsule_version=EVAL_CONTEXT_CAPSULE_VERSION,
            database_url=database_url,
        )
        if cached is not None:
            savior = token_savior_from_capsule(cached)
            return savior.model_copy(
                update={
                    "context": savior.context.model_copy(
                        update={
                            "qa_write_paths": _filter_relevant_qa_write_paths(
                                discover_qa_write_paths(root),
                                query=query,
                                relevant_paths=savior.context.relevant_paths,
                            )
                        }
                    )
                }
            )
    savior = build_token_savior_project_context(
        project_root=root,
        query=query,
        budget_tokens=budget_tokens,
        memory_digest=memory_digest,
    )
    savior = savior.model_copy(
        update={
            "context": savior.context.model_copy(
                update={
                    "qa_write_paths": _filter_relevant_qa_write_paths(
                        discover_qa_write_paths(root),
                        query=query,
                        relevant_paths=savior.context.relevant_paths,
                    )
                }
            )
        }
    )
    if database_url:
        upsert_context_capsule(
            capsule_from_token_savior(
                project=goal.project,
                git_head=git_head,
                query_hash=query_hash,
                capsule_version=EVAL_CONTEXT_CAPSULE_VERSION,
                result=savior,
                metadata={"source": "live_eval"},
            ),
            database_url=database_url,
        )
    return savior


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    iterations = payload.get("iterations") if isinstance(payload.get("iterations"), list) else []
    critic: CriticVerdict | None = None
    if iterations:
        raw_critic = iterations[-1].get("critic") if isinstance(iterations[-1], dict) else None
        if isinstance(raw_critic, dict):
            critic = CriticVerdict.model_validate(raw_critic)
    return {
        "backend": payload["backend"],
        "model": payload.get("model"),
        "mechanical_model": payload.get("mechanical_model"),
        "reasoning": payload.get("reasoning"),
        "mechanical_reasoning": payload.get("mechanical_reasoning"),
        "status": payload["status"],
        "accepted_at_iteration": payload.get("accepted_at_iteration"),
        "validation_error_count": len(payload.get("validation_errors") or []),
        "slice_count": len(final.get("task_slices") or []) if isinstance(final, dict) else 0,
        "roles": sorted({item.get("role") for item in final.get("task_slices", [])})
        if isinstance(final, dict)
        else [],
        "critic_verdict": critic.verdict if critic else None,
        "critic_blocking_findings": len(
            [
                finding
                for finding in (critic.findings if critic else [])
                if finding.severity == "blocking"
            ]
        ),
        "token_savior": payload.get("token_savior"),
        "output": str(Path(payload["backend"]) / "outcome.json"),
    }


if __name__ == "__main__":
    sys.exit(main())
