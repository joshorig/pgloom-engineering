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
    planner_invocation_timeout_seconds: float = 600.0
    planner_command: list[str] = Field(default_factory=lambda: ["cat"])
    planner_profile_commands: dict[str, list[str]] = Field(default_factory=dict)
    planner_token_savior_enabled: bool = True
    planner_token_savior_budget_tokens: int = 3500
    planner_context_capsule_cache_enabled: bool = True
    planner_context_capsule_version: str = "planning-context-capsule.v2"
    planner_small_feature_panelist_count: int = 2
    planner_high_risk_panelist_count: int = 3
    planner_memory_budget_tokens: int = 800
    role_context_token_savior_enabled: bool = True
    role_context_token_savior_budget_tokens: int = 2500
    role_context_memory_budget_tokens: int = 800
    role_context_capsule_cache_enabled: bool = True
    role_context_capsule_version: str = "role-context-capsule.v1"
    role_model_context_isolation_enabled: bool = False
    qa_author_model_context_isolation_enabled: bool = True
    qa_author_model_context_add_dir_enabled: bool = False
    qa_validation_model_context_isolation_enabled: bool = False
    qa_validation_model_context_add_dir_enabled: bool = True
    implementer_model_context_isolation_enabled: bool = True
    implementer_model_context_add_dir_enabled: bool = False
    reviewer_model_context_isolation_enabled: bool = False
    reviewer_model_context_add_dir_enabled: bool = True
    role_model_context_root: Path = Path(".")
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
    qa_author_profile: str = "qa-author"
    qa_author_command: list[str] = Field(default_factory=lambda: ["cat"])
    qa_author_invocation_timeout_seconds: float = 600.0
    qa_author_escalate_after_attempts: int = 2
    qa_author_codex_model: str = "gpt-5.4"
    qa_author_codex_reasoning: str = "medium"
    qa_author_claude_model: str = "haiku"
    qa_author_code_repair_attempts: int = 1
    qa_author_contract_repair_attempts: int = 1
    qa_author_quality_repair_attempts: int = 0
    qa_author_no_changes_repair_attempts: int = 1
    qa_validation_profile: str = "qa-validation"
    qa_validation_command: list[str] = Field(default_factory=lambda: ["cat"])
    qa_validation_invocation_timeout_seconds: float = 600.0
    qa_validation_codex_model: str = "gpt-5.4"
    qa_validation_codex_reasoning: str = "medium"
    qa_validation_claude_model: str = "haiku"
    workflow_replan_after_blocked_attempts: int = 3
    workflow_replan_after_input_tokens: int = 750_000
    workflow_replan_immediate_blocker_codes: list[str] = Field(
        default_factory=lambda: [
            "engineering.qa_path_violation",
            "engineering.qa_no_changes",
            "engineering.task_contract_missing",
            "engineering.active_plan_missing",
            "engineering.milestone_locked",
            "engineering.implementer_contract_invalid",
            "engineering.implementation_reported_blockers",
            "engineering.implementation_path_violation",
            "engineering.implementation_verification_failed",
            "engineering.invalid_handler_output",
            "engineering.plan_contract_invalid",
            "engineering.planner_council_exhausted",
            "engineering.handoff_missing",
            "engineering.qa_handoff_missing",
            "engineering.qa_usertest_contract_invalid",
            "engineering.review_rejected",
            "engineering.qa_verify_failed",
            "engineering.qa_usertest_failed",
            "engineering.worker_crash",
        ]
    )
    workflow_replan_blocker_codes: list[str] = Field(
        default_factory=lambda: [
            "engineering.qa_semantic_quality_failed",
            "engineering.qa_tests_do_not_compile",
            "engineering.qa_tests_not_red",
            "engineering.qa_author_contract_invalid",
            "engineering.qa_no_changes",
            "engineering.qa_path_violation",
            "engineering.implementer_contract_invalid",
            "engineering.implementation_reported_blockers",
            "engineering.implementation_path_violation",
            "engineering.implementation_verification_failed",
            "engineering.invalid_handler_output",
            "engineering.plan_contract_invalid",
            "engineering.planner_council_exhausted",
            "engineering.handoff_missing",
            "engineering.qa_handoff_missing",
            "engineering.review_rejected",
            "engineering.review_contract_invalid",
            "engineering.qa_verify_failed",
            "engineering.qa_usertest_contract_invalid",
            "engineering.qa_usertest_failed",
            "engineering.worker_crash",
        ]
    )
    implementer_profile: str = "implementer"
    implementer_command: list[str] = Field(default_factory=lambda: ["cat"])
    implementer_invocation_timeout_seconds: float = 600.0
    implementer_inline_repair_attempts: int = 1
    implementer_source_starter_max_source_files: int = 3
    implementer_source_starter_max_test_files: int = 2
    implementer_source_starter_max_file_chars: int = 3200
    implementer_source_starter_max_total_chars: int = 12000
    qa_author_repair_max_file_chars: int = 12000
    qa_author_repair_max_total_file_chars: int = 36000
    qa_author_repair_max_response_chars: int = 12000
    implementer_codex_model: str = "gpt-5.4"
    implementer_codex_reasoning: str = "medium"
    implementer_claude_model: str = "sonnet"
    reviewer_profile: str = "reviewer"
    reviewer_command: list[str] = Field(default_factory=lambda: ["cat"])
    reviewer_invocation_timeout_seconds: float = 600.0
    reviewer_codex_model: str = "gpt-5.4"
    reviewer_codex_reasoning: str = "medium"
    reviewer_claude_model: str = "sonnet"
    qa_worktree_root: Path = Path(".local/worktrees")
    rtk_filter_enabled: bool = True
    rtk_passthrough_commands: list[str] = Field(default_factory=list)
    rtk_passthrough_on_success: bool = False
    rtk_max_tokens_after: int | None = None
    token_count_encoder: str = "cl100k_base"


def get_settings() -> EngineeringSettings:
    return EngineeringSettings()
