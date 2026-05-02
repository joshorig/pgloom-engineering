from __future__ import annotations

from pgloom.workflows import create_workflow

from pgloom_engineering.db.migrations import check
from pgloom_engineering.features import create_feature


def test_engineering_migrations_and_feature_round_trip(database_url: str) -> None:
    assert check(database_url)["ok"]
    workflow = create_workflow(domain="engineering", name="demo", database_url=database_url)
    feature = create_feature(
        workflow_id=workflow["id"],
        project="pgloom",
        branch="feature/demo",
        database_url=database_url,
    )
    assert feature["id"] == workflow["id"]
    assert feature["project"] == "pgloom"
