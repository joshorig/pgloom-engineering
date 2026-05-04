# pgloom-engineering

`pgloom-engineering` is the engineering-focused orchestrator built on top of
`pgloom`. It hosts planner, implementer, reviewer, QA, historian, GitHub,
worktree, Telegram, dashboard, and report integrations while keeping the core
runtime in `pgloom`.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pgloom-engineering --help
pgloom-engineering pgloom --help
```

Run core database migrations through the wrapped pgloom CLI:

```bash
export PGLOOM_DATABASE_URL=postgresql://localhost/pgloom_engineering_dev
pgloom-engineering pgloom db migrate
pgloom-engineering db migrate
```

## Status

Current focus is the autonomous engineering workflow:

- Planner produces typed multi-agent `PlanContract`s with token-economy context.
- QA author can create failing tests in isolated worktrees, validate required
  project gates, run deterministic semantic quality checks, and emit
  `QAAuthorContract` output.
- Project metadata in `docs/evals/project-registry.yaml` describes QA commands,
  required gates, test roots, endpoint conventions, benchmark conventions, and
  semantic rules used by prompts and deterministic gates.
- Generated eval reports under `docs/reports/*/` are local artifacts; source eval
  fixtures live under `docs/evals/`.

Run the local validation suite with:

```bash
uv run pytest -q
```
