from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from pgloom.db.migrations import migrate as migrate_pgloom
from pgloom.db.postgres import connect

from pgloom_engineering.db.migrations import migrate as migrate_engineering


@pytest.fixture()
def database_url() -> Iterator[str]:
    url = os.environ.get("PGLOOM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PGLOOM_TEST_DATABASE_URL not set")
    migrate_pgloom(url)
    migrate_engineering(url)
    with connect(url) as conn, conn.transaction():
        conn.execute(
            """
            truncate table
              engineering_recovery_actions,
              engineering_worker_runs,
              engineering_operator_interventions,
              engineering_handoffs,
              engineering_task_contracts,
              engineering_plan_contracts,
              engineering_projects,
              engineering_project_context_capsules,
              engineering_token_savior_usage,
              token_savings,
              engineering_self_repair_deliberations,
              engineering_self_repair_issues,
              engineering_feature_children,
              engineering_features,
              blocker_codes,
              memory_entries,
              scenario_assertions,
              scenario_runs,
              resource_locks,
              quota_buckets,
              external_actions,
              model_usage,
              model_profiles,
              health_checks,
              slots,
              workers,
              approvals,
              artifacts,
              task_events,
              task_dependencies,
              tasks,
              workflows
            restart identity cascade
            """
        )
    yield url
