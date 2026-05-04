# Operations

Operational entry points will be added as integrations land:

- `pgloom-engineering worker ...`
- `pgloom-engineering telegram run`
- `pgloom-engineering dashboard serve`
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

For QA author evals, project-specific runtime, route, UI, benchmark, and gate
metadata belongs in `docs/evals/project-registry.yaml`. Prompts consume the same
metadata as the deterministic validation layer; do not rely on model guesses for
test roots, smoke/regression commands, endpoint coverage, or JMH conventions.
