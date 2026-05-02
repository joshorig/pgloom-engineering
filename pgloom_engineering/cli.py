from __future__ import annotations

import json
from typing import Annotated

import typer
from pgloom.cli import app as pgloom_app
from rich.console import Console
from rich.table import Table

from pgloom_engineering.db.migrations import check as check_engineering_schema
from pgloom_engineering.db.migrations import migrate as migrate_engineering_schema
from pgloom_engineering.features import get_feature_aggregate, list_features

app = typer.Typer(help="Engineering orchestrator built on pgloom.")
app.add_typer(pgloom_app, name="pgloom")

db_app = typer.Typer(help="Engineering-specific database commands.")
app.add_typer(db_app, name="db")

feature_app = typer.Typer(help="Feature aggregate commands.")
app.add_typer(feature_app, name="feature")

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


@feature_app.command("list")
def feature_list(
    project: Annotated[
        str | None,
        typer.Option("--project", help="Filter by project name."),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Filter by feature state."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    rows = list_features(project=project, state=state, database_url=database_url)
    if as_json:
        console.print(_json(rows))
        return
    table = Table(title="Features")
    table.add_column("ID")
    table.add_column("Project")
    table.add_column("State")
    table.add_column("Branch")
    table.add_column("PR")
    table.add_column("Updated")
    for row in rows:
        table.add_row(
            str(row["id"]),
            str(row["project"]),
            str(row["state"]),
            str(row.get("branch") or ""),
            str(row.get("pr_url") or ""),
            str(row.get("updated_at") or ""),
        )
    console.print(table)


@feature_app.command("show")
def feature_show(
    feature_id: str,
    events_limit: Annotated[
        int,
        typer.Option("--events-limit", min=0, help="Maximum recent task events to show."),
    ] = 20,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of human-readable output."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    aggregate = get_feature_aggregate(
        feature_id,
        events_limit=events_limit,
        database_url=database_url,
    )
    if aggregate is None:
        raise typer.BadParameter(f"Feature not found: {feature_id}")
    if as_json:
        console.print(_json(aggregate))
        return
    _print_feature_aggregate(aggregate)


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


def _json(value: object) -> str:
    return json.dumps(value, default=str, indent=2, sort_keys=True)


def _print_feature_aggregate(aggregate: dict[str, object]) -> None:
    feature = aggregate["feature"]
    workflow = aggregate["workflow"]
    assert isinstance(feature, dict)
    assert workflow is None or isinstance(workflow, dict)

    summary = Table(title=f"Feature {feature['id']}")
    summary.add_column("Field")
    summary.add_column("Value")
    for key in ["project", "state", "branch", "pr_url", "created_at", "updated_at"]:
        summary.add_row(key, str(feature.get(key) or ""))
    if workflow:
        summary.add_row("workflow_name", str(workflow.get("name") or ""))
        summary.add_row("workflow_state", str(workflow.get("state") or ""))
    console.print(summary)

    tasks = aggregate["tasks"]
    assert isinstance(tasks, list)
    task_table = Table(title="Tasks")
    for column in [
        "Role",
        "Task",
        "Type",
        "Slot",
        "State",
        "Attempt",
        "Blocker",
        "Updated",
    ]:
        task_table.add_column(column)
    for task in tasks:
        assert isinstance(task, dict)
        task_table.add_row(
            str(task.get("role") or ""),
            str(task.get("id") or ""),
            str(task.get("task_type") or ""),
            str(task.get("slot") or ""),
            str(task.get("state") or ""),
            f"{task.get('attempt')}/{task.get('max_attempts')}",
            str(task.get("blocker_code") or task.get("blocker_reason") or ""),
            str(task.get("updated_at") or ""),
        )
    console.print(task_table)

    model_usage = aggregate["model_usage"]
    assert isinstance(model_usage, dict)
    usage_summary = model_usage["summary"]
    assert isinstance(usage_summary, dict)
    console.print(
        "Model usage: "
        f"{usage_summary['input_tokens']} input / "
        f"{usage_summary['output_tokens']} output tokens, "
        f"${float(usage_summary['cost_usd']):.6f}"
    )

    token_savior = aggregate["token_savior"]
    assert isinstance(token_savior, dict)
    token_summary = token_savior["summary"]
    assert isinstance(token_summary, dict)
    original = int(token_summary["input_tokens_original"])
    if original:
        console.print(
            "Token Savior: "
            f"{int(token_summary['tokens_saved']):,} saved / "
            f"{original:,} original "
            f"({float(token_summary['reduction_ratio']) * 100:.1f}%), "
            f"est. ${float(token_summary['estimated_cost_saved_usd']):.6f} saved"
        )
        token_rows = token_savior["by_task"]
        assert isinstance(token_rows, list)
        token_table = Table(title="Token Savior")
        for column in ["Task", "Profile", "Original", "After", "Saved", "Reduction", "Saved $"]:
            token_table.add_column(column)
        for row in token_rows:
            assert isinstance(row, dict)
            token_table.add_row(
                str(row.get("task_id") or ""),
                str(row.get("profile_name") or ""),
                f"{int(row['input_tokens_original']):,}",
                f"{int(row['input_tokens_after_savior']):,}",
                f"{int(row['tokens_saved']):,}",
                f"{float(row['reduction_ratio']) * 100:.1f}%",
                f"{float(row['estimated_cost_saved_usd']):.6f}",
            )
        console.print(token_table)
    else:
        console.print("Token Savior: no recorded usage")

    recent_events = aggregate["recent_events"]
    assert isinstance(recent_events, list)
    if recent_events:
        events = Table(title="Recent Events")
        for column in ["Task", "Event", "From", "To", "Message", "Created"]:
            events.add_column(column)
        for event in recent_events:
            assert isinstance(event, dict)
            events.add_row(
                str(event.get("task_id") or ""),
                str(event.get("event_type") or ""),
                str(event.get("from_state") or ""),
                str(event.get("to_state") or ""),
                str(event.get("message") or ""),
                str(event.get("created_at") or ""),
            )
        console.print(events)
