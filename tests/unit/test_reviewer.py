from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pgloom_engineering.contracts import (
    DesignContract,
    PlanContract,
    ReviewVerdictContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.roles import reviewer
from pgloom_engineering.roles.reviewer import (
    ReviewerHandler,
    normalize_review_payload,
)


class ReviewerProvider:
    def invoke(self, *, profile: Any, prompt: str, **kwargs: Any) -> Any:
        del profile, kwargs
        payload = json.loads(prompt)
        assert payload["task_result_contract"]["changed_files"] == ["src/App.java"]
        assert any(
            "Do not block solely because QA-owned commands" in instruction
            for instruction in payload["instructions"]
        )
        assert payload["role_gate_contract"]["contract_version"] == (
            "engineering.role_gate_contract.v1"
        )
        assert payload["role_gate_contract"]["role"] == "reviewer"
        assert "ReviewVerdictContract schema validation" in payload["role_gate_contract"][
            "judged_by"
        ]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "ReviewVerdictContract": {
                        "feature_id": "feature-1",
                        "task_id": "review-1",
                        "panel": ["automated-reviewer"],
                        "verdict": "approve",
                        "rationale": "Scoped implementation with passing checks.",
                        "findings": [],
                    }
                }
            ),
            model_usage_id=7,
        )


def test_reviewer_generates_verdict_from_dependency_output(monkeypatch: Any) -> None:
    _patch_live_contracts(monkeypatch)

    result = ReviewerHandler(provider=ReviewerProvider()).handle(_task())

    assert result.status == "done"
    contract = result.result["review_verdict_contract"]
    assert contract["task_id"] == "review-1"
    assert contract["verdict"] == "approve"


def test_reviewer_accepts_payload_contract() -> None:
    verdict = ReviewVerdictContract(
        feature_id="feature-1",
        task_id="review-1",
        panel=["human"],
        verdict="approve",
        rationale="ok",
    )

    result = ReviewerHandler().handle(
        {
            "id": "review-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.review",
            "payload": {"review_verdict_contract": verdict.model_dump(mode="json")},
        }
    )

    assert result.status == "done"
    assert result.result["review_verdict_contract"]["panel"] == ["human"]


def test_normalize_review_payload_accepts_wrappers() -> None:
    assert normalize_review_payload({"ReviewVerdictContract": {"verdict": "approve"}}) == {
        "verdict": "approve"
    }
    assert normalize_review_payload({"review_verdict_contract": {"verdict": "approve"}}) == {
        "verdict": "approve"
    }


def test_normalize_review_payload_maps_revise_and_structured_findings() -> None:
    normalized = normalize_review_payload(
        {
            "verdict": "reject",
            "findings": [{"severity": "blocking", "message": "bad"}],
        }
    )

    assert normalized["verdict"] == "coder_repair"
    assert '"severity": "blocking"' in normalized["findings"][0]


def test_reviewer_accepts_payload_contract_with_reject_alias() -> None:
    result = ReviewerHandler().handle(
        {
            "id": "review-1",
            "workflow_id": "feature-1",
            "task_type": "engineering.review",
            "payload": {
                "review_verdict_contract": {
                    "feature_id": "feature-1",
                    "task_id": "review-1",
                    "panel": ["automated-reviewer"],
                    "verdict": "reject",
                    "rationale": "Implementation needs repair.",
                    "findings": [{"severity": "blocking", "message": "bug"}],
                }
            },
        }
    )

    assert result.status == "done"
    contract = result.result["review_verdict_contract"]
    assert contract["verdict"] == "coder_repair"
    assert '"severity": "blocking"' in contract["findings"][0]


def _patch_live_contracts(monkeypatch: Any) -> None:
    plan = _plan()
    task_contract = _review_contract()

    def fake_get_task_contract(task_id: str, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        if task_id == "review-1":
            return {"input_contract": task_contract.model_dump(mode="json")}
        if task_id == "impl-1":
            return {
                "input_contract": {},
                "output_contract": {
                    "feature_id": "feature-1",
                    "task_id": "impl-1",
                    "changed_files": ["src/App.java"],
                    "checks": [{"status": "passed"}],
                },
            }
        return None

    monkeypatch.setattr(reviewer, "get_task_contract", fake_get_task_contract)
    monkeypatch.setattr(
        reviewer,
        "get_active_plan_contract",
        lambda *args, **kwargs: {"contract": plan.model_dump(mode="json")},
    )
    monkeypatch.setattr(reviewer, "list_task_handoffs", lambda *args, **kwargs: [])


def _task() -> dict[str, Any]:
    return {
        "id": "review-1",
        "workflow_id": "feature-1",
        "task_type": "engineering.review",
        "payload": {"database_url": None},
    }


def _plan() -> PlanContract:
    return PlanContract(
        feature_id="feature-1",
        project="demo",
        problem_statement="Review implementation.",
        design_contract=DesignContract(acceptance_tests=["criterion"]),
        affected_surfaces=["src/"],
        task_slices=[
            TaskSliceContract(
                slice_id="review",
                role="reviewer",
                task_type="engineering.review",
                objective="Review source.",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
                expected_outputs=["ReviewVerdictContract"],
            )
        ],
        acceptance_test_matrix=["criterion"],
    )


def _review_contract() -> TaskContract:
    return TaskContract(
        feature_id="feature-1",
        plan_contract_id="plan-1",
        role="reviewer",
        task_type="engineering.review",
        objective="Review source.",
        inputs={"task_slice_id": "review"},
        allowed_paths=["src/"],
        forbidden_paths=["tests/"],
        dependencies=["impl-1"],
        expected_outputs=["ReviewVerdictContract"],
    )
