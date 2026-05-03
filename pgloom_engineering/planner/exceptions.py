from __future__ import annotations

from typing import Any


class CandidateInvalid(Exception):
    def __init__(self, raw_response: str, parse_error: str) -> None:
        super().__init__(parse_error)
        self.raw_response = raw_response
        self.parse_error = parse_error


class PlannerCouncilExhausted(Exception):
    def __init__(self, iterations: list[Any]) -> None:
        super().__init__("planner council exhausted")
        self.iterations = iterations
