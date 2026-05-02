from __future__ import annotations

from importlib import resources
from typing import Any

from pgloom.db.postgres import connect

REQUIRED_TABLES = {
    "engineering_features",
    "engineering_feature_children",
    "engineering_self_repair_issues",
    "engineering_self_repair_deliberations",
    "engineering_token_savior_usage",
}


def _schema_files() -> list[Any]:
    schema = resources.files("pgloom_engineering.db.schema")
    return sorted(path for path in schema.iterdir() if path.name.endswith(".sql"))


def migrate(database_url: str | None = None) -> list[str]:
    applied: list[str] = []
    with connect(database_url) as conn, conn.transaction():
        conn.execute(
            """
            create table if not exists engineering_schema_migrations (
              version text primary key,
              applied_at timestamptz not null default now()
            )
            """
        )
        existing = {
            row["version"]
            for row in conn.execute("select version from engineering_schema_migrations")
        }
        for path in _schema_files():
            if path.name in existing:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "insert into engineering_schema_migrations(version) values (%s)",
                (path.name,),
            )
            applied.append(path.name)
    return applied


def check(database_url: str | None = None) -> dict[str, Any]:
    with connect(database_url) as conn:
        rows = conn.execute(
            """
            select table_name
            from information_schema.tables
            where table_schema = 'public'
            """
        ).fetchall()
    present = {str(row["table_name"]) for row in rows}
    missing = sorted(REQUIRED_TABLES - present)
    return {"ok": not missing, "tables": len(REQUIRED_TABLES - set(missing)), "missing": missing}
