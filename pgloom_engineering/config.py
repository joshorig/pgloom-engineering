from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineeringSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PGLOOM_ENGINEERING_",
        env_file=".env",
        extra="ignore",
    )

    projects_file: Path = Path(".local/projects.yaml")
    planner_panelist_profile: str = "planner-panelist"
    planner_critic_profile: str = "planner-critic"
    planner_consolidator_profile: str = "planner-consolidator"
    planner_panelist_count: int = 3
    planner_iter_1_panelist_count: int = 3
    planner_iter_2_panelist_count: int = 1
    planner_max_iterations: int = 3
    planner_invocation_timeout_seconds: float = 300.0
    planner_command: list[str] = Field(default_factory=lambda: ["cat"])
    planner_profile_commands: dict[str, list[str]] = Field(default_factory=dict)
    planner_token_savior_enabled: bool = True
    planner_token_savior_budget_tokens: int = 3500
    planner_context_capsule_cache_enabled: bool = True
    planner_context_capsule_version: str = "planning-context-capsule.v2"
    planner_small_feature_panelist_count: int = 2
    planner_high_risk_panelist_count: int = 3
    planner_memory_budget_tokens: int = 800
    planner_consolidator_scoped_inputs_enabled: bool = True
    planner_production_grade_preempts_critic: bool = True
    planner_production_grade_critic_sample_rate: float = 0.1
    planner_claude_panelist_model: str = "sonnet"
    planner_claude_consolidator_model: str = "haiku"
    planner_claude_critic_model: str = "haiku"
    planner_codex_panelist_model: str = "gpt-5.5"
    planner_codex_consolidator_model: str = "gpt-5.5"
    planner_codex_critic_model: str = "gpt-5.5"
    planner_codex_panelist_reasoning: str = "high"
    planner_codex_consolidator_reasoning: str = "medium"
    planner_codex_critic_reasoning: str = "medium"
    rtk_filter_enabled: bool = True
    rtk_passthrough_commands: list[str] = Field(default_factory=list)
    rtk_passthrough_on_success: bool = False
    rtk_max_tokens_after: int | None = None
    token_count_encoder: str = "cl100k_base"


def get_settings() -> EngineeringSettings:
    return EngineeringSettings()
