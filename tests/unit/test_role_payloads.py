from __future__ import annotations

from pgloom_engineering.contracts import QAAuthorContract
from pgloom_engineering.role_payloads import (
    compact_qa_author_payload,
    compact_task_result_payload,
)


def test_compact_qa_author_payload_keeps_actionable_handoff_fields() -> None:
    payload = compact_qa_author_payload(
        QAAuthorContract(
            feature_id="feature-1",
            task_id="qa-1",
            tests_added=["tests/test_feature.py::test_feature"],
            matrix_coverage={"criterion": ["tests/test_feature.py::test_feature"]},
            red_proof=[
                {
                    "test": "tests/test_feature.py::test_feature",
                    "command": ["pytest", "tests/test_feature.py", "-q"],
                    "exit_code": 1,
                    "output_excerpt": "x" * 1200,
                }
            ],
            paths_touched=["tests/test_feature.py"],
            worktree_path="/tmp/worktree",
        )
    )

    assert payload["tests_added"] == ["tests/test_feature.py::test_feature"]
    assert payload["matrix_coverage"] == {"criterion": ["tests/test_feature.py::test_feature"]}
    assert payload["worktree_path"] == "/tmp/worktree"
    assert len(payload["red_proof"][0]["output_excerpt"]) < 700
    assert "diagnostics" not in payload


def test_compact_task_result_payload_trims_command_output_for_review() -> None:
    payload = compact_task_result_payload(
        {
            "contract_version": "engineering.contracts.v1",
            "feature_id": "feature-1",
            "task_id": "impl-1",
            "changed_files": ["src/App.java"],
            "worktree_path": "/tmp/worktree",
            "checks": [
                {
                    "command": ["pytest", "-q"],
                    "exit_code": 1,
                    "status": "failed",
                    "stdout_excerpt": "s" * 1200,
                    "stderr_excerpt": "e" * 1200,
                }
            ],
            "raw_log": "do not pass this through",
        }
    )

    assert payload["changed_files"] == ["src/App.java"]
    assert payload["worktree_path"] == "/tmp/worktree"
    assert len(payload["checks"][0]["stdout_excerpt"]) < 700
    assert len(payload["checks"][0]["stderr_excerpt"]) < 700
    assert "raw_log" not in payload
