from __future__ import annotations

import json
from pathlib import Path

from pgloom_engineering.planner.eval_runtime import (
    DirectProvider,
    command_for_planner_model,
    model_result_text,
    usage_record,
)


def test_planner_eval_runtime_builds_codex_command() -> None:
    assert command_for_planner_model(
        "codex",
        "planner-panelist",
        model="gpt-5.5",
        reasoning="high",
        claude_max_budget_usd="5.00",
        cwd=Path("/tmp/pgloom-engineering"),
    ) == [
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="high"',
        "-s",
        "read-only",
        "-C",
        "/tmp/pgloom-engineering",
        "--ephemeral",
        "--json",
        "-",
    ]


def test_planner_eval_provider_uses_configurable_cwd(tmp_path: Path) -> None:
    provider = DirectProvider(
        backend="codex",
        output_dir=tmp_path,
        model="gpt-5.5",
        reasoning="high",
        mechanical_model=None,
        mechanical_reasoning=None,
        claude_max_budget_usd="5.00",
        cwd=tmp_path,
    )

    assert provider.cwd == tmp_path
    assert provider.timeout_seconds == 600


def test_planner_eval_provider_accepts_invocation_timeout(tmp_path: Path) -> None:
    provider = DirectProvider(
        backend="claude",
        output_dir=tmp_path,
        model="sonnet",
        reasoning="",
        mechanical_model=None,
        mechanical_reasoning=None,
        claude_max_budget_usd="5.00",
        timeout_seconds=1800,
        cwd=tmp_path,
    )

    assert provider.timeout_seconds == 1800


def test_planner_eval_runtime_extracts_codex_usage_and_result_text() -> None:
    response = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "final plan"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 25,
                        "cached_input_tokens": 80,
                        "reasoning_output_tokens": 10,
                    },
                }
            ),
        ]
    )

    assert model_result_text("codex", response) == "final plan"
    usage = usage_record(
        backend="codex",
        profile_name="planner-panelist",
        call_index=1,
        command=["codex"],
        model="gpt-5.5",
        reasoning="high",
        elapsed_seconds=1.25,
        prompt="prompt",
        response=response,
    )
    assert usage["actual_input_tokens"] == 100
    assert usage["actual_output_tokens"] == 25
    assert usage["cache_read_input_tokens"] == 80
    assert usage["reasoning_output_tokens"] == 10
    assert usage["actual_usage_source"] == "codex_json_usage"
