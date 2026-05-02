from __future__ import annotations

from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect


def create_feature(
    *,
    workflow_id: str,
    project: str,
    state: str = "open",
    branch: str | None = None,
    metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            insert into engineering_features(id, project, branch, state, metadata)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (workflow_id, project, branch, state, jsonb(metadata or {})),
        ).fetchone()
    if row is None:
        raise RuntimeError("feature insert did not return a row")
    return dict(row)
