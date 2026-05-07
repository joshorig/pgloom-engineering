from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pgloom_engineering import workflow_driver


def test_run_workflow_drives_planner_then_qa_until_done(monkeypatch: Any) -> None:
    aggregates = [
        _aggregate([{"id": "planner-1", "slot": "planner", "state": "queued"}]),
        _aggregate(
            [
                {"id": "planner-1", "slot": "planner", "state": "done"},
                {"id": "qa-1", "slot": "qa-engineer", "state": "queued"},
            ]
        ),
        _aggregate(
            [
                {"id": "planner-1", "slot": "planner", "state": "done"},
                {"id": "qa-1", "slot": "qa-engineer", "state": "done"},
            ]
        ),
    ]
    claimed_slots: list[str] = []
    feature_states: list[str] = []

    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: aggregates.pop(0),
    )
    monkeypatch.setattr(
        workflow_driver,
        "update_feature_state",
        lambda _feature_id, *, state, database_url=None, **kwargs: feature_states.append(state),
    )

    def worker(**kwargs: Any) -> dict[str, object]:
        claimed_slots.append(str(kwargs["slot"]))
        return {"claimed": True, "task_id": f"{kwargs['slot']}-task", "status": "done"}

    result = workflow_driver.run_workflow("feature-1", worker=worker)

    assert result["status"] == "done"
    assert claimed_slots == ["planner", "qa-engineer"]
    assert feature_states == ["ready_for_finalization"]
    assert len(result["steps"]) == 2


def test_run_workflow_stops_on_blocked_task(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: _aggregate(
                [
                    {"id": "planner-1", "slot": "planner", "state": "done"},
                    {
                        "id": "qa-1",
                        "slot": "qa-engineer",
                        "state": "blocked",
                        "blocker_code": "engineering.qa_failed",
                    },
                ]
            ),
        )
    states: list[str] = []
    monkeypatch.setattr(
        workflow_driver,
        "update_feature_state",
        lambda _feature_id, *, state, database_url=None, **kwargs: states.append(state),
    )

    result = workflow_driver.run_workflow("feature-1")

    assert result == {
        "status": "blocked",
        "feature_id": "feature-1",
        "blocked_task_ids": ["qa-1"],
        "steps": [],
    }
    assert states == ["blocked"]


def test_run_workflow_ignores_dependency_waiting_blocked_tasks(monkeypatch: Any) -> None:
    aggregates = [
        _aggregate(
            [
                {"id": "design-1", "slot": "designer", "state": "queued"},
                {"id": "qa-1", "slot": "qa-engineer", "state": "blocked"},
            ]
        ),
        _aggregate(
            [
                {"id": "design-1", "slot": "designer", "state": "done"},
                {"id": "qa-1", "slot": "qa-engineer", "state": "done"},
            ]
        ),
    ]
    claimed_slots: list[str] = []
    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: aggregates.pop(0),
    )
    monkeypatch.setattr(
        workflow_driver,
        "update_feature_state",
        lambda *args, **kwargs: None,
    )

    def worker(**kwargs: Any) -> dict[str, object]:
        claimed_slots.append(str(kwargs["slot"]))
        return {"claimed": True, "task_id": "design-1", "status": "done"}

    result = workflow_driver.run_workflow("feature-1", worker=worker)

    assert result["status"] == "done"
    assert claimed_slots == ["designer"]


def test_run_workflow_reports_stalled_when_no_slot_claims(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: _aggregate(
            [{"id": "impl-1", "slot": "implementer", "state": "queued"}]
        ),
    )
    monkeypatch.setattr(
        workflow_driver,
        "update_feature_state",
        lambda *args, **kwargs: None,
    )

    result = workflow_driver.run_workflow(
        "feature-1",
        worker=lambda **kwargs: {"claimed": False},
    )

    assert result["status"] == "stalled"
    assert result["active_task_ids"] == ["impl-1"]
    assert result["steps"] == [{"slot": "implementer", "claimed": False}]


def test_blocked_replan_waits_until_retry_budget(monkeypatch: Any) -> None:
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 2,
                    "blocker_code": "engineering.qa_semantic_quality_failed",
                }
            ]
        ),
        None,
    )

    assert result is None


def test_blocked_replan_enqueues_planner_with_failure_knowledge(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    attached: list[tuple[str, str, str]] = []
    transitioned: list[tuple[str, str]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(
        workflow_driver,
        "attach_task",
        lambda feature_id, task_id, *, role, database_url=None: attached.append(
            (feature_id, task_id, role)
        )
        or {},
    )
    monkeypatch.setattr(
        workflow_driver,
        "transition_task",
        lambda task_id, to_state, **kwargs: transitioned.append((task_id, to_state.value)) or {},
    )
    monkeypatch.setattr(
        workflow_driver,
        "record_recovery_action",
        lambda decision, **kwargs: recovered.append(decision.model_dump()) or {},
    )

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "planner-1",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "done",
                },
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 3,
                    "priority": 4,
                    "blocker_code": "engineering.qa_semantic_quality_failed",
                    "blocker_reason": (
                        "direct Spring controller call in CryptoDomainControllersTest"
                    ),
                },
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "queued",
                },
            ]
        ),
        "postgres://unit",
    )

    assert result == {
        "slot": "planner",
        "claimed": True,
        "status": "replan",
        "task_id": "planner-replan-1",
        "replanned_from_task_id": "qa-1",
    }
    payload = enqueued[0]["payload"]
    assert enqueued[0]["priority"] == 5
    assert payload["requires_multi_agent_council"] is True
    assert payload["replan_context"]["blocker_code"] == "engineering.qa_semantic_quality_failed"
    assert "MockMvc/WebTestClient/TestRestTemplate" in payload["replan_context"]["summary"]
    assert any(
        "direct Spring controller call" in item
        for item in payload["feature_goal_contract"]["requirements"]
    )
    assert attached == [("feature-1", "planner-replan-1", "planner")]
    assert transitioned == [("qa-1", "abandoned"), ("impl-1", "abandoned")]
    assert recovered[0]["action"] == "replan"


def test_blocked_replan_skips_when_planner_is_already_active(monkeypatch: Any) -> None:
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "planner-2",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "queued",
                },
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 4,
                    "blocker_code": "engineering.qa_tests_not_red",
                },
            ]
        ),
        None,
    )

    assert result is None


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow_replan_after_blocked_attempts=3,
        workflow_replan_after_input_tokens=750_000,
        workflow_replan_blocker_codes=[
            "engineering.qa_semantic_quality_failed",
            "engineering.qa_tests_not_red",
        ],
    )


def _aggregate(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "feature": {
            "id": "feature-1",
            "state": "open",
            "metadata": {
                "feature_goal_contract": {
                    "project": "trade-research-platform",
                    "goal": "Add small feature",
                    "requirements": [],
                    "constraints": [],
                    "acceptance_criteria": ["behavior is covered"],
                },
                "project": {"name": "trade-research-platform"},
            },
        },
        "tasks": tasks,
        "agent_topology": {"planning": "multi_agent"},
    }
