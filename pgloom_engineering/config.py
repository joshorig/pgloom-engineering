from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineeringSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PGLOOM_ENGINEERING_",
        env_file=".env",
        extra="ignore",
    )

    projects_file: Path = Path(".local/projects.yaml")


def get_settings() -> EngineeringSettings:
    return EngineeringSettings()
