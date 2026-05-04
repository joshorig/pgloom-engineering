from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pgloom.models.cli import CLIModelProfile


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
        cwd: Path | None = None,
    ) -> None:
        self.backend = backend
        self.output_dir = output_dir
        self.model = model
        self.reasoning = reasoning
        self.mechanical_model = mechanical_model
        self.mechanical_reasoning = mechanical_reasoning
        self.claude_max_budget_usd = claude_max_budget_usd
        self.cwd = cwd or Path.cwd()
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
        command = command_for_planner_model(
            self.backend,
            profile.name,
            model=self._model_for_profile(profile.name),
            reasoning=self._reasoning_for_profile(profile.name),
            claude_max_budget_usd=self.claude_max_budget_usd,
            cwd=self.cwd,
        )
        started = time.monotonic()
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=self.cwd,
            timeout=600,
            check=False,
        )
        elapsed_seconds = time.monotonic() - started
        raw_response = completed.stdout + completed.stderr
        response_path.write_text(raw_response, encoding="utf-8")
        usage = usage_record(
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
        result_text = model_result_text(self.backend, completed.stdout)
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


def command_for_planner_model(
    backend: str,
    profile_name: str,
    *,
    model: str,
    reasoning: str,
    claude_max_budget_usd: str,
    cwd: Path | None = None,
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
        f'model_reasoning_effort="{profile_reasoning}"',
        "-s",
        "read-only",
        "-C",
        str(cwd or Path.cwd()),
        "--ephemeral",
        "--json",
        "-",
    ]


def usage_record(
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
    actual_tokens = actual_total_tokens(response)
    parsed_claude_usage = claude_usage(response) if backend == "claude" else {}
    parsed_codex_usage = codex_usage(response) if backend == "codex" else {}
    if parsed_claude_usage.get("total_tokens") is not None:
        actual_tokens = int(parsed_claude_usage["total_tokens"])
    if parsed_codex_usage.get("total_tokens") is not None:
        actual_tokens = int(parsed_codex_usage["total_tokens"])
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
        "estimated_input_tokens": estimate_tokens(prompt),
        "estimated_output_tokens": estimate_tokens(response),
        "estimated_total_tokens": estimate_tokens(prompt) + estimate_tokens(response),
        "actual_total_tokens": actual_tokens,
        "actual_input_tokens": parsed_claude_usage.get("input_tokens")
        or parsed_codex_usage.get("input_tokens"),
        "actual_output_tokens": (
            parsed_claude_usage.get("output_tokens") or parsed_codex_usage.get("output_tokens")
        ),
        "cache_creation_input_tokens": parsed_claude_usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": parsed_claude_usage.get("cache_read_input_tokens")
        or parsed_codex_usage.get("cached_input_tokens"),
        "reasoning_output_tokens": parsed_codex_usage.get("reasoning_output_tokens"),
        "total_cost_usd": parsed_claude_usage.get("total_cost_usd"),
        "actual_usage_source": actual_usage_source(
            backend, actual_tokens, parsed_claude_usage, parsed_codex_usage
        ),
    }


def actual_total_tokens(response: str) -> int | None:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)\s*$", response, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def claude_usage(response: str) -> dict[str, Any]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    input_tokens = int_or_none(usage.get("input_tokens"))
    output_tokens = int_or_none(usage.get("output_tokens"))
    cache_creation = int_or_none(usage.get("cache_creation_input_tokens"))
    cache_read = int_or_none(usage.get("cache_read_input_tokens"))
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


def codex_usage(response: str) -> dict[str, Any]:
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
    input_tokens = int_or_none(usage.get("input_tokens"))
    output_tokens = int_or_none(usage.get("output_tokens"))
    cached_input_tokens = int_or_none(usage.get("cached_input_tokens"))
    reasoning_output_tokens = int_or_none(usage.get("reasoning_output_tokens"))
    if input_tokens is None and output_tokens is None:
        return {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": (input_tokens or 0) + (output_tokens or 0),
    }


def model_result_text(backend: str, stdout: str) -> str:
    if backend == "codex":
        result = codex_result_text(stdout)
        return result if result is not None else stdout
    if backend != "claude":
        return stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, str) else stdout


def codex_result_text(stdout: str) -> str | None:
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


def actual_usage_source(
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


def int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0
