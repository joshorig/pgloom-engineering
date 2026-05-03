# Planner Council + Rubric Critic Completion

## Diff Stat

Tracked diff at completion:

```text
pgloom_engineering/cli.py           |  78 ++++++++++++++++-
pgloom_engineering/config.py        |   8 ++
pgloom_engineering/projects.py      |   8 ++
pgloom_engineering/roles/planner.py | 161 +++++++++++++++++++++++++++++++++++-
pyproject.toml                      |   2 +-
```

Additional new files:

```text
docs/prompts/
docs/reports/planner-impl-and-review-completion.md
pgloom_engineering/planner/
tests/integration/test_planner_role_gates.py
tests/unit/test_plan_dry_run_cli.py
tests/unit/test_planner_council.py
```

## Verification

```text
.venv/bin/ruff check pgloom_engineering tests scripts
All checks passed!
```

```text
.venv/bin/python -m mypy pgloom_engineering
Success: no issues found in 45 source files
```

```text
set -a; source .env; set +a; .venv/bin/python -m pytest -q
........................................                                 [100%]
```

## Produced R-002 Contract

The hermetic tests use a representative R-002-like `PlanContract` covering snapshot/restore,
CRC invariants, stale/invalid snapshot handling, partial journal failure handling, reviewer,
QA, and role-gated implementer dispatch. The real model-produced R-002 plan is intentionally
not generated in tests because the council is wired through scripted/fake providers.

## Deviations

- BRAID runtime was explicitly parked before implementation. The critic is a bounded rubric
  implementation, not a BRAID-compatible graph surface.
- `EngineeringCLIModelProvider` now records to pgloom's `model_usage` table and returns the
  inserted row id. Planner council proposal/verdict audit trails can link back to durable usage
  rows, and planner Token Savior rows link to the relevant panelist/critic calls.
- The dry-run CLI uses configured CLI commands. The unit test verifies the exhausted JSON path
  rather than monkeypatching a full successful council.

## QA Contract Gaps

- Planner contracts now require the split QA DAG: `engineering.qa.author` before every
  implementer, `engineering.qa.verify` after every reviewer, and disjoint QA/source write paths.
- The QA handler should not start until Track D worktree/GitHub support, shared rubric extraction,
  and `engineering.feature_finalize` pre-gate semantics are available.
- The remaining QA brief work is implementation detail, not planner contract discovery: diff policy,
  sign-off persistence, resource locking, and full-app evidence capture.

## Self Review

The key risk is that the first council implementation is intentionally narrow: it provides the
typed loop, critic normalization, role-gated planner dispatch, and tests, but it does not yet
produce a live high-quality plan from real Claude/Codex profiles. The next hardening step is to
wire realistic project context extraction and scripted model fixtures for a full accepted R-002
plan, then use that same rubric interface for Reviewer panels.
