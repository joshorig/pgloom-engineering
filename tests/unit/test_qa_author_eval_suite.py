from __future__ import annotations

from scripts.run_qa_author_eval_suite import _run_timeout_seconds


def test_qa_author_suite_timeout_allows_repair_phases() -> None:
    assert _run_timeout_seconds({"defaults": {"timeout_seconds": 900}}, {}) == 3720
    assert (
        _run_timeout_seconds({"defaults": {"timeout_seconds": 900}}, {"timeout_seconds": 120})
        == 600
    )
