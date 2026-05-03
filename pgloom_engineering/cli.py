from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated

import typer
from pgloom.cli import app as pgloom_app
from pgloom.tasks import enqueue_task
from pgloom.workflows import create_workflow
from rich.console import Console
from rich.table import Table

from pgloom_engineering.config import get_settings
from pgloom_engineering.contracts import FeatureGoalContract
from pgloom_engineering.db.migrations import check as check_engineering_schema
from pgloom_engineering.db.migrations import migrate as migrate_engineering_schema
from pgloom_engineering.features import (
    attach_task,
    create_feature,
    get_feature_aggregate,
    list_features,
)
from pgloom_engineering.model_provider import EngineeringCLIModelProvider
from pgloom_engineering.planner import CouncilConfig, PlannerCouncil, ProjectContext
from pgloom_engineering.planner.exceptions import PlannerCouncilExhausted
from pgloom_engineering.projects import (
    ProjectConfig,
    default_agent_topology,
    get_project,
    import_projects_file,
    list_projects,
    normalize_implementation_topology,
    register_project,
    update_project_state,
)
from pgloom_engineering.worker import run_once as run_engineering_worker_once

app = typer.Typer(help="Engineering orchestrator built on pgloom.")
app.add_typer(pgloom_app, name="pgloom")

db_app = typer.Typer(help="Engineering-specific database commands.")
app.add_typer(db_app, name="db")

feature_app = typer.Typer(help="Feature aggregate commands.")
app.add_typer(feature_app, name="feature")

project_app = typer.Typer(help="Project registry commands.")
app.add_typer(project_app, name="project")

worker_app = typer.Typer(help="Engineering worker commands.")
app.add_typer(worker_app, name="worker")

plan_app = typer.Typer(help="Planner council commands.")
app.add_typer(plan_app, name="plan")

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


@project_app.command("register")
def project_register(
    name: Annotated[str, typer.Option("--name", help="Project name.")],
    root: Annotated[
        Path,
        typer.Option("--root", exists=True, file_okay=False, help="Repository root."),
    ],
    github_repo: Annotated[
        str | None,
        typer.Option("--github-repo", help="GitHub repo as owner/name."),
    ] = None,
    base_branch: Annotated[
        str,
        typer.Option("--base-branch", help="Base branch for final feature PRs."),
    ] = "main",
    implementation_topology: Annotated[
        str,
        typer.Option(
            "--implementation-topology",
            help="single, split_specialists, parallel_candidates, or council_decides.",
        ),
    ] = "council_decides",
    smoke_command: Annotated[
        str | None,
        typer.Option("--smoke-command", help="Smoke command, shell-split into argv."),
    ] = None,
    regression_command: Annotated[
        str | None,
        typer.Option("--regression-command", help="Regression command, shell-split into argv."),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Replace an existing project."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a sentence."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    topology = default_agent_topology()
    topology.implementation = normalize_implementation_topology(implementation_topology)
    project = ProjectConfig(
        name=name,
        root=root.resolve(),
        github_repo=github_repo,
        base_branch=base_branch,
        smoke_command=shlex.split(smoke_command or ""),
        regression_command=shlex.split(regression_command or ""),
        agent_topology=topology,
    )
    try:
        registered = register_project(project, replace=replace, database_url=database_url)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        console.print(_json(registered.model_dump(mode="json")))
        return
    console.print(f"Registered project {registered.name} at {registered.root}")


@worker_app.command("run-once")
def worker_run_once(
    slot: Annotated[str, typer.Option("--slot", help="Worker slot to claim from.")],
    worker_id: Annotated[
        str | None,
        typer.Option("--worker-id", help="Worker id. Defaults to pgloom-engineering-<slot>."),
    ] = None,
    lease_seconds: Annotated[
        int,
        typer.Option("--lease-seconds", min=1, help="Task lease duration."),
    ] = 300,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    result = run_engineering_worker_once(
        slot=slot,
        worker_id=worker_id or f"pgloom-engineering-{slot}",
        lease_seconds=lease_seconds,
        database_url=database_url,
    )
    console.print(_json(result))


@project_app.command("list")
def project_list(
    state: Annotated[
        str | None,
        typer.Option("--state", help="Filter by project state."),
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
    rows = list_projects(state=state, database_url=database_url)
    payload = [row.model_dump(mode="json") for row in rows]
    if as_json:
        console.print(_json(payload))
        return
    table = Table(title="Projects")
    for column in ["Name", "State", "Root", "Repo", "Base", "Implementation"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            row.name,
            row.state,
            str(row.root),
            row.github_repo or "",
            row.base_branch,
            row.agent_topology.implementation.value,
        )
    console.print(table)


@project_app.command("show")
def project_show(
    name: str,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    project = get_project(name, database_url=database_url)
    if project is None:
        raise typer.BadParameter(f"Project not found: {name}")
    payload = project.model_dump(mode="json")
    if as_json:
        console.print(_json(payload))
        return
    table = Table(title=f"Project {project.name}")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in payload.items():
        rendered = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        table.add_row(str(key), rendered)
    console.print(table)


@project_app.command("enable")
def project_enable(
    name: str,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a sentence."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    _project_set_state(name, "active", as_json=as_json, database_url=database_url)


@project_app.command("disable")
def project_disable(
    name: str,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a sentence."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    _project_set_state(name, "disabled", as_json=as_json, database_url=database_url)


@project_app.command("archive")
def project_archive(
    name: str,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a sentence."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    _project_set_state(name, "archived", as_json=as_json, database_url=database_url)


@project_app.command("import")
def project_import(
    path: Annotated[
        Path,
        typer.Option("--file", exists=True, dir_okay=False, help="YAML/JSON project file."),
    ],
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Replace existing projects."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a sentence."),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    imported = import_projects_file(path, replace=replace, database_url=database_url)
    payload = [row.model_dump(mode="json") for row in imported]
    if as_json:
        console.print(_json(payload))
        return
    console.print(f"Imported {len(imported)} project(s)")


@feature_app.command("create")
def feature_create(
    project: Annotated[str, typer.Option("--project", help="Project name.")],
    goal: Annotated[
        str | None,
        typer.Option("--goal", help="Goal text. Use --goal-file for longer requirements."),
    ] = None,
    goal_file: Annotated[
        Path | None,
        typer.Option("--goal-file", exists=True, dir_okay=False, help="Goal text or JSON file."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Workflow name. Defaults to the first goal line."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table."),
    ] = False,
    allow_unregistered_project: Annotated[
        bool,
        typer.Option(
            "--allow-unregistered-project",
            help="Use default topology when the project is not registered.",
        ),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Override PGLOOM_DATABASE_URL."),
    ] = None,
) -> None:
    if not goal and not goal_file:
        raise typer.BadParameter("Provide --goal or --goal-file.")
    goal_contract = _goal_contract(project=project, goal=goal, goal_file=goal_file)
    project_config = get_project(project, database_url=database_url)
    if project_config is None and not allow_unregistered_project:
        raise typer.BadParameter(
            f"Project not registered: {project}. Run `pgloom-engineering project register` first."
        )
    topology = (
        project_config.agent_topology if project_config is not None else default_agent_topology()
    )
    project_metadata = (
        project_config.model_dump(mode="json") if project_config is not None else None
    )
    workflow = create_workflow(
        domain="engineering",
        name=name or goal_contract.goal.splitlines()[0][:120],
        metadata={
            "feature_goal_contract": goal_contract.model_dump(mode="json"),
            "agent_topology": topology.model_dump(mode="json"),
            "project": project_metadata,
        },
        database_url=database_url,
    )
    feature = create_feature(
        workflow_id=workflow["id"],
        project=project,
        branch=f"feature/{workflow['id']}",
        metadata={
            "feature_goal_contract": goal_contract.model_dump(mode="json"),
            "agent_topology": topology.model_dump(mode="json"),
            "project": project_metadata,
        },
        database_url=database_url,
    )
    planner = enqueue_task(
        workflow_id=workflow["id"],
        domain="engineering",
        task_type="engineering.plan",
        slot="planner",
        payload={
            "feature_goal_contract": goal_contract.model_dump(mode="json"),
            "agent_topology": topology.model_dump(mode="json"),
            "project": project_metadata,
            "allow_unregistered_project": allow_unregistered_project,
            "requires_multi_agent_council": True,
        },
        database_url=database_url,
    )
    attach_task(feature["id"], planner["id"], role="planner", database_url=database_url)
    payload = {"workflow": workflow, "feature": feature, "planner_task": planner}
    if as_json:
        console.print(_json(payload))
        return
    console.print(f"Created feature {feature['id']} with planner task {planner['id']}")


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


@plan_app.command("dry-run")
def plan_dry_run(
    feature_goal: Annotated[
        Path,
        typer.Option(
            "--feature-goal",
            exists=True,
            dir_okay=False,
            help="FeatureGoalContract JSON.",
        ),
    ],
    project_root: Annotated[
        Path,
        typer.Option("--project-root", exists=True, file_okay=False, help="Project root."),
    ],
    panelist_profile: Annotated[
        str,
        typer.Option("--panelist-profile", help="CLIModelProfile name for panelists."),
    ] = "planner-panelist",
    critic_profile: Annotated[
        str,
        typer.Option("--critic-profile", help="CLIModelProfile name for critic."),
    ] = "planner-critic",
    consolidator_profile: Annotated[
        str,
        typer.Option("--consolidator-profile", help="CLIModelProfile name for consolidator."),
    ] = "planner-consolidator",
    max_iterations: Annotated[
        int,
        typer.Option("--max-iterations", min=1, help="Maximum council iterations."),
    ] = 3,
) -> None:
    goal = FeatureGoalContract.model_validate(json.loads(feature_goal.read_text(encoding="utf-8")))
    settings = get_settings()
    config = CouncilConfig(
        panelist_count=settings.planner_panelist_count,
        max_iterations=max_iterations,
        panelist_profile=panelist_profile,
        critic_profile=critic_profile,
        consolidator_profile=consolidator_profile,
        timeout_seconds_per_invocation=settings.planner_invocation_timeout_seconds,
        command=settings.planner_command,
        profile_commands=settings.planner_profile_commands,
    )
    council = _build_dry_run_council(config)
    try:
        outcome = council.run(
            feature_goal=goal,
            project_context=ProjectContext(
                project_root=project_root.resolve(),
                qa_smoke_path=project_root.resolve().joinpath("qa/smoke.sh"),
                qa_regression_path=project_root.resolve().joinpath("qa/regression.sh"),
            ),
        )
    except PlannerCouncilExhausted as exc:
        payload = {
            "error": "planner_council_exhausted",
            "iterations": [
                item.model_dump(mode="json", exclude={"proposals": {"__all__": {"raw_response"}}})
                if hasattr(item, "model_dump")
                else item
                for item in exc.iterations
            ],
        }
        typer.echo(_json(payload))
        raise typer.Exit(2) from exc
    typer.echo(_json(outcome.model_dump(mode="json")))


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


def _build_dry_run_council(config: CouncilConfig) -> PlannerCouncil:
    return PlannerCouncil(config=config, provider=EngineeringCLIModelProvider())


def _project_set_state(
    name: str,
    state: str,
    *,
    as_json: bool,
    database_url: str | None,
) -> None:
    project = update_project_state(name, state, database_url=database_url)
    if project is None:
        raise typer.BadParameter(f"Project not found: {name}")
    if as_json:
        console.print(_json(project.model_dump(mode="json")))
        return
    console.print(f"Project {project.name} is now {project.state}")


def _goal_contract(
    *,
    project: str,
    goal: str | None,
    goal_file: Path | None,
) -> FeatureGoalContract:
    if goal_file is None:
        assert goal is not None
        return FeatureGoalContract(project=project, goal=goal)
    text = goal_file.read_text(encoding="utf-8")
    if goal_file.suffix.lower() == ".json":
        payload = json.loads(text)
        payload.setdefault("project", project)
        if goal:
            payload["goal"] = goal
        return FeatureGoalContract.model_validate(payload)
    return FeatureGoalContract(project=project, goal=goal or text.strip())


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
    topology = aggregate.get("agent_topology")
    if isinstance(topology, dict):
        summary.add_row("planning_agents", str(topology.get("planning") or ""))
        summary.add_row("review_agents", str(topology.get("review") or ""))
        summary.add_row("implementation_agents", str(topology.get("implementation") or ""))
    console.print(summary)

    active_plan = aggregate.get("active_plan_contract")
    if isinstance(active_plan, dict):
        contract = active_plan.get("contract")
        if isinstance(contract, dict):
            console.print(
                "Plan contract: "
                f"{active_plan.get('status')} "
                f"{str(active_plan.get('contract_hash') or '')[:12]} "
                f"topology={contract.get('implementation_topology')}"
            )
    else:
        console.print("Plan contract: none")

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

    recovery = aggregate.get("recovery_actions")
    if isinstance(recovery, list) and recovery:
        recovery_table = Table(title="Recovery Actions")
        for column in ["Action", "Blocker", "Status", "Attempt", "Outcome", "Created"]:
            recovery_table.add_column(column)
        for row in recovery:
            assert isinstance(row, dict)
            recovery_table.add_row(
                str(row.get("action") or ""),
                str(row.get("blocker_code") or ""),
                str(row.get("status") or ""),
                f"{row.get('attempt')}/{row.get('max_attempts')}",
                str(row.get("outcome") or ""),
                str(row.get("created_at") or ""),
            )
        console.print(recovery_table)
