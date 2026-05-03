from __future__ import annotations

from pgloom_engineering.contracts import PlanContract


def candidate_summary(candidate: PlanContract) -> dict[str, object]:
    return {
        "feature_id": candidate.feature_id,
        "project": candidate.project,
        "problem_statement": _limit(candidate.problem_statement),
        "assumptions": [_limit(item) for item in candidate.assumptions],
        "design_contract": _limit_dict(candidate.design_contract.model_dump(mode="json")),
        "affected_surfaces": candidate.affected_surfaces,
        "implementation_topology": candidate.implementation_topology.value,
        "task_slices": [
            {
                "slice_id": task_slice.slice_id,
                "role": task_slice.role,
                "task_type": task_slice.task_type,
                "objective": _limit(task_slice.objective, limit=700),
                "allowed_paths": task_slice.allowed_paths,
                "forbidden_paths": task_slice.forbidden_paths,
                "depends_on": task_slice.depends_on,
                "expected_outputs": task_slice.expected_outputs,
                "verification_commands": task_slice.verification_commands,
            }
            for task_slice in candidate.task_slices
        ],
        "acceptance_test_matrix": [_limit(item) for item in candidate.acceptance_test_matrix],
        "risk_register": [_limit(item) for item in candidate.risk_register],
        "self_heal_policy": candidate.self_heal_policy,
        "finalization_policy": candidate.finalization_policy,
    }


def _limit_dict(value: dict[str, object], *, limit: int = 900) -> dict[str, object]:
    return {
        key: _limit(item, limit=limit) if isinstance(item, str) else item
        for key, item in value.items()
    }


def _limit(value: str, *, limit: int = 900) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"
