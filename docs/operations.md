# Operations

Operational entry points will be added as integrations land:

- `pgloom-engineering worker ...`
- `pgloom-engineering telegram run`
- `pgloom-engineering dashboard serve`
- `pgloom-engineering command-center serve`
- `pgloom-engineering reports ppd`
- `uv run python scripts/run_qa_author_eval_suite.py --suite docs/evals/qa-author-model-suite.json --output-dir <dir> --model gpt-5.5 --jobs 2`

For now, core pgloom commands are available under:

```bash
pgloom-engineering pgloom ...
```

Register projects in Postgres before creating features:

```bash
pgloom-engineering project register \
  --name pgloom \
  --root /Volumes/devssd/repos/oss/pgloom \
  --github-repo joshorig/pgloom \
  --implementation-topology council_decides \
  --smoke-command "pytest -q"

pgloom-engineering feature create --project pgloom --goal-file goal.md
```

Temporarily stop or resume dispatch for a project with:

```bash
pgloom-engineering project disable pgloom
pgloom-engineering project enable pgloom
```

Future feature-level operation should happen through Command Center rather than
direct table edits. Required interventions are:

- pause/resume feature dispatch
- skip a slice with an audited recovery handoff
- drop a slice and adjust downstream dependencies
- request a milestone replan with an operator note
- add a chat-style note for the orchestrator to consume on the next planning
  pass

Workers must refuse to claim paused features and milestone-locked downstream
work. User-test validation should run on a separate `qa-usertest` slot with a
per-project `full_app_run` resource lock, allowing user tests for different
projects to run in parallel while preventing same-project app teardown races.

For QA author evals, project-specific runtime, route, UI, benchmark, and gate
metadata belongs in `docs/evals/project-registry.yaml`. Prompts consume the same
metadata as the deterministic validation layer; do not rely on model guesses for
test roots, smoke/regression commands, endpoint coverage, or JMH conventions.

Each live worker run should leave enough telemetry for a future dashboard:

- task and role phase, including repair phase
- wall-clock timing and blocker state
- model profile, model, reasoning level, cost, and token usage
- Token Savior and RTK/log-filter token savings
- commands run with exit codes and durations
- artifact ids for prompts, responses, logs, diffs, screenshots, traces, and
  outcome JSON
