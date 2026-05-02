# Operations

Operational entry points will be added as integrations land:

- `pgloom-engineering worker ...`
- `pgloom-engineering telegram run`
- `pgloom-engineering dashboard serve`
- `pgloom-engineering reports ppd`

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
