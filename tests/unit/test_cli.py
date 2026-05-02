from __future__ import annotations

from typer.testing import CliRunner

from pgloom_engineering.cli import app


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Engineering orchestrator" in result.output


def test_pgloom_cli_is_wrapped() -> None:
    result = CliRunner().invoke(app, ["pgloom", "--help"])
    assert result.exit_code == 0
    assert "Postgres-backed reusable orchestration runtime" in result.output
