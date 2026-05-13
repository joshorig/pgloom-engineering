from __future__ import annotations

# mypy: disable-error-code="func-returns-value"
from types import SimpleNamespace
from typing import Any

from pgloom_engineering import workflow_driver
from pgloom_engineering.config import EngineeringSettings
from pgloom_engineering.contracts import (
    DesignContract,
    MilestoneContract,
    PlanContract,
    TaskSliceContract,
)


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
        assert kwargs["feature_id"] == "feature-1"
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


def test_run_workflow_ignores_recovery_abandoned_tasks_when_done(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: _aggregate(
            [
                {
                    "id": "old-impl",
                    "slot": "implementer",
                    "state": "abandoned",
                    "terminal_reason": "workflow_recovery_replan",
                },
                {
                    "id": "old-stale-worker",
                    "slot": "qa-engineer",
                    "state": "abandoned",
                    "terminal_reason": "stale_live_eval_worker",
                },
                {
                    "id": "new-impl",
                    "slot": "implementer",
                    "state": "done",
                },
                {
                    "id": "new-review",
                    "slot": "reviewer",
                    "state": "done",
                },
                {
                    "id": "new-scrutiny",
                    "slot": "qa-scrutiny",
                    "state": "done",
                },
                {
                    "id": "new-usertest",
                    "slot": "qa-usertest",
                    "state": "done",
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

    assert result == {"status": "done", "feature_id": "feature-1", "steps": []}
    assert states == ["ready_for_finalization"]


def test_run_workflow_still_fails_unexplained_abandoned_tasks(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        workflow_driver,
        "get_feature_aggregate",
        lambda *args, **kwargs: _aggregate(
            [
                {"id": "old-impl", "slot": "implementer", "state": "abandoned"},
                {"id": "new-impl", "slot": "implementer", "state": "done"},
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
        "status": "failed",
        "feature_id": "feature-1",
        "task_ids": ["old-impl"],
        "steps": [],
    }
    assert states == ["failed"]


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


def test_worker_crash_is_default_recoverable_blocker() -> None:
    settings = EngineeringSettings()

    assert "engineering.worker_crash" in settings.workflow_replan_blocker_codes
    assert "engineering.worker_crash" in settings.workflow_replan_immediate_blocker_codes


def test_path_violation_replans_immediately(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 2,
                    "blocker_code": "engineering.qa_path_violation",
                    "blocker_reason": "benchmarks/src/jmh/java outside allowed paths",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.qa_path_violation"
    assert "path boundary" in payload["replan_context"]["summary"]


def test_worker_crash_replans_immediately_with_crash_context(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    settings = _settings()
    settings.workflow_replan_immediate_blocker_codes.append("engineering.worker_crash")
    settings.workflow_replan_blocker_codes.append("engineering.worker_crash")
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: settings)
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 3,
                    "priority": 2,
                    "blocker_code": "engineering.worker_crash",
                    "blocker_reason": "worker crash: malformed command metadata",
                    "result": {
                        "worker_crash": {
                            "exception_type": "ValueError",
                            "message": "worker crash: malformed command metadata",
                            "task_type": "engineering.qa.author",
                            "attempt": 3,
                            "max_attempts": 3,
                        }
                    },
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.worker_crash"
    assert "QA-author repair" in payload["replan_context"]["summary"]
    assert "worker_crash=" in payload["replan_context"]["failure_context"]


def test_initial_worker_crash_requeues_same_role_repair(monkeypatch: Any) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
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
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.worker_crash",
                    "blocker_reason": "worker crashed during model output parsing",
                    "result": {
                        "worker_crash": {
                            "exception_type": "ValueError",
                            "message": "bad command metadata",
                            "task_type": "engineering.qa.author",
                            "attempt": 1,
                        }
                    },
                }
            ]
        ),
        "postgres://unit",
    )

    assert result == {
        "slot": "qa-engineer",
        "claimed": True,
        "status": "repair_task",
        "task_id": "qa-1",
        "task_type": "engineering.qa.author",
    }
    assert updates[0][0] == "queued"
    assert updates[0][1] == 2
    assert updates[0][3] == "qa-1"
    repair_context = updates[0][2]["same_role_repair_context"]
    assert repair_context["blocker_code"] == "engineering.worker_crash"
    assert "bad command metadata" in repair_context["failure_context"]
    assert recovered[0]["action"] == "repair_task"


def test_corrective_planner_crash_requeues_original_same_role_repair(
    monkeypatch: Any,
) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    abandoned: list[tuple[list[str], str | None]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
    monkeypatch.setattr(
        workflow_driver,
        "record_recovery_action",
        lambda decision, **kwargs: recovered.append(decision.model_dump()) or {},
    )
    monkeypatch.setattr(
        workflow_driver,
        "_abandon_nonterminal_tasks",
        lambda _aggregate, *, exclude_task_ids, database_url: abandoned.append(
            (sorted(exclude_task_ids), database_url)
        ),
    )

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "planner-repair-1",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 4,
                    "blocker_code": "engineering.worker_crash",
                    "blocker_reason": "corrective planner lease expired",
                    "payload": {
                        "replan_context": {
                            "blocked_task_id": "impl-1",
                            "blocked_task_type": "engineering.implement",
                            "blocked_slice_id": "impl",
                            "blocker_code": "engineering.implementer_contract_invalid",
                            "blocker_reason": "missing task_id",
                            "failure_context": "TaskResultContract missing task_id",
                            "summary": "Repair invalid implementer result.",
                            "blocked_slice_allowed_paths": ["src/"],
                        }
                    },
                },
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "abandoned",
                    "attempt": 1,
                    "priority": 3,
                },
            ]
        ),
        "postgres://unit",
    )

    assert result == {
        "slot": "implementer",
        "claimed": True,
        "status": "repair_task",
        "task_id": "impl-1",
        "task_type": "engineering.implement",
    }
    assert updates[0][0] == "queued"
    assert updates[0][1] == 2
    assert updates[0][3] == "impl-1"
    repair_context = updates[0][2]["same_role_repair_context"]
    assert repair_context["source"] == "interrupted_corrective_planner"
    assert repair_context["blocker_code"] == "engineering.implementer_contract_invalid"
    assert repair_context["allowed_paths"] == ["src/"]
    assert "missing task_id" in repair_context["failure_context"]
    assert recovered[0]["action"] == "repair_task"
    assert abandoned == [(["impl-1"], "postgres://unit")]


def test_corrective_planner_crash_routes_original_review_rejection_to_owner(
    monkeypatch: Any,
) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
    monkeypatch.setattr(workflow_driver, "_abandon_nonterminal_tasks", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "done",
                    "attempt": 1,
                },
                {
                    "id": "planner-repair-1",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 4,
                    "blocker_code": "engineering.worker_crash",
                    "blocker_reason": "corrective planner lease expired",
                    "payload": {
                        "replan_context": {
                            "blocked_task_id": "review-1",
                            "blocked_task_type": "engineering.review",
                            "blocker_code": "engineering.review_rejected",
                            "blocker_reason": (
                                "store/src/main implementation changed close lifecycle"
                            ),
                            "failure_context": "reviewer requested coder_repair",
                        }
                    },
                },
            ]
        ),
        "postgres://unit",
    )

    assert result is not None
    assert result["task_id"] == "impl-1"
    assert result["task_type"] == "engineering.implement"
    repair_context = updates[0][2]["same_role_repair_context"]
    assert repair_context["blocker_code"] == "engineering.review_rejected"
    assert repair_context["original_blocked_task_id"] == "review-1"


def test_review_rejection_replans_immediately(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.review_rejected",
                    "blocker_reason": "stable callback view required",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.review_rejected"
    assert "narrow corrective implementation slice" in payload["replan_context"]["summary"]


def test_review_rejection_routes_production_finding_to_implementer_repair(
    monkeypatch: Any,
) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
    monkeypatch.setattr(workflow_driver, "_abandon_nonterminal_tasks", lambda *args, **kwargs: {})
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
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "done",
                    "attempt": 1,
                },
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.review_rejected",
                    "blocker_reason": (
                        "coder_repair: store/src/main implementation changed close "
                        "lifecycle and must be repaired"
                    ),
                },
            ]
        ),
        "postgres://unit",
    )

    assert result == {
        "slot": "implementer",
        "claimed": True,
        "status": "repair_task",
        "task_id": "impl-1",
        "task_type": "engineering.implement",
    }
    repair_context = updates[0][2]["same_role_repair_context"]
    assert repair_context["source"] == "review_rejected"
    assert repair_context["recommended_owner"] == "implementer"
    assert "close lifecycle" in repair_context["failure_context"]
    assert recovered[0]["action"] == "repair_task"


def test_qa_verify_failure_replans_immediately(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "qa-verify-1",
                    "slot": "qa-scrutiny",
                    "task_type": "engineering.qa.verify.scrutiny",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.qa_verify_failed",
                    "blocker_reason": "feature-specific benchmark smoke failed",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.qa_verify_failed"
    assert "feature-specific tests" in payload["replan_context"]["summary"]


def test_contract_invalid_blockers_replan_immediately(monkeypatch: Any) -> None:
    for blocker_code, summary_text in [
        ("engineering.implementer_contract_invalid", "TaskResultContract"),
        ("engineering.qa_usertest_contract_invalid", "QAResultContract"),
        ("engineering.invalid_handler_output", "could not be persisted"),
    ]:
        enqueued: list[dict[str, Any]] = []
        recovered: list[dict[str, Any]] = []
        monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
        monkeypatch.setattr(
            workflow_driver,
            "enqueue_task",
            lambda _enqueued=enqueued, **kwargs: _enqueued.append(kwargs)
            or {"id": "planner-replan-1"},
        )
        monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
        monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
        monkeypatch.setattr(
            workflow_driver,
            "record_recovery_action",
            lambda decision, _recovered=recovered, **kwargs: _recovered.append(
                {**decision.model_dump(), "status": kwargs.get("status")}
            )
            or {},
        )

        result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
            "feature-1",
            _aggregate(
                [
                    {
                        "id": f"{blocker_code}-task",
                        "slot": "qa-usertest",
                        "task_type": "engineering.qa.verify.usertest",
                        "state": "blocked",
                        "attempt": 1,
                        "priority": 3,
                        "blocker_code": blocker_code,
                        "blocker_reason": "schema validation failed",
                    }
                ]
            ),
            None,
        )

        assert result is not None
        payload = enqueued[0]["payload"]
        assert payload["replan_context"]["blocker_code"] == blocker_code
        assert summary_text in payload["replan_context"]["summary"]
        assert recovered[0]["action"] == "corrective_slice"
        assert recovered[0]["status"] == "completed"


def test_review_rejected_benchmark_finding_requests_qa_author_repair(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.review_rejected",
                    "blocker_reason": (
                        "benchmarks/src/jmh/java/CiSmokeBenchmark.java does not call "
                        "LvcStore.ascendingRange; benchmark-smoke misses StoreVisitor API"
                    ),
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.review_rejected"
    summary = payload["replan_context"]["summary"]
    assert "QA-owned benchmark/test harness" in summary
    assert "Do not emit an implementation slice" in summary


def test_review_rejected_conformance_test_finding_requests_qa_author_repair(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.review_rejected",
                    "blocker_reason": (
                        "Reviewer verdict was coder_repair: conformance test wiring "
                        "is incomplete. Findings: "
                        "conformance-tests/src/test/java/RangeScanConformanceTest.java "
                        "must add prefix matching/non-matching assertions for direct "
                        "SINGLE, mmap SINGLE, direct DOUBLE, mmap DOUBLE."
                    ),
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    summary = payload["replan_context"]["summary"]
    assert "QA-owned benchmark/test harness" in summary
    assert "Do not emit an implementation slice" in summary
    assert "conformance-tests/src/test/java/RangeScanConformanceTest.java" in summary


def test_review_rejected_mixed_api_and_test_finding_requests_implementation_repair(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.review_rejected",
                    "blocker_reason": (
                        "core/src/main/java/com/example/LvcStore.java is missing the "
                        "required public API byte[] keyPrefix overload, and "
                        "core/src/test/java/com/example/RangeScanApiTest.java does not "
                        "assert that overload."
                    ),
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    summary = payload["replan_context"]["summary"]
    assert "mixed production API/implementation defects" in summary
    assert "narrow implementation repair slice" in summary
    assert "Do not treat public API or store implementation defects as QA-only" in summary


def test_qa_handoff_missing_replans_with_qa_dependency(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.qa_handoff_missing",
                    "blocker_reason": "implementer requires a QA author worktree handoff",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.qa_handoff_missing"
    assert "QA author handoff" in payload["replan_context"]["summary"]


def test_generic_handoff_missing_replans_missing_producer(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "review-1",
                    "slot": "reviewer",
                    "task_type": "engineering.review",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.handoff_missing",
                    "blocker_reason": "review task missing task_result handoff",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.handoff_missing"
    assert "missing upstream producer slice" in payload["replan_context"]["summary"]


def test_blocked_planner_replans_when_council_exhausted(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        workflow_driver,
        "record_recovery_action",
        lambda decision, **kwargs: recovered.append(
            {**decision.model_dump(), "status": kwargs.get("status")}
        )
        or {},
    )

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "planner-1",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.planner_council_exhausted",
                    "blocker_reason": "planner council exhausted",
                    "result": {
                        "failure_excerpt": "critic rejected benchmark gate wiring"
                    },
                }
            ]
        ),
        None,
    )

    assert result is not None
    assert result["status"] == "corrective_slice"
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.planner_council_exhausted"
    assert "invalid proposals" in payload["replan_context"]["summary"]
    assert recovered[0]["action"] == "corrective_slice"
    assert recovered[0]["status"] == "completed"


def test_plan_contract_invalid_replans_immediately(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "planner-1",
                    "slot": "planner",
                    "task_type": "engineering.plan",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.plan_contract_invalid",
                    "blocker_reason": "missing task_slices",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["blocker_code"] == "engineering.plan_contract_invalid"
    assert "PlanContract or post-normalization production-grade validation" in payload[
        "replan_context"
    ]["summary"]


def test_plan_contract_invalid_variant_gate_replan_is_narrow() -> None:
    summary = workflow_driver._replan_summary(  # noqa: SLF001
        {
            "blocker_code": "engineering.plan_contract_invalid",
            "blocker_reason": "normalized plan failed production-grade validation",
            "result": {
                "errors": [
                    {
                        "code": "variant_slice_uses_broad_conformance_gate",
                        "slice_id": "impl-single",
                        "message": "Variant-scoped implementer slice uses a broad gate.",
                    }
                ]
            },
        }
    )

    assert "repair only the invalid verification shape" in summary
    assert "merge the variant implementation work" in summary
    assert "method/class filters" in summary


def test_plan_contract_invalid_hot_path_surface_replan_names_paths() -> None:
    summary = workflow_driver._replan_summary(  # noqa: SLF001
        {
            "blocker_code": "engineering.plan_contract_invalid",
            "blocker_reason": "normalized plan failed production-grade validation",
            "result": {
                "errors": [
                    {
                        "code": "hot_path_implementation_surface_missing",
                        "message": (
                            "Hot-path shared API plan omits implementation paths: "
                            "core/src/main/java/example/metrics/InstrumentedStore.java"
                        ),
                    }
                ]
            },
        }
    )

    assert "add the exact source paths named" in summary
    assert "without dropping sibling concrete implementations" in summary
    assert "core/src/main/java/example/metrics/InstrumentedStore.java" in summary


def test_implementation_reported_blockers_replans_immediately(monkeypatch: Any) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.implementation_reported_blockers",
                    "blocker_reason": "cannot repair QA benchmark wiring",
                }
            ]
        ),
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert (
        payload["replan_context"]["blocker_code"]
        == "engineering.implementation_reported_blockers"
    )
    assert "reported blockers" in payload["replan_context"]["summary"]


def test_implementation_path_violation_with_artifacts_requeues_same_role_repair(
    monkeypatch: Any,
) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
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
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 3,
                    "blocker_code": "engineering.implementation_path_violation",
                    "blocker_reason": "implementer touched paths outside its contract",
                    "result": {
                        "changed_files": [
                            "benchmarks/src/jmh/java/RangeScanSmokeBenchmark.java"
                        ],
                        "violations": [
                            {
                                "path": "benchmarks/src/jmh/java/RangeScanSmokeBenchmark.java",
                                "reason": "forbidden_path",
                            }
                        ],
                    },
                }
            ]
        ),
        None,
    )

    assert result == {
        "slot": "implementer",
        "claimed": True,
        "status": "repair_task",
        "task_id": "impl-1",
        "task_type": "engineering.implement",
    }
    repair_payload = updates[0][2]
    assert repair_payload["preserve_worktree_on_retry"] is True
    repair_context = repair_payload["same_role_repair_context"]
    assert repair_context["blocker_code"] == "engineering.implementation_path_violation"
    assert "benchmarks/src/jmh/java/RangeScanSmokeBenchmark.java" in (
        repair_context["failure_context"]
    )
    assert recovered[0]["action"] == "repair_task"


def test_repeated_same_blocker_recovery_records_incremented_attempt(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    decisions: list[Any] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        workflow_driver,
        "record_recovery_action",
        lambda decision, **kwargs: decisions.append(decision) or {},
    )
    aggregate = _aggregate(
        [
            {
                "id": "qa-1",
                "slot": "qa-engineer",
                "task_type": "engineering.qa.author",
                "state": "blocked",
                "attempt": 1,
                "priority": 3,
                "blocker_code": "engineering.qa_tests_do_not_compile",
                "blocker_reason": "checkstyle failure in QA-authored test",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "qa-1",
            "blocker_code": "engineering.qa_tests_do_not_compile",
            "action": "corrective_slice",
            "status": "completed",
        },
        {
            "task_id": "qa-1",
            "blocker_code": "engineering.qa_tests_do_not_compile",
            "action": "corrective_slice",
            "status": "completed",
        },
    ]

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        aggregate,
        None,
    )

    assert result is not None
    assert enqueued[0]["payload"]["replan_context"]["attempt"] == 3
    assert decisions[0].attempt == 3


def test_repeated_same_blocker_recovery_stops_after_attempt_cap(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    aggregate = _aggregate(
        [
            {
                "id": "qa-1",
                "slot": "qa-engineer",
                "task_type": "engineering.qa.author",
                "state": "blocked",
                "attempt": 1,
                "priority": 3,
                "blocker_code": "engineering.qa_tests_do_not_compile",
                "blocker_reason": "checkstyle failure in QA-authored test",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "blocker_code": "engineering.qa_tests_do_not_compile",
            "action": "corrective_slice",
            "status": "completed",
        },
        {
            "blocker_code": "engineering.qa_tests_do_not_compile",
            "action": "corrective_slice",
            "status": "completed",
        },
        {
            "blocker_code": "engineering.qa_tests_do_not_compile",
            "action": "corrective_slice",
            "status": "completed",
        },
    ]

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        aggregate,
        None,
    )

    assert result is None


def test_repeated_qa_semantic_failure_escalates_replan_instruction(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})
    aggregate = _aggregate(
        [
            {
                "id": "qa-1",
                "slot": "qa-engineer",
                "task_type": "engineering.qa.author",
                "state": "blocked",
                "attempt": 1,
                "priority": 3,
                "blocker_code": "engineering.qa_semantic_quality_failed",
                "blocker_reason": "qa_semantic_jmh_reflective_invocation",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "qa-1",
            "blocker_code": "engineering.qa_semantic_quality_failed",
            "action": "corrective_slice",
            "status": "completed",
        },
        {
            "task_id": "qa-1",
            "blocker_code": "engineering.qa_semantic_quality_failed",
            "action": "corrective_slice",
            "status": "completed",
        },
    ]
    aggregate["model_usage"] = {"by_profile": [{"input_tokens": 750_000}]}

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        aggregate,
        None,
    )

    assert result is not None
    payload = enqueued[0]["payload"]
    assert payload["replan_context"]["same_blocker_recovery_count"] == 2
    assert "must not emit another broad QA-author slice" in payload["replan_context"]["summary"]
    assert "reflection/proxy/adapter shortcuts" in payload["replan_context"]["summary"]


def test_qa_semantic_failure_with_artifacts_requeues_same_role_repair(
    monkeypatch: Any,
) -> None:
    updates: list[tuple[str, int, dict[str, Any], str]] = []
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(workflow_driver, "jsonb", lambda value: value)

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def transaction(self) -> _Conn:
            return self

        def execute(self, _sql: str, params: tuple[Any, ...]) -> None:
            updates.append((str(params[0]), int(params[1]), params[2], str(params[3])))

    monkeypatch.setattr(workflow_driver, "connect", lambda _url: _Conn())
    monkeypatch.setattr(
        workflow_driver,
        "record_recovery_action",
        lambda decision, **kwargs: recovered.append(decision.model_dump()) or {},
    )
    aggregate = _aggregate(
        [
            {
                "id": "qa-1",
                "slot": "qa-engineer",
                "task_type": "engineering.qa.author",
                "state": "blocked",
                "attempt": 1,
                "priority": 3,
                "blocker_code": "engineering.qa_semantic_quality_failed",
                "blocker_reason": "semantic review failed",
                "result": {
                    "changed_files": ["tests/example_test.py"],
                    "findings": [
                        {
                            "code": "qa_semantic_observation_only",
                            "file": "tests/example_test.py",
                            "line": 12,
                            "message": "assert behavior instead of only observing it",
                        }
                    ],
                },
            }
        ]
    )

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        aggregate,
        "postgres://unit",
    )

    assert result == {
        "slot": "qa-engineer",
        "claimed": True,
        "status": "repair_task",
        "task_id": "qa-1",
        "task_type": "engineering.qa.author",
    }
    assert updates[0][0] == "queued"
    assert updates[0][1] == 2
    assert updates[0][3] == "qa-1"
    repair_payload = updates[0][2]
    assert repair_payload["preserve_worktree_on_retry"] is True
    repair_context = repair_payload["same_role_repair_context"]
    assert repair_context["mode"] == "same_role_repair"
    assert repair_context["changed_files"] == ["tests/example_test.py"]
    assert "qa_semantic_observation_only" in repair_context["failure_context"]
    assert recovered[0]["action"] == "repair_task"


def test_replan_recovery_budget_ignores_abandoned_sibling_blockers(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow_driver, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        workflow_driver,
        "enqueue_task",
        lambda **kwargs: enqueued.append(kwargs) or {"id": "planner-replan-1"},
    )
    monkeypatch.setattr(workflow_driver, "attach_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "transition_task", lambda *args, **kwargs: {})
    monkeypatch.setattr(workflow_driver, "record_recovery_action", lambda *args, **kwargs: {})
    aggregate = _aggregate(
        [
            {
                "id": "qa-fresh",
                "slot": "qa-engineer",
                "task_type": "engineering.qa.author",
                "state": "blocked",
                "attempt": 1,
                "priority": 3,
                "blocker_code": "engineering.qa_semantic_quality_failed",
                "blocker_reason": "fresh deterministic QA quality finding",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": f"qa-old-{index}",
            "blocker_code": "engineering.qa_semantic_quality_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
        for index in range(4)
    ]
    aggregate["model_usage"] = {"by_profile": [{"input_tokens": 750_000}]}

    result = workflow_driver._maybe_replan_blocked_feature(  # noqa: SLF001
        "feature-1",
        aggregate,
        None,
    )

    assert result is not None
    assert enqueued[0]["payload"]["replan_context"]["same_blocker_recovery_count"] == 0


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
        "status": "corrective_slice",
        "task_id": "planner-replan-1",
        "replanned_from_task_id": "qa-1",
    }
    payload = enqueued[0]["payload"]
    assert enqueued[0]["priority"] == 5
    assert payload["requires_multi_agent_council"] is True
    assert payload["replan_context"]["mode"] == "corrective_slice"
    assert payload["replan_context"]["max_new_slices"] == 3
    assert payload["replan_context"]["blocker_code"] == "engineering.qa_semantic_quality_failed"
    assert "project-approved public API or user-facing harnesses" in payload[
        "replan_context"
    ]["summary"]
    assert "MockMvc/WebTestClient/TestRestTemplate" not in payload[
        "replan_context"
    ]["summary"]
    assert any(
        "direct Spring controller call" in item
        for item in payload["feature_goal_contract"]["requirements"]
    )
    assert attached == [("feature-1", "planner-replan-1", "planner")]
    assert transitioned == [("qa-1", "abandoned"), ("impl-1", "abandoned")]
    assert recovered[0]["action"] == "corrective_slice"


def test_replan_payload_carries_qa_semantic_findings_without_http_assumption() -> None:
    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "qa-1",
                    "slot": "qa-engineer",
                    "task_type": "engineering.qa.author",
                    "state": "blocked",
                    "attempt": 1,
                    "priority": 4,
                    "blocker_code": "engineering.qa_semantic_quality_failed",
                    "blocker_reason": (
                        "qa.author output failed deterministic semantic quality review"
                    ),
                    "result": {
                        "findings": [
                            {
                                "code": "qa_semantic_range_prefix_behavior_missing",
                                "file": "RangeScanConsumerJourneyTest.java",
                                "line": 29,
                                "message": (
                                    "Range-scan QA must include public-API behavior "
                                    "assertions for matching and non-matching prefix scans."
                                ),
                            }
                        ]
                    },
                }
            ]
        ),
        {
            "id": "qa-1",
            "slot": "qa-engineer",
            "task_type": "engineering.qa.author",
            "state": "blocked",
            "attempt": 1,
            "priority": 4,
            "blocker_code": "engineering.qa_semantic_quality_failed",
            "blocker_reason": "qa.author output failed deterministic semantic quality review",
            "result": {
                "findings": [
                    {
                        "code": "qa_semantic_range_prefix_behavior_missing",
                        "file": "RangeScanConsumerJourneyTest.java",
                        "line": 29,
                        "message": (
                            "Range-scan QA must include public-API behavior assertions "
                            "for matching and non-matching prefix scans."
                        ),
                    }
                ]
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert "qa_semantic_range_prefix_behavior_missing" in context["failure_context"]
    assert "RangeScanConsumerJourneyTest.java:29" in context["failure_context"]
    assert "MockMvc" not in context["summary"]
    assert "public API or user-facing harnesses" in context["summary"]


def test_replan_payload_carries_active_plan_contract_id() -> None:
    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        {
            **_aggregate(
                [
                    {
                        "id": "qa-verify-1",
                        "slot": "qa-scrutiny",
                        "task_type": "engineering.qa.verify.scrutiny",
                        "state": "blocked",
                        "attempt": 1,
                        "blocker_code": "engineering.qa_verify_failed",
                    }
                ]
            ),
            "active_plan_contract": {"id": "plan-active-1", "active": True},
        },
        {
            "id": "qa-verify-1",
            "attempt": 1,
            "blocker_code": "engineering.qa_verify_failed",
            "blocker_reason": "bare ./gradlew check failed",
        },
    )

    assert payload is not None
    assert payload["replan_context"]["active_plan_contract_id"] == "plan-active-1"


def test_replan_payload_carries_blocked_task_path_scope() -> None:
    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        {
            **_aggregate(
                [
                    {
                        "id": "impl-1",
                        "slot": "implementer",
                        "task_type": "engineering.implement",
                        "state": "blocked",
                        "attempt": 1,
                        "blocker_code": "engineering.implementer_contract_invalid",
                    }
                ]
            ),
            "task_contracts": [
                {
                    "task_id": "impl-1",
                    "input_contract": {
                        "task_type": "engineering.implement",
                        "allowed_paths": [
                            "store/src/main/java/com/example/RangeStore.java"
                        ],
                        "forbidden_paths": ["core/src/main/java/"],
                        "inputs": {"task_slice_id": "impl-range"},
                    },
                }
            ],
        },
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementer_contract_invalid",
            "blocker_reason": "handler output failed schema validation",
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert context["blocked_slice_allowed_paths"] == [
        "store/src/main/java/com/example/RangeStore.java"
    ]
    assert context["blocked_slice_forbidden_paths"] == ["core/src/main/java/"]
    assert context["blocked_slice_id"] == "impl-range"
    assert context["blocked_task_type"] == "engineering.implement"


def test_replan_payload_carries_implementation_failure_evidence() -> None:
    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "attempt": 1,
                    "blocker_code": "engineering.implementation_verification_failed",
                }
            ]
        ),
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": "implementer verification commands failed",
            "result": {
                "stderr_excerpt": (
                    "Missing smoke benchmark result for RangeScanVisitorBenchmark"
                ),
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    assert payload["replan_context"]["failure_context"].startswith("Missing smoke")
    assert "production-code behavior or QA-owned" in payload["replan_context"]["summary"]
    assert "QA-authored test classes/methods" in payload["replan_context"]["summary"]
    assert "Do not invent replacement test classes" in payload["replan_context"]["summary"]
    assert "RangeScanVisitorBenchmark" in payload["feature_goal_contract"]["requirements"][-1]


def test_replan_payload_routes_material_benchmark_gate_to_implementer() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "impl-1",
                "slot": "implementer",
                "task_type": "engineering.implement",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.implementation_verification_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "impl-1",
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": (
                "benchmark_smoke_diagnostic: rangeScanSmoke allocated 0.031 B/op "
                "above threshold during :benchmarks:jmhSmokeCheck"
            ),
            "result": {
                "stderr_excerpt": "Allocation threshold exceeded: 0.031 B/op > 0.005 B/op",
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert context["same_blocker_recovery_count"] == 1
    assert context["benchmark_gate_classification"] == "material_allocation"
    assert context["benchmark_allocation_diagnosis"]["classification"] == (
        "material_allocation"
    )
    assert context["benchmark_allocation_diagnosis"]["threshold_b_op"] == 0.005
    assert context["benchmark_allocation_diagnosis"]["recommended_owner"] == "implementer"
    assert context["benchmark_allocation_diagnosis"]["diagnostic_required"] is False
    assert context["benchmark_allocation_diagnosis"]["source_allocation_known"] is False
    assert "Repeated implementer verification failure" in context["summary"]
    assert "material benchmark-smoke allocation failure" in context["summary"]
    assert "exactly one narrow implementation slice" in context["summary"]
    assert "benchmark_allocation_diagnosis" in context["summary"]
    assert "rangeScanSmoke allocated" in payload["feature_goal_contract"]["requirements"][-1]


def test_replan_payload_classifies_material_benchmark_gate_as_implementation_work() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "impl-1",
                "slot": "implementer",
                "task_type": "engineering.implement",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.implementation_verification_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "impl-1",
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": (
                "benchmark_smoke_diagnostic: RangeScanBenchmark.ascendingScan "
                "allocated 0.031 B/op above 0.005 B/op threshold; "
                "source-level allocation uses ByteBuffer.allocate in the range loop"
            ),
            "result": {
                "stderr_excerpt": "Allocation threshold exceeded: 0.031 B/op > 0.005 B/op",
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert context["benchmark_gate_classification"] == "material_allocation"
    diagnosis = context["benchmark_allocation_diagnosis"]
    assert diagnosis["classification"] == "material_allocation"
    assert diagnosis["recommended_owner"] == "implementer"
    assert diagnosis["diagnostic_required"] is False
    assert diagnosis["source_allocation_known"] is True
    assert diagnosis["failing_benchmarks"] == [
        {
            "benchmark": "RangeScanBenchmark.ascendingScan",
            "b_op": 0.031,
            "threshold_b_op": 0.005,
            "severity": "near_threshold",
        }
    ]
    assert "material benchmark-smoke allocation failure" in context["summary"]
    assert "exactly one narrow implementation slice" in context["summary"]
    assert "do not emit a QA-author repair slice" in context["summary"]


def test_replan_payload_keeps_repeated_material_allocations_with_implementer() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "impl-1",
                "slot": "implementer",
                "task_type": "engineering.implement",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.implementation_verification_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "impl-1",
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        },
        {
            "task_id": "impl-1",
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        },
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": (
                "benchmark_smoke_diagnostic: RangeScanBenchmark.ascendingRange "
                "allocated 4.174 B/op, above threshold 0.005 B/op | "
                "RangeScanBenchmark.prefixAscendingRange allocated 0.719 B/op, "
                "above threshold 0.005 B/op"
            ),
            "result": {
                "stderr_excerpt": "JMH smoke GC gate failed",
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    diagnosis = context["benchmark_allocation_diagnosis"]
    assert diagnosis["classification"] == "material_allocation"
    assert diagnosis["diagnostic_required"] is False
    assert diagnosis["recommended_owner"] == "implementer"
    assert diagnosis["source_allocation_known"] is False
    assert diagnosis["max_b_op"] == 4.174
    assert diagnosis["failing_benchmarks"][0]["benchmark"] == (
        "RangeScanBenchmark.ascendingRange"
    )
    assert "diagnostic QA-scrutiny/performance slice" in context["summary"]
    assert "Allocation diagnosis:" in context["summary"]


def test_replan_payload_carries_qa_allocation_diagnostic_to_planner() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "qa-scrutiny-1",
                "slot": "qa-scrutiny",
                "task_type": "engineering.qa.verify.scrutiny",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.qa_verify_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "blocker_code": "engineering.qa_verify_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "qa-scrutiny-1",
            "attempt": 1,
            "blocker_code": "engineering.qa_verify_failed",
            "blocker_reason": "qa.verify.scrutiny allocation diagnostic failed",
            "result": {
                "qa_result_contract": {
                    "verdict": "fail",
                    "validator_type": "scrutiny",
                    "findings": [
                        (
                            "RangeScanBenchmark.ascendingRange allocated 4.174 B/op "
                            "above 0.005 B/op"
                        )
                    ],
                    "validation_evidence": [
                        {
                            "summary": (
                                "RangeScanBenchmark.ascendingRange allocated "
                                "4.174 B/op above 0.005 B/op"
                            ),
                            "metadata": {
                                "benchmark_allocation_diagnosis": {
                                    "classification": "material_allocation",
                                    "diagnostic_required": True,
                                    "suspected_source": (
                                        "store/src/main/java/.../DoubleMmapStore.java"
                                    ),
                                }
                            },
                        }
                    ],
                }
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert "qa_result_contract" in context["failure_context"]
    assert "suspected_source" in context["failure_context"]
    assert context["benchmark_gate_classification"] == "material_allocation"
    assert context["benchmark_allocation_diagnosis"]["classification"] == (
        "material_allocation"
    )


def test_replan_payload_classifies_benchmark_harness_error_as_qa_harness() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "impl-1",
                "slot": "implementer",
                "task_type": "engineering.implement",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.implementation_verification_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "task_id": "impl-1",
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": (
                "benchmark_smoke_diagnostic: missing smoke benchmark result for "
                "RangeScanBenchmark.mmapScan"
            ),
            "result": {
                "stderr_excerpt": "Missing smoke benchmark result for RangeScanBenchmark.mmapScan",
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert context["benchmark_gate_classification"] == "qa_harness"
    assert context["benchmark_allocation_diagnosis"]["recommended_owner"] == "qa-author"
    assert "qa-harness benchmark-smoke gate" in context["summary"]
    assert "QA-owned benchmark" in context["summary"]


def test_replan_payload_classifies_no_matching_jmh_as_qa_harness() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "qa-scrutiny-1",
                "slot": "qa-scrutiny",
                "task_type": "engineering.qa.verify.scrutiny",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.qa_verify_failed",
            }
        ]
    )

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "qa-scrutiny-1",
            "attempt": 1,
            "blocker_code": "engineering.qa_verify_failed",
            "blocker_reason": (
                "qa.verify command failed: ./gradlew :benchmarks:jmhSmokeCheck "
                "exited 1: No matching benchmarks. Miss-spelled regexp?"
            ),
            "result": {
                "qa_result_contract": {
                    "verdict": "fail",
                    "validator_type": "scrutiny",
                    "findings": [
                        (
                            "qa.verify command failed: ./gradlew "
                            ":benchmarks:jmhSmokeCheck exited 1: "
                            "No matching benchmarks. Miss-spelled regexp?"
                        )
                    ],
                },
            },
        },
    )

    assert payload is not None
    context = payload["replan_context"]
    assert context["benchmark_gate_classification"] == "qa_harness"
    assert context["benchmark_allocation_diagnosis"]["recommended_owner"] == "qa-author"
    assert "No matching benchmarks" in context["failure_context"]
    assert "QA-test repair" in context["summary"]


def test_replan_payload_directs_implementation_no_matching_jmh_to_qa_discovery_repair() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "impl-1",
                "slot": "implementer",
                "task_type": "engineering.implement",
                "state": "blocked",
                "attempt": 1,
                "blocker_code": "engineering.implementation_verification_failed",
            }
        ]
    )
    aggregate["recovery_actions"] = [
        {
            "blocker_code": "engineering.implementation_verification_failed",
            "action": "corrective_slice",
            "status": "completed",
        }
    ]

    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        aggregate,
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": (
                "implementer verification commands failed: ./gradlew "
                ":benchmarks:jmhSmokeCheck exited 1"
            ),
            "result": {
                "stderr_excerpt": "No matching benchmarks. Miss-spelled regexp?",
                "commands": [["./gradlew", ":benchmarks:jmhSmokeCheck"]],
            },
        },
    )

    assert payload is not None
    diagnosis = payload["replan_context"]["benchmark_allocation_diagnosis"]
    assert payload["replan_context"]["benchmark_gate_classification"] == "qa_harness"
    assert diagnosis["recommended_owner"] == "qa-author"
    assert "benchmark include regex" in diagnosis["repair_directive"]
    assert "actually discovered and run" in diagnosis["repair_directive"]


def test_replan_payload_carries_implementation_artifact_hints() -> None:
    payload = workflow_driver._replan_payload(  # noqa: SLF001
        "feature-1",
        _aggregate(
            [
                {
                    "id": "impl-1",
                    "slot": "implementer",
                    "task_type": "engineering.implement",
                    "state": "blocked",
                    "attempt": 1,
                    "blocker_code": "engineering.implementation_verification_failed",
                }
            ]
        ),
        {
            "id": "impl-1",
            "attempt": 1,
            "blocker_code": "engineering.implementation_verification_failed",
            "blocker_reason": "implementer verification commands failed",
            "result": {
                "stdout_excerpt": "BUILD FAILED",
                "artifact_hints": {
                    "gradle_test_failures": [
                        {
                            "test": "com.example.RangeScanApiTest.visitsInclusiveRange",
                            "message": "expected:<3> but was:<2>",
                        }
                    ],
                    "failure_output_lines": [
                        "RangeScanApiTest > visitsInclusiveRange FAILED"
                    ],
                },
                "commands": [["./gradlew", ":core:test"]],
            },
        },
    )

    assert payload is not None
    failure_context = payload["replan_context"]["failure_context"]
    assert "gradle_test_failures" in failure_context
    assert "RangeScanApiTest.visitsInclusiveRange" in failure_context
    assert "expected:<3> but was:<2>" in failure_context


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


def test_operator_replan_from_milestone_enqueues_planner_and_supersedes_suffix(
    monkeypatch: Any,
) -> None:
    enqueued: list[dict[str, Any]] = []
    attached: list[tuple[str, str, str]] = []
    superseded: list[str] = []
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
        "_supersede_tasks_for_operator_replan",
        lambda task_ids, *, database_url: superseded.extend(task_ids),
    )
    aggregate = _aggregate(
        [
            {"id": "design-task", "slot": "designer", "state": "done"},
            {"id": "qa-task", "slot": "qa-engineer", "state": "queued", "priority": 2},
            {"id": "impl-task", "slot": "implementer", "state": "queued", "priority": 3},
        ]
    )
    plan = _milestone_plan()
    aggregate["active_plan_contract"] = {"id": "plan-old", "contract": plan.model_dump(mode="json")}
    aggregate["task_contracts"] = [
        {"task_id": "design-task", "task_slice_id": "design", "milestone_id": "m1"},
        {"task_id": "qa-task", "task_slice_id": "qa-author", "milestone_id": "m2"},
        {"task_id": "impl-task", "task_slice_id": "impl", "milestone_id": "m2"},
    ]
    aggregate["operator_interventions"] = [
        {
            "id": 12,
            "action_type": "replan_from_milestone",
            "payload": {"milestone_id": "m2", "reason": "validator found a gap"},
        }
    ]

    result = workflow_driver._maybe_consume_replan_from_milestone(  # noqa: SLF001
        "feature-1",
        aggregate,
        "postgres://unit",
    )

    assert result is not None
    assert result["status"] == "replan_from_milestone"
    assert superseded == ["qa-task", "impl-task"]
    assert attached == [("feature-1", "planner-replan-1", "planner")]
    payload = enqueued[0]["payload"]
    assert payload["baseline_plan"]["feature_id"] == "feature-1"
    assert payload["replan_from_milestone_id"] == "m2"
    assert payload["frozen_prefix_task_ids"] == ["design-task"]
    assert payload["replan_context"]["frozen_prefix_slice_ids"] == ["design"]
    assert payload["replan_context"]["replanned_slice_ids"] == ["qa-author", "impl"]
    assert enqueued[0]["priority"] == 4


def test_operator_replan_from_milestone_skips_consumed_intervention() -> None:
    aggregate = _aggregate(
        [
            {
                "id": "planner-replan-1",
                "slot": "planner",
                "task_type": "engineering.plan",
                "state": "done",
                "payload": {"operator_intervention_id": "12"},
            }
        ]
    )
    aggregate["operator_interventions"] = [
        {
            "id": 12,
            "action_type": "replan_from_milestone",
            "payload": {"milestone_id": "m2"},
        }
    ]

    result = workflow_driver._maybe_consume_replan_from_milestone(  # noqa: SLF001
        "feature-1",
        aggregate,
        None,
    )

    assert result is None


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow_replan_after_blocked_attempts=3,
        workflow_replan_after_input_tokens=750_000,
        workflow_same_role_repair_blocker_codes=[
            "engineering.qa_semantic_quality_failed",
            "engineering.qa_tests_do_not_compile",
            "engineering.qa_tests_not_red",
            "engineering.qa_no_changes",
            "engineering.implementer_contract_invalid",
            "engineering.implementation_path_violation",
            "engineering.implementation_verification_failed",
            "engineering.worker_crash",
        ],
        workflow_replan_immediate_blocker_codes=[
            "engineering.qa_path_violation",
            "engineering.implementer_contract_invalid",
            "engineering.implementation_reported_blockers",
            "engineering.implementation_path_violation",
            "engineering.review_rejected",
            "engineering.qa_verify_failed",
            "engineering.qa_usertest_contract_invalid",
            "engineering.qa_usertest_failed",
            "engineering.implementation_verification_failed",
            "engineering.invalid_handler_output",
            "engineering.plan_contract_invalid",
            "engineering.planner_council_exhausted",
            "engineering.handoff_missing",
            "engineering.qa_handoff_missing",
            "engineering.worker_crash",
        ],
        workflow_replan_blocker_codes=[
            "engineering.qa_semantic_quality_failed",
            "engineering.qa_tests_do_not_compile",
            "engineering.qa_tests_not_red",
            "engineering.qa_path_violation",
            "engineering.implementer_contract_invalid",
            "engineering.implementation_reported_blockers",
            "engineering.implementation_path_violation",
            "engineering.review_rejected",
            "engineering.qa_verify_failed",
            "engineering.qa_usertest_contract_invalid",
            "engineering.qa_usertest_failed",
            "engineering.implementation_verification_failed",
            "engineering.invalid_handler_output",
            "engineering.plan_contract_invalid",
            "engineering.planner_council_exhausted",
            "engineering.handoff_missing",
            "engineering.qa_handoff_missing",
            "engineering.worker_crash",
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


def _milestone_plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="trade-research-platform",
        problem_statement="Add small feature",
        design_contract=DesignContract(public_api="Small API"),
        affected_surfaces=["src/", "tests/"],
        task_slices=[
            TaskSliceContract(
                slice_id="design",
                role="designer",
                task_type="engineering.design",
                objective="Design the small API.",
                allowed_paths=["docs/"],
                forbidden_paths=["src/"],
                expected_outputs=["DesignContract"],
                verification_commands=[["./gradlew", "test"]],
                acceptance_assertion_ids=["behavior is covered"],
                milestone_id="m1",
            ),
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write feature-specific tests.",
                allowed_paths=["tests/"],
                forbidden_paths=["src/"],
                depends_on=["design"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[["./gradlew", "test"]],
                acceptance_assertion_ids=["behavior is covered"],
                milestone_id="m2",
            ),
            TaskSliceContract(
                slice_id="impl",
                role="implementer",
                task_type="engineering.implement",
                objective="Implement the small API.",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
                depends_on=["qa-author"],
                expected_outputs=["TaskResultContract"],
                verification_commands=[["./gradlew", "test"]],
                acceptance_assertion_ids=["behavior is covered"],
                milestone_id="m2",
            ),
        ],
        acceptance_test_matrix=["behavior is covered"],
        acceptance_assertions=["behavior is covered"],
        milestones=[
            MilestoneContract(
                milestone_id="m1",
                name="Design",
                slice_ids=["design"],
                acceptance_assertions=["behavior is covered"],
                validation_contract={"scrutiny": True},
                signoff_policy="scrutiny_only",
            ),
            MilestoneContract(
                milestone_id="m2",
                name="Build",
                slice_ids=["qa-author", "impl"],
                acceptance_assertions=["behavior is covered"],
                validation_contract={"scrutiny": True},
                signoff_policy="scrutiny_only",
            ),
        ],
    )
