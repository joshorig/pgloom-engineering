from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pgloom.db.postgres import connect
from pgloom.models.cli import CLIModelProfile

from pgloom_engineering import model_provider
from pgloom_engineering.model_provider import EngineeringCLIModelProvider


def test_engineering_provider_records_claude_json_usage(
    database_url: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "claude_json.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps({",
                "  'result': '{\"ok\": true}',",
                "  'total_cost_usd': 0.123456,",
                "  'usage': {",
                "    'input_tokens': 10,",
                "    'cache_creation_input_tokens': 20,",
                "    'cache_read_input_tokens': 30,",
                "    'output_tokens': 40,",
                "    'service_tier': 'standard'",
                "  },",
                "  'modelUsage': {'claude-sonnet': {'costUSD': 0.123456}}",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    result = EngineeringCLIModelProvider(database_url=database_url).invoke(
        profile=CLIModelProfile(
            name="planner-panelist",
            command=[sys.executable, str(script)],
            parse_response="json",
        ),
        prompt="hello",
    )

    assert result.text == '{"ok": true}'
    assert result.input_tokens == 60
    assert result.output_tokens == 40
    assert result.cost_usd == 0.123456
    assert result.model_usage_id is not None
    row = _usage_row(database_url, result.model_usage_id)
    assert row["input_tokens"] == 60
    assert row["output_tokens"] == 40
    assert float(row["cost_usd"]) == 0.123456
    assert row["metadata"]["token_count_source"] == "json_usage"
    assert row["metadata"]["cache_read_input_tokens"] == 30


def test_engineering_provider_unwraps_claude_json_for_text_profiles(
    database_url: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "claude_json_text.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps({",
                "  'result': '{\"contract_version\": \"engineering.contracts.v1\"}',",
                "  'usage': {'input_tokens': 11, 'output_tokens': 22}",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    result = EngineeringCLIModelProvider(database_url=database_url).invoke(
        profile=CLIModelProfile(
            name="planner-panelist",
            command=[sys.executable, str(script)],
            parse_response="text",
        ),
        prompt="hello",
    )

    assert result.text == '{"contract_version": "engineering.contracts.v1"}'
    assert result.input_tokens == 11
    assert result.output_tokens == 22
    assert result.model_usage_id is not None
    row = _usage_row(database_url, result.model_usage_id)
    assert row["metadata"]["token_count_source"] == "json_usage"


def test_engineering_provider_records_codex_jsonl_usage(
    database_url: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "codex_jsonl.py"
    events = [
        {"type": "thread.started", "thread_id": "abc"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"ok": true}'},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 90,
                "output_tokens": 12,
                "reasoning_output_tokens": 3,
            },
        },
    ]
    script.write_text(
        "\n".join(
            [
                "import json",
                f"events = {json.dumps(events)!r}",
                "for event in json.loads(events):",
                "    print(json.dumps(event))",
            ]
        ),
        encoding="utf-8",
    )

    result = EngineeringCLIModelProvider(database_url=database_url).invoke(
        profile=CLIModelProfile(
            name="planner-critic",
            command=[sys.executable, str(script)],
            parse_response="text",
        ),
        prompt="hello",
    )

    assert result.text == '{"ok": true}'
    assert result.input_tokens == 100
    assert result.output_tokens == 12
    assert result.model_usage_id is not None
    row = _usage_row(database_url, result.model_usage_id)
    assert row["metadata"]["token_count_source"] == "codex_json_usage"
    assert row["metadata"]["cached_input_tokens"] == 90
    assert row["metadata"]["reasoning_output_tokens"] == 3
    assert float(row["cost_usd"]) == pytest.approx(0.000455)
    assert row["metadata"]["prompt_estimated_tokens"] > 0
    assert row["metadata"]["prompt_bytes"] == len(b"hello")
    assert row["metadata"]["stdout_bytes"] > 0
    assert row["metadata"]["duration_seconds"] >= 0


def test_engineering_provider_reprices_zero_codex_cost(
    database_url: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "codex_json_zero_cost.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "print(json.dumps({",
                "  'result': '{\"ok\": true}',",
                "  'total_cost_usd': 0,",
                "  'usage': {",
                "    'input_tokens': 10,",
                "    'cache_read_input_tokens': 90,",
                "    'output_tokens': 12",
                "  }",
                "}))",
            ]
        ),
        encoding="utf-8",
    )

    result = EngineeringCLIModelProvider(database_url=database_url).invoke(
        profile=CLIModelProfile(
            name="implementer",
            command=[sys.executable, str(script), "codex"],
            parse_response="json",
        ),
        prompt="hello",
    )

    assert result.input_tokens == 100
    assert result.output_tokens == 12
    assert result.cost_usd == pytest.approx(0.000455)
    assert result.model_usage_id is not None
    row = _usage_row(database_url, result.model_usage_id)
    assert float(row["cost_usd"]) == pytest.approx(0.000455)


def test_codex_commands_disable_approval_prompts() -> None:
    command = model_provider._codex_no_approval_command(  # noqa: SLF001
        [
            "codex",
            "exec",
            "-m",
            "gpt-5.5",
            "-s",
            "danger-full-access",
            "--json",
            "-",
        ]
    )

    assert "--ask-for-approval" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert command.index("--dangerously-bypass-approvals-and-sandbox") > command.index(
        "exec"
    )


def test_codex_commands_replace_unsupported_exec_approval_policy() -> None:
    command = model_provider._codex_no_approval_command(  # noqa: SLF001
        [
            "codex",
            "exec",
            "--ask-for-approval",
            "never",
            "-m",
            "gpt-5.5",
        ]
    )

    assert command == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m",
        "gpt-5.5",
    ]


def _usage_row(database_url: str, usage_id: int) -> dict[str, Any]:
    with connect(database_url) as conn:
        row = conn.execute("select * from model_usage where id = %s", (usage_id,)).fetchone()
    assert row is not None
    return dict(row)
