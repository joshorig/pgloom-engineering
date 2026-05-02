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
