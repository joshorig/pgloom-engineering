from __future__ import annotations

from typing import Any


class CandidateInvalid(Exception):
    def __init__(
        self,
        raw_response: str,
        parse_error: str,
        *,
        model_usage_id: int | None = None,
    ) -> None:
        super().__init__(parse_error)
        self.raw_response = raw_response
        self.parse_error = parse_error
        self.model_usage_id = model_usage_id


class PlannerCouncilExhausted(Exception):
    def __init__(self, iterations: list[Any], invalid_proposals: list[Any] | None = None) -> None:
        super().__init__("planner council exhausted")
        self.iterations = iterations
        self.invalid_proposals = invalid_proposals or []
