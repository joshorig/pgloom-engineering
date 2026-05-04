from __future__ import annotations

from pgloom_engineering.worker import _requires_handoff


def test_qa_verify_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.verify"})


def test_qa_author_still_requires_producer_handoff() -> None:
    assert _requires_handoff({"task_type": "engineering.qa.author"})
