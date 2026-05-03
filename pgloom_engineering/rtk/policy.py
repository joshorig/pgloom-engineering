from __future__ import annotations

from pathlib import Path

from pgloom.harness.subprocess import SubprocessResult
from pydantic import BaseModel, Field


class FilterPolicy(BaseModel):
    enabled: bool = True
    passthrough_commands: list[str] = Field(default_factory=list)
    passthrough_exit_codes: list[int] = Field(default_factory=list)
    max_tokens_after: int | None = None


def should_filter(result: SubprocessResult, policy: FilterPolicy) -> bool:
    if not policy.enabled:
        return False
    command = Path(result.argv[0]).name if result.argv else ""
    if command == "rtk" or command in policy.passthrough_commands:
        return False
    if result.exit_code in policy.passthrough_exit_codes:
        return False
    return bool(result.stdout or result.stderr)
