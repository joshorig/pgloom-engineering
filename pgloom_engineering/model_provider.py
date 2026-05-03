from __future__ import annotations

import json
import re
from typing import Any

from pgloom.context import count_tokens
from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pgloom.harness.subprocess import SubprocessResult, run_bounded
from pgloom.models.cli import CLIModelProfile
from pydantic import BaseModel, Field


class EngineeringModelInvocationResult(BaseModel):
    text: str
    parsed: Any = None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    subprocess: SubprocessResult
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_usage_id: int | None = None


class EngineeringCLIModelProvider:
    """CLI model provider that records durable pgloom usage rows and returns their ids."""

    def __init__(self, *, database_url: str | None = None, record_usage: bool = True) -> None:
        self._database_url = database_url
        self._record_usage_enabled = record_usage

    def invoke(
        self,
        *,
        profile: CLIModelProfile,
        prompt: str,
        input_tokens_hint: int | None = None,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> EngineeringModelInvocationResult:
        completed = run_bounded(
            profile.command,
            timeout_seconds=profile.timeout_seconds,
            stdin=prompt.encode("utf-8"),
        )
        parsed, text, usage = _parse_model_output(completed.stdout, profile.parse_response)
        input_tokens = usage.input_tokens or input_tokens_hint or _approx_tokens(prompt)
        output_tokens = usage.output_tokens or _approx_tokens(text)
        cost_usd = usage.cost_usd
        if cost_usd is None:
            cost_usd = (
                input_tokens * profile.cost_per_input_token_usd
                + output_tokens * profile.cost_per_output_token_usd
            )
        metadata = {
            "argv": completed.argv,
            "exit_code": completed.exit_code,
            "timed_out": completed.timed_out,
            "killed": completed.killed,
            "stderr": completed.stderr,
            "token_count_source": usage.source,
            **usage.metadata,
        }
        result = EngineeringModelInvocationResult(
            text=text,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            subprocess=completed,
            metadata=metadata,
        )
        if self._record_usage_enabled:
            result.model_usage_id = self._record_usage(
                result=result,
                profile_name=profile.name,
                workflow_id=workflow_id,
                task_id=task_id,
            )
        return result

    def _record_usage(
        self,
        *,
        result: EngineeringModelInvocationResult,
        profile_name: str,
        workflow_id: str | None,
        task_id: str | None,
    ) -> int:
        with connect(self._database_url) as conn, conn.transaction():
            row = conn.execute(
                """
                insert into model_usage(
                  workflow_id, task_id, profile_name, input_tokens, output_tokens,
                  cost_usd, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    workflow_id,
                    task_id,
                    profile_name,
                    result.input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    jsonb(result.metadata),
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("model usage insert did not return a row")
        return int(row["id"])


class _Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    source: str = "approximate"
    metadata: dict[str, Any] = Field(default_factory=dict)


def _parse_model_output(stdout: str, parse_response: str) -> tuple[Any, str, _Usage]:
    if parse_response == "json" and stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return None, stdout, _parse_codex_text_usage(stdout)
        if isinstance(parsed, dict):
            return parsed, _json_result_text(parsed, stdout), _parse_json_usage(parsed)
        return parsed, stdout, _Usage()
    codex_text = _codex_result_text(stdout)
    if codex_text is not None:
        return None, codex_text, _parse_codex_jsonl_usage(stdout)
    return None, stdout, _parse_codex_text_usage(stdout)


def _json_result_text(parsed: dict[str, Any], fallback: str) -> str:
    result = parsed.get("result")
    if isinstance(result, str):
        return result
    text = parsed.get("text")
    return text if isinstance(text, str) else fallback


def _parse_json_usage(parsed: dict[str, Any]) -> _Usage:
    usage = parsed.get("usage")
    metadata: dict[str, Any] = {}
    if isinstance(parsed.get("modelUsage"), dict):
        metadata["model_usage_by_model"] = parsed["modelUsage"]
    if not isinstance(usage, dict):
        return _Usage(cost_usd=_float_or_none(parsed.get("total_cost_usd")))
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cache_creation = _int_or_none(usage.get("cache_creation_input_tokens"))
    cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
    input_total = sum(
        item for item in [input_tokens, cache_creation, cache_read] if item is not None
    )
    metadata.update(
        {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "service_tier": usage.get("service_tier"),
        }
    )
    return _Usage(
        input_tokens=input_total if input_total else input_tokens,
        output_tokens=output_tokens,
        cost_usd=_float_or_none(parsed.get("total_cost_usd")),
        source="json_usage",
        metadata=metadata,
    )


def _parse_codex_jsonl_usage(stdout: str) -> _Usage:
    usage: dict[str, Any] = {}
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "turn.completed":
            raw_usage = payload.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    if input_tokens is None and output_tokens is None:
        return _Usage()
    return _Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        source="codex_json_usage",
        metadata={
            "cached_input_tokens": _int_or_none(usage.get("cached_input_tokens")),
            "reasoning_output_tokens": _int_or_none(usage.get("reasoning_output_tokens")),
        },
    )


def _parse_codex_text_usage(stdout: str) -> _Usage:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)\s*$", stdout, flags=re.IGNORECASE)
    if not match:
        return _Usage()
    return _Usage(
        input_tokens=int(match.group(1).replace(",", "")),
        output_tokens=0,
        source="codex_cli_tokens_used",
    )


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


def _approx_tokens(text: str) -> int:
    return count_tokens(text)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
