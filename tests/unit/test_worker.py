from __future__ import annotations

from typing import Any

from pgloom_engineering import worker
from pgloom_engineering.worker import _record_dependency_handoffs, _requires_handoff


def test_qa_verify_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.verify"})


def test_qa_author_does_not_require_task_result_handoff_gate() -> None:
    assert not _requires_handoff({"task_type": "engineering.qa.author"})


def test_reviewer_requires_producer_handoff() -> None:
    assert _requires_handoff({"task_type": "engineering.review"})


def test_record_dependency_handoffs_targets_dependent_task_contracts(monkeypatch: Any) -> None:
    handoffs: list[dict[str, Any]] = []
    monkeypatch.setattr(
        worker,
        "list_task_contracts",
        lambda *args, **kwargs: [
            {
                "task_id": "review-1",
                "input_contract": {"dependencies": ["impl-1"]},
            },
            {
                "task_id": "qa-verify-1",
                "input_contract": {"dependencies": ["review-1"]},
            },
        ],
    )
    monkeypatch.setattr(
        worker,
        "record_handoff",
        lambda **kwargs: handoffs.append(kwargs) or {},
    )

    _record_dependency_handoffs(
        feature_id="feature-1",
        from_task_id="impl-1",
        handoff_type="task_result",
        contract={"changed_files": ["src/App.java"]},
        database_url=None,
    )

    assert handoffs == [
        {
            "feature_id": "feature-1",
            "from_task_id": "impl-1",
            "to_task_id": "review-1",
            "handoff_type": "task_result",
            "contract": {"changed_files": ["src/App.java"]},
            "database_url": None,
        }
    ]
