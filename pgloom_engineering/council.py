from __future__ import annotations

from pydantic import BaseModel


class CouncilVerdict(BaseModel):
    approved: bool
    rationale: str
