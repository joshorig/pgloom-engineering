from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pgloom_engineering.contracts import PlanContract, validate_plan_contract
from pgloom_engineering.planner.critic import compute_verdict, deterministic_check_results
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.planner.token_savior_context import build_token_savior_project_context


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    savior = build_token_savior_project_context(
        project_root=Path("/Volumes/devssd/repos/ull/lvc-standard"),
        query=(
            "Store.snapshot Store.restore snapshot restore CRC journal cursor "
            "publishChecked SINGLE DOUBLE allocation gate"
        ),
        budget_tokens=3000,
    )
    (output_dir / "token_savior_context.txt").write_text(
        savior.packed_context,
        encoding="utf-8",
    )
    prompt = _prompt(savior.packed_context)
    (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run(
        _command(args.backend),
        input=prompt,
        text=True,
        capture_output=True,
        cwd="/Volumes/devssd/repos/oss/pgloom-engineering",
        timeout=600,
        check=False,
    )
    elapsed_seconds = time.monotonic() - started
    response = completed.stdout + completed.stderr
    (output_dir / "response.txt").write_text(response, encoding="utf-8")
    (output_dir / "model_usage.jsonl").write_text(
        json.dumps(
            _usage_record(
                backend=args.backend,
                elapsed_seconds=elapsed_seconds,
                prompt=prompt,
                response=response,
            ),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        print(response)
        return completed.returncode
    try:
        payload = extract_json(_model_result_text(args.backend, completed.stdout))
        plan = PlanContract.model_validate(payload)
    except Exception as exc:
        result = {"backend": args.backend, "status": "invalid_json_or_contract", "error": str(exc)}
        (output_dir / "outcome.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    validator_errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, validator_errors)
    verdict = compute_verdict(checks, validator_errors)
    result = {
        "backend": args.backend,
        "status": "accepted" if verdict == "accept" and not validator_errors else "needs_revision",
        "token_savior": savior.model_dump(mode="json", exclude={"context", "packed_context"}),
        "validator_errors": validator_errors,
        "critic_verdict": verdict,
        "critic_findings": [
            finding.model_dump(mode="json")
            for check in checks
            for finding in check.findings
            if finding.severity == "blocking"
        ],
        "final": plan.model_dump(mode="json"),
        "summary": _summary(plan, validator_errors, verdict),
    }
    (output_dir / "outcome.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["status"] == "accepted" else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["claude", "codex"], required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def _command(backend: str) -> list[str]:
    if backend == "claude":
        return [
            "claude",
            "-p",
            "--model",
            "sonnet",
            "--output-format",
            "json",
            "--max-budget-usd",
            "3.00",
        ]
    return [
        "codex",
        "exec",
        "-m",
        "gpt-5.4-mini",
        "-s",
        "read-only",
        "-C",
        "/Volumes/devssd/repos/oss/pgloom-engineering",
        "--ephemeral",
        "--json",
        "-",
    ]


def _prompt(packed_context: str) -> str:
    return f"""
Return exactly one JSON object matching the PlanContract schema below. No Markdown.

Project: lvc-standard
Feature id: lvc-r002-live
Goal: Implement scheduled snapshot + atomic restore for SINGLE and DOUBLE stores so
cold-start time collapses from journal replay to mmap'd reload, while preserving
atomic publishChecked semantics on restore.

Requirements:
- Store.snapshot(Path) writes a snapshot with magic+version header and per-page CRC.
- Store.restore(Path) atomically swaps in the snapshot and reconciles with the
  guaranteed journal cursor.
- Restore must not surface staged-but-unjournaled writes until the journal cursor is reconciled.
- SINGLE and DOUBLE store implementations must both support snapshot and restore.
- Zero allocation on the publish hot path stays invariant.
- qa/smoke.sh must still pass the :benchmarks:jmhSmokeCheck alloc gate.
- Restore latency under 10ms for a 1M-key snapshot.

Token Savior packed context:
{packed_context}

PlanContract JSON schema by example:
{{
  "contract_version": "engineering.contracts.v1",
  "feature_id": "lvc-r002-live",
  "project": "lvc-standard",
  "problem_statement": "...",
  "assumptions": ["..."],
  "design_contract": {{
    "public_api": "...",
    "ownership_boundaries": "...",
    "concurrency_protocol": "...",
    "persistence_protocol": "...",
    "hard_constraints": ["..."],
    "forbidden_alternatives": ["..."],
    "acceptance_tests": ["..."]
  }},
  "affected_surfaces": ["store/", "conformance-tests/", "benchmarks/", "qa/"],
  "implementation_topology": "council_decides",
  "task_slices": [
    {{
      "slice_id": "design-snapshot-format",
      "role": "designer",
      "task_type": "engineering.design",
      "objective": "Concrete objective naming files/classes/tests.",
      "allowed_paths": ["store/", "docs/"],
      "forbidden_paths": ["benchmarks/"],
      "depends_on": [],
      "expected_outputs": ["DesignContract"],
      "verification_commands": [["./qa/smoke.sh"]]
    }}
  ],
  "acceptance_test_matrix": [
    "stale or invalid snapshot precondition test",
    "CRC invariant failure test",
    "partial journal failure restore test"
  ],
  "risk_register": ["..."],
  "self_heal_policy": "retry_repair_replan_then_escalate",
  "finalization_policy": "open_final_feature_pr_for_human_merge",
  "council_reports": []
}}

Hard requirements:
- Every task slice has non-empty allowed_paths, forbidden_paths, expected_outputs,
  verification_commands.
- Dependency IDs refer only to earlier slices.
- Include at least one implementer slice for store snapshot/restore, one implementer
  slice mentioning journal cursor reconciliation, one reviewer slice, and one QA slice.
- Acceptance matrix must cover stale/invalid, invariant/CRC, and failure/partial journal cases.
- Use implementation_topology "council_decides" unless you can justify another valid enum.
"""


def _usage_record(
    *,
    backend: str,
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
        "profile_name": "single-model",
        "call_index": 1,
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


def _summary(plan: PlanContract, errors: list[dict[str, Any]], verdict: str) -> dict[str, Any]:
    roles = sorted({item.role for item in plan.task_slices})
    return {
        "project": plan.project,
        "feature_id": plan.feature_id,
        "status": "accepted" if verdict == "accept" and not errors else "needs_revision",
        "critic_verdict": verdict,
        "validation_error_count": len(errors),
        "slice_count": len(plan.task_slices),
        "roles": roles,
        "implementation_topology": plan.implementation_topology.value,
    }


if __name__ == "__main__":
    sys.exit(main())
