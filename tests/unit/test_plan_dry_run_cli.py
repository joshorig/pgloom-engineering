from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pgloom_engineering.cli import app


def test_plan_dry_run_exits_with_json_when_council_exhausted(tmp_path: Path) -> None:
    goal = tmp_path / "goal.json"
    goal.write_text(
        json.dumps({"project": "demo", "goal": "Do a bounded planning dry run."}),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "dry-run",
            "--feature-goal",
            str(goal),
            "--project-root",
            str(tmp_path),
            "--max-iterations",
            "1",
        ],
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"] == "planner_council_exhausted"
