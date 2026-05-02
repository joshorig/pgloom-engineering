# pgloom-engineering

`pgloom-engineering` is the engineering-focused orchestrator built on top of
`pgloom`. It will host planner, implementer, reviewer, QA, historian, BRAID,
GitHub, worktree, Telegram, dashboard, and report integrations while keeping the
core runtime in `pgloom`.

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

This repo is currently Phase 1 scaffolding. Domain handlers and integrations are
added in later phases.
