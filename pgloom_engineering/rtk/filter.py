from __future__ import annotations

import shutil
from typing import Literal

from pgloom.artifacts import register_artifact
from pgloom.context import count_tokens
from pgloom.harness.subprocess import SubprocessResult, run_bounded
from pydantic import BaseModel, Field

from pgloom_engineering.config import get_settings
from pgloom_engineering.rtk.policy import FilterPolicy, should_filter
from pgloom_engineering.token_savior import TokenSaviorUsage, record_token_savior_usage


class FilteredSubprocessResult(BaseModel):
    original: SubprocessResult
    filtered_stdout: str
    filtered_stderr: str
    filter_method: Literal["rtk", "passthrough", "rtk_unavailable"]
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)
    tokens_saved: int = Field(ge=0)
    reduction_ratio: float = Field(ge=0, le=1)
    artifact_id_unfiltered_stdout: str | None = None
    artifact_id_unfiltered_stderr: str | None = None


def filter_subprocess_result(
    result: SubprocessResult,
    *,
    policy: FilterPolicy | None = None,
    encoder_name: str | None = None,
    record_in: str | None = None,
    feature_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
) -> FilteredSubprocessResult:
    settings = get_settings()
    policy = policy or FilterPolicy(
        enabled=settings.rtk_filter_enabled,
        passthrough_commands=settings.rtk_passthrough_commands,
        passthrough_exit_codes=[0] if settings.rtk_passthrough_on_success else [],
        max_tokens_after=settings.rtk_max_tokens_after,
    )
    encoder = encoder_name or settings.token_count_encoder
    original_text = result.stdout + result.stderr
    tokens_before = count_tokens(original_text, encoder_name=encoder)
    artifact_stdout, artifact_stderr = _register_originals(
        result,
        database_url=record_in,
        workflow_id=workflow_id,
        task_id=task_id,
    )
    if not should_filter(result, policy):
        filtered = _result(
            result,
            stdout=result.stdout,
            stderr=result.stderr,
            method="passthrough",
            tokens_before=tokens_before,
            encoder_name=encoder,
            artifact_stdout=artifact_stdout,
            artifact_stderr=artifact_stderr,
        )
        _record(filtered, record_in, feature_id, workflow_id, task_id, role, result)
        return filtered

    stdout, stderr, method = _run_rtk(result)
    if policy.max_tokens_after is not None:
        stdout = _truncate_to_token_budget(stdout, policy.max_tokens_after, encoder_name=encoder)
        remaining = max(0, policy.max_tokens_after - count_tokens(stdout, encoder_name=encoder))
        stderr = _truncate_to_token_budget(stderr, remaining, encoder_name=encoder)
    filtered = _result(
        result,
        stdout=stdout,
        stderr=stderr,
        method=method,
        tokens_before=tokens_before,
        encoder_name=encoder,
        artifact_stdout=artifact_stdout,
        artifact_stderr=artifact_stderr,
    )
    _record(filtered, record_in, feature_id, workflow_id, task_id, role, result)
    return filtered


def _run_rtk(result: SubprocessResult) -> tuple[str, str, Literal["rtk", "rtk_unavailable"]]:
    if shutil.which("rtk") is None:
        return result.stdout, result.stderr, "rtk_unavailable"
    filter_name = _filter_name_for_command(result.argv)
    stdout = _filter_stream(result.stdout, filter_name=filter_name)
    stderr = _filter_stream(result.stderr, filter_name=filter_name)
    if stdout is None or stderr is None:
        return result.stdout, result.stderr, "rtk_unavailable"
    return stdout, stderr, "rtk"


def _filter_stream(value: str, *, filter_name: str) -> str | None:
    if not value:
        return value
    completed = run_bounded(
        ["rtk", "pipe", "-f", filter_name],
        timeout_seconds=5,
        stdin=value.encode("utf-8"),
    )
    if completed.exit_code != 0 or completed.timed_out or completed.killed:
        return None
    return completed.stdout


def _filter_name_for_command(argv: list[str]) -> str:
    lowered = " ".join(argv).lower()
    if "pytest" in lowered:
        return "pytest"
    if "mypy" in lowered:
        return "mypy"
    if "ruff" in lowered and "format" in lowered:
        return "ruff-format"
    if "ruff" in lowered:
        return "ruff-check"
    if "git diff" in lowered:
        return "git-diff"
    if "git status" in lowered:
        return "git-status"
    if "git log" in lowered:
        return "git-log"
    return "cargo-test"


def _result(
    original: SubprocessResult,
    *,
    stdout: str,
    stderr: str,
    method: Literal["rtk", "passthrough", "rtk_unavailable"],
    tokens_before: int,
    encoder_name: str,
    artifact_stdout: str | None,
    artifact_stderr: str | None,
) -> FilteredSubprocessResult:
    tokens_after = count_tokens(stdout + stderr, encoder_name=encoder_name)
    tokens_saved = max(0, tokens_before - tokens_after)
    return FilteredSubprocessResult(
        original=original,
        filtered_stdout=stdout,
        filtered_stderr=stderr,
        filter_method=method,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        tokens_saved=tokens_saved,
        reduction_ratio=tokens_saved / tokens_before if tokens_before else 0.0,
        artifact_id_unfiltered_stdout=artifact_stdout,
        artifact_id_unfiltered_stderr=artifact_stderr,
    )


def _register_originals(
    result: SubprocessResult,
    *,
    database_url: str | None,
    workflow_id: str | None,
    task_id: str | None,
) -> tuple[str | None, str | None]:
    if workflow_id is None:
        return None, None
    stdout_id = _register_stream(
        result.stdout,
        stream="stdout",
        result=result,
        database_url=database_url,
        workflow_id=workflow_id,
        task_id=task_id,
    )
    stderr_id = _register_stream(
        result.stderr,
        stream="stderr",
        result=result,
        database_url=database_url,
        workflow_id=workflow_id,
        task_id=task_id,
    )
    return stdout_id, stderr_id


def _register_stream(
    value: str,
    *,
    stream: str,
    result: SubprocessResult,
    database_url: str | None,
    workflow_id: str,
    task_id: str | None,
) -> str | None:
    if not value:
        return None
    try:
        row = register_artifact(
            workflow_id=workflow_id,
            task_id=task_id,
            artifact_type=f"subprocess-{stream}",
            content=value.encode("utf-8", errors="replace"),
            metadata={"argv": result.argv, "exit_code": result.exit_code, "stream": stream},
            database_url=database_url,
        )
    except Exception:
        return None
    return str(row["id"]) if row.get("id") is not None else None


def _record(
    filtered: FilteredSubprocessResult,
    database_url: str | None,
    feature_id: str | None,
    workflow_id: str | None,
    task_id: str | None,
    role: str | None,
    result: SubprocessResult,
) -> None:
    if feature_id is None:
        return
    command = result.argv[0] if result.argv else ""
    try:
        record_token_savior_usage(
            TokenSaviorUsage(
                feature_id=feature_id,
                workflow_id=workflow_id,
                task_id=task_id,
                profile_name=role,
                input_tokens_original=filtered.tokens_before,
                input_tokens_after_savior=filtered.tokens_after,
                tokens_saved=filtered.tokens_saved,
                reduction_ratio=filtered.reduction_ratio,
                metadata={
                    "method": filtered.filter_method if filtered.filter_method != "rtk" else "rtk",
                    "role": role,
                    "command": command,
                    "artifact_id_unfiltered_stdout": filtered.artifact_id_unfiltered_stdout,
                    "artifact_id_unfiltered_stderr": filtered.artifact_id_unfiltered_stderr,
                },
            ),
            database_url=database_url,
        )
    except Exception:
        return


def _truncate_to_token_budget(text: str, budget: int, *, encoder_name: str) -> str:
    if budget <= 0 or not text:
        return ""
    if count_tokens(text, encoder_name=encoder_name) <= budget:
        return text
    approx_chars = max(1, budget * 4)
    truncated = text[:approx_chars]
    while count_tokens(truncated, encoder_name=encoder_name) > budget and truncated:
        truncated = truncated[: max(0, len(truncated) - 128)]
    return truncated
