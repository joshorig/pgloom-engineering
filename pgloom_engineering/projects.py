from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pgloom.db.json import jsonb
from pgloom.db.postgres import connect
from pydantic import BaseModel

from pgloom_engineering.contracts import AgentTopologyPolicy, ImplementationTopology


class ProjectConfig(BaseModel):
    name: str
    root: Path
    github_repo: str | None = None
    base_branch: str = "main"
    smoke_command: list[str] = []
    regression_command: list[str] = []
    agent_topology: AgentTopologyPolicy = AgentTopologyPolicy()
    state: str = "active"
    metadata: dict[str, Any] = {}


def default_agent_topology() -> AgentTopologyPolicy:
    return AgentTopologyPolicy()


def project_agent_topology(project: ProjectConfig | None = None) -> AgentTopologyPolicy:
    if project is None:
        return default_agent_topology()
    return project.agent_topology


def role_enabled(project: ProjectConfig, role: str) -> bool:
    gates = project.metadata.get("role_gates")
    if not isinstance(gates, dict):
        return True
    value = gates.get(role, "enabled")
    return isinstance(value, str) and value == "enabled"


def register_project(
    project: ProjectConfig,
    *,
    replace: bool = False,
    database_url: str | None = None,
) -> ProjectConfig:
    with connect(database_url) as conn, conn.transaction():
        existing = conn.execute(
            "select name from engineering_projects where name = %s",
            (project.name,),
        ).fetchone()
        if existing is not None and not replace:
            raise ValueError(f"project already exists: {project.name}")
        row = conn.execute(
            """
            insert into engineering_projects(
              name, root, github_repo, base_branch, smoke_command, regression_command,
              agent_topology, state, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (name) do update set
              root = excluded.root,
              github_repo = excluded.github_repo,
              base_branch = excluded.base_branch,
              smoke_command = excluded.smoke_command,
              regression_command = excluded.regression_command,
              agent_topology = excluded.agent_topology,
              state = excluded.state,
              metadata = excluded.metadata,
              updated_at = now()
            returning *
            """,
            (
                project.name,
                str(project.root),
                project.github_repo,
                project.base_branch,
                jsonb(project.smoke_command),
                jsonb(project.regression_command),
                jsonb(project.agent_topology.model_dump(mode="json")),
                project.state,
                jsonb(project.metadata),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("project insert did not return a row")
    return _project_from_row(dict(row))


def get_project(name: str, *, database_url: str | None = None) -> ProjectConfig | None:
    with connect(database_url) as conn:
        row = conn.execute(
            "select * from engineering_projects where name = %s",
            (name,),
        ).fetchone()
    return _project_from_row(dict(row)) if row else None


def update_project_state(
    name: str,
    state: str,
    *,
    database_url: str | None = None,
) -> ProjectConfig | None:
    with connect(database_url) as conn, conn.transaction():
        row = conn.execute(
            """
            update engineering_projects
            set state = %s, updated_at = now()
            where name = %s
            returning *
            """,
            (state, name),
        ).fetchone()
    return _project_from_row(dict(row)) if row else None


def list_projects(
    *,
    state: str | None = None,
    database_url: str | None = None,
) -> list[ProjectConfig]:
    where_sql = "where state = %s" if state is not None else ""
    params: list[Any] = [state] if state is not None else []
    with connect(database_url) as conn:
        rows = conn.execute(
            f"""
            select *
            from engineering_projects
            {where_sql}
            order by name
            """,
            params,
        ).fetchall()
    return [_project_from_row(dict(row)) for row in rows]


def load_projects_file(path: Path) -> list[ProjectConfig]:
    if not path.exists():
        return []
    data = _load_project_data(path)
    if isinstance(data, dict):
        rows = data.get("projects", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    return [ProjectConfig.model_validate(row) for row in rows]


def import_projects_file(
    path: Path,
    *,
    replace: bool = False,
    database_url: str | None = None,
) -> list[ProjectConfig]:
    imported = []
    for project in load_projects_file(path):
        imported.append(register_project(project, replace=replace, database_url=database_url))
    return imported


def resolve_project_file(path: Path, name: str) -> ProjectConfig | None:
    for project in load_projects_file(path):
        if project.name == name:
            return project
    return None


def write_projects(path: Path, projects: list[ProjectConfig]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"projects": [project.model_dump(mode="json") for project in projects]}
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    _write_yaml(path, payload)


def _load_project_data(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            f"{path} requires PyYAML; use JSON or install pgloom-engineering with yaml support"
        ) from exc
    loaded = yaml.safe_load(text)
    return loaded or {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            f"{path} requires PyYAML; use JSON or install pgloom-engineering with yaml support"
        ) from exc
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def normalize_implementation_topology(value: str | None) -> ImplementationTopology:
    if not value:
        return ImplementationTopology.COUNCIL_DECIDES
    return ImplementationTopology(value)


def _project_from_row(row: dict[str, Any]) -> ProjectConfig:
    return ProjectConfig(
        name=str(row["name"]),
        root=Path(str(row["root"])),
        github_repo=row.get("github_repo"),
        base_branch=str(row["base_branch"]),
        smoke_command=list(row.get("smoke_command") or []),
        regression_command=list(row.get("regression_command") or []),
        agent_topology=AgentTopologyPolicy.model_validate(row.get("agent_topology") or {}),
        state=str(row["state"]),
        metadata=dict(row.get("metadata") or {}),
    )
