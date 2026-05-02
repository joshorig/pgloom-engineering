from __future__ import annotations

from typing import Annotated

import typer
from pgloom.cli import app as pgloom_app
from rich.console import Console

from pgloom_engineering.db.migrations import check as check_engineering_schema
from pgloom_engineering.db.migrations import migrate as migrate_engineering_schema

app = typer.Typer(help="Engineering orchestrator built on pgloom.")
app.add_typer(pgloom_app, name="pgloom")

db_app = typer.Typer(help="Engineering-specific database commands.")
app.add_typer(db_app, name="db")

console = Console()


@db_app.command("migrate")
def db_migrate(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    applied = migrate_engineering_schema(database_url)
    console.print({"applied": applied})


@db_app.command("check")
def db_check(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    console.print(check_engineering_schema(database_url))


@app.command("plan")
def plan() -> None:
    console.print("planning handler not implemented yet")


@app.command("implement")
def implement() -> None:
    console.print("implementation handler not implemented yet")


@app.command("review")
def review() -> None:
    console.print("review handler not implemented yet")


@app.command("feature-status")
def feature_status() -> None:
    console.print("feature status not implemented yet")
