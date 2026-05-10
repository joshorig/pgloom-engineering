from __future__ import annotations

from typing import Any

from pgloom_engineering import autonomy_eval
from pgloom_engineering.autonomy_eval import (
    AUTONOMY_STEPS,
    assert_autonomy_eval_covered,
    run_autonomy_eval,
)


def test_autonomy_eval_uses_live_worker_runtime(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []
    task_ids = [f"task-{index}" for index, _ in enumerate(AUTONOMY_STEPS)]
    tasks = {
        task_id: {"id": task_id, "task_type": task_type, "slot": f"slot-{index}", "state": "done"}
        for index, (task_id, task_type) in enumerate(zip(task_ids, AUTONOMY_STEPS, strict=True))
    }

    def fake_run_once(**kwargs: Any) -> dict[str, object]:
        slot = str(kwargs["slot"])
        worker_id = str(kwargs["worker_id"])
        calls.append((slot, worker_id))
        if len(calls) > len(task_ids):
            return {"claimed": False}
        return {"claimed": True, "task_id": task_ids[len(calls) - 1], "status": "done"}

    monkeypatch.setattr(autonomy_eval, "run_once", fake_run_once)
    monkeypatch.setattr(autonomy_eval, "build_registry", lambda: object())
    monkeypatch.setattr(
        autonomy_eval,
        "get_task",
        lambda task_id, **kwargs: tasks.get(task_id),
    )
    monkeypatch.setattr(
        autonomy_eval,
        "get_feature_aggregate",
        lambda feature_id, **kwargs: {
            "feature": {"id": feature_id},
            "worker_runs": [{"id": index} for index, _ in enumerate(AUTONOMY_STEPS)],
            "handoffs": [{"id": "handoff-1"}],
        },
    )

    report = run_autonomy_eval("feature-1", slots=["live-slot"], max_steps=10)

    assert [slot for slot, _ in calls] == ["live-slot"] * (len(AUTONOMY_STEPS) + 1)
    assert report.executed_task_types == AUTONOMY_STEPS
    assert_autonomy_eval_covered(report)
