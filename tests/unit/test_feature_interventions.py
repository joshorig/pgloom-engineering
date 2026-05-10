from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from pgloom_engineering import cli
from pgloom_engineering.cli import app


def test_feature_intervention_commands_record_audited_actions(monkeypatch: Any) -> None:
    recorded: list[dict[str, Any]] = []

    def fake_record(**kwargs: Any) -> dict[str, Any]:
        recorded.append(kwargs)
        return {"id": len(recorded), **kwargs}

    monkeypatch.setattr(cli, "record_operator_intervention", fake_record)

    result = CliRunner().invoke(
        app,
        [
            "feature",
            "replan-from-milestone",
            "feature-1",
            "--milestone-id",
            "m2",
            "--actor",
            "alice",
            "--reason",
            "validator finding",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert recorded == [
        {
            "feature_id": "feature-1",
            "actor": "alice",
            "action_type": "replan_from_milestone",
            "payload": {"milestone_id": "m2", "reason": "validator finding"},
            "database_url": None,
        }
    ]
