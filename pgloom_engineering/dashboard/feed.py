from __future__ import annotations

from typing import Any

import psycopg
from pgloom.dashboard import DashboardSection


class EngineeringFeatureCollector:
    def collect(self, conn: psycopg.Connection[dict[str, Any]]) -> DashboardSection:
        rows = conn.execute(
            """
            select project, state, count(*) as count
            from engineering_features
            group by project, state
            order by project, state
            """
        ).fetchall()
        return DashboardSection(
            key="engineering_features",
            title="Engineering Features",
            data=[dict(row) for row in rows],
        )


class EngineeringWorkerRunCollector:
    def collect(self, conn: psycopg.Connection[dict[str, Any]]) -> DashboardSection:
        rows = conn.execute(
            """
            select feature_id,
                   role,
                   phase,
                   validator_type,
                   status,
                   count(*) as runs,
                   coalesce(sum(cost_usd), 0) as cost_usd,
                   coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(token_savior_saved_tokens + rtk_saved_tokens), 0) as tokens_saved,
                   coalesce(sum(running_seconds), 0) as running_seconds
            from engineering_worker_runs
            group by feature_id, role, phase, validator_type, status
            order by feature_id, role, phase, validator_type, status
            """
        ).fetchall()
        return DashboardSection(
            key="engineering_worker_runs",
            title="Engineering Worker Runs",
            data=[dict(row) for row in rows],
        )


class EngineeringOperatorInterventionCollector:
    def collect(self, conn: psycopg.Connection[dict[str, Any]]) -> DashboardSection:
        rows = conn.execute(
            """
            select feature_id, actor, action_type, payload, created_at
            from engineering_operator_interventions
            order by created_at desc, id desc
            limit 200
            """
        ).fetchall()
        return DashboardSection(
            key="engineering_operator_interventions",
            title="Engineering Operator Interventions",
            data=[dict(row) for row in rows],
        )
