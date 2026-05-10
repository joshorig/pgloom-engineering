from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pgloom_engineering.live_role_eval import run_live_role_eval


def test_live_role_eval_orchestration_uses_worker_runtime(
    database_url: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    script = tmp_path / "fake_role_model.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "worktree = Path(sys.argv[1])",
                "prompt = json.loads(sys.stdin.read())",
                "role = prompt.get('role')",
                "if role == 'implementation_engineer':",
                "    worktree.joinpath('src/calc.py').write_text("
                "\"def increment(value: int) -> int:\\n    return value + 1\\n\", "
                "encoding='utf-8')",
                "    print(json.dumps({'TaskResultContract': {",
                "      'feature_id': prompt['task_contract']['feature_id'],",
                "      'task_id': prompt['task_contract']['inputs']['task_id'],",
                "      'changed_files': ['src/calc.py'],",
                "      'checks': []",
                "    }}))",
                "elif role == 'production_reviewer':",
                "    print(json.dumps({'ReviewVerdictContract': {",
                "      'feature_id': prompt['task_contract']['feature_id'],",
                "      'task_id': prompt['task_contract']['inputs']['task_id'],",
                "      'panel': ['fake-live-reviewer'],",
                "      'verdict': 'approve',",
                "      'rationale': 'fixture implementation is scoped',",
                "      'findings': []",
                "    }}))",
                "elif role == 'qa.usertest':",
                "    print(json.dumps({'QAResultContract': {",
                "      'feature_id': prompt['task_contract']['feature_id'],",
                "      'task_id': prompt['task_contract']['inputs']['task_id'],",
                "      'verdict': 'pass',",
                "      'validator_type': 'usertest',",
                "      'commands': [[",
                "        'python', '-c',",
                "        'from src.calc import increment; assert increment(1) == 2'",
                "      ]],",
                "      'commands_run': [{",
                "        'cmd': [",
                "          'python', '-c',",
                "          'from src.calc import increment; assert increment(1) == 2'",
                "        ],",
                "        'exit_code': 0,",
                "        'duration_s': 0.01,",
                "        'artifact_ids': []",
                "      }],",
                "      'validation_evidence': [{",
                "        'evidence_id': 'fixture-usertest',",
                "        'kind': 'integration_check',",
                "        'summary': 'Consumer-style command verified increment(1) == 2.',",
                "        'verdict': 'pass',",
                "        'command_run_ids': [],",
                "        'artifact_ids': [],",
                "        'metadata': {'surface': 'library-cli'}",
                "      }],",
                "      'evidence': ['consumer-style command verified increment behavior'],",
                "      'findings': [],",
                "      'procedures_attestation': {'user_facing_flow_exercised': True}",
                "    }}))",
                "else:",
                "    print('{}')",
            ]
        ),
        encoding="utf-8",
    )

    def fake_role_commands(**kwargs: Any) -> dict[str, list[str]]:
        del kwargs
        return {
            "planner": [sys.executable, str(script), "{worktree}"],
            "implementer": [sys.executable, str(script), "{worktree}"],
            "reviewer": [sys.executable, str(script), "{worktree}"],
            "qa_author": [sys.executable, str(script), "{worktree}"],
        }

    monkeypatch.setattr("pgloom_engineering.live_role_eval._role_commands", fake_role_commands)

    result = run_live_role_eval(
        {"id": "fixture-orchestration", "role": "worker-orchestration"},
        role="worker-orchestration",
        output_dir=tmp_path / "out",
        database_url=database_url,
        max_steps=10,
    )

    assert result.status == "pass"
    assert {item["status"] for item in result.worker_results} == {"done"}
    assert result.aggregate is not None
    task_types = {
        row["metadata"]["task_type"]
        for row in result.aggregate["worker_runs"]
        if row["status"] == "done"
    }
    assert {
        "engineering.implement",
        "engineering.review",
        "engineering.qa.verify.scrutiny",
        "engineering.qa.verify.usertest",
    }.issubset(task_types)
    assert result.aggregate["model_usage"]["summary"]["input_tokens"] > 0
    assert result.aggregate["token_savior"]["summary"]["input_tokens_original"] > 0
    assert any(
        int(row["rtk_raw_log_tokens"]) > 0
        for row in result.aggregate["worker_runs"]
        if row["metadata"]["task_type"] in {
            "engineering.implement",
            "engineering.qa.verify.scrutiny",
        }
    )
    assert (tmp_path / "out" / "outcome.json").is_file()
    assert (tmp_path / "out" / "worktree.diff").is_file()
    assert (tmp_path / "out" / "file-snapshots.json").is_file()
    assert (tmp_path / "out" / "artifacts.json").is_file()
    assert "return value + 1" in (tmp_path / "out" / "worktree.diff").read_text(
        encoding="utf-8"
    )
