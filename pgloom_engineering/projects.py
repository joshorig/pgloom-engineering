from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ProjectConfig(BaseModel):
    name: str
    root: Path
    base_branch: str = "main"
    smoke_command: list[str] = []
    regression_command: list[str] = []
