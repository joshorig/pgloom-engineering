# Workflow fixes status — 2026-05-10 council follow-up

This note records the workflow/runtime fixes that followed the
2026-05-10 council review. It replaces the original implementation handoff
for this batch; do not treat the old "what to do" wording as still open.

## Read first

1. `docs/reports/council-review-2026-05-10.md` — original findings. The
   relevant areas were QA verdict quality, ops observability,
   replan-from-milestone, live-suite blockers, and council visibility.
2. `docs/prompts/command-center-dashboard.md` — Command Center contract,
   including operator interventions and council persistence.
3. `docs/plans/engineering-orchestrator-port.md` — autonomy contract and
   phase-2 acceptance.
4. `docs/prompts/qa-engineer-impl.md` — QA author/verify expectations.
5. `docs/prompts/planner-impl-and-review.md` — planner, reviewer, and
   recovery expectations.

## Landed fixes

### QA benchmark StoreVisitor signature gate

Status: landed.

The QA semantic review path now checks generated benchmark fixtures against
the current public `StoreVisitor` callback signature. Mismatches produce
`qa_semantic_benchmark_visitor_signature_mismatch` under
`engineering.qa_semantic_quality_failed`, so recovery receives a structured
QA-author blocker instead of a downstream reviewer cascade.

Primary surfaces:

- `pgloom_engineering/qa_semantic_review.py`
- `pgloom_engineering/qa_author_runtime.py`
- `tests/unit/test_qa_semantic_review.py`

### Implementer QA-owned path restoration

Status: landed.

The implementer path gate now treats QA-owned path mutations as
`engineering.implementation_path_violation` and restores the QA-owned
surface before downstream reviewer or validator roles can consume the
worktree. This is a pre-handoff containment gate; true pre-write prevention
would require a later sandbox or overlay design.

Primary surfaces:

- `pgloom_engineering/roles/implementer.py`
- `pgloom_engineering/workflow_driver.py`
- `pgloom_engineering/roles/planner.py`
- `tests/unit/test_implementer.py`
- `tests/unit/test_workflow_driver.py`

### Abort and terminal reasons

Status: landed.

Migration `016_abort_reason.sql` adds structured termination fields on
features, tasks, and worker runs. Runtime paths populate feature
`abort_reason` / `abort_detail` and task or worker `terminal_reason` /
`terminal_detail` for cancellation, lifecycle errors, and interrupted runs.
`scripts/pgloom-review.sh` includes these fields for run analysis.

Primary surfaces:

- `pgloom_engineering/db/schema/016_abort_reason.sql`
- `pgloom_engineering/features.py`
- `pgloom_engineering/contract_store.py`
- `pgloom_engineering/worker.py`
- `pgloom_engineering/workflow_driver.py`
- `scripts/pgloom-review.sh`

### Replan from milestone

Status: landed.

The workflow driver now consumes `replan_from_milestone` operator
interventions. It preserves tasks before the requested milestone,
supersedes tasks at and after the milestone, and enqueues a planner task
with `baseline_plan`, `replan_from_milestone_id`, and frozen-prefix task
ids. Planner council prompts and the critic carry baseline mode, and the
critic rejects mutations to the frozen prefix.

Primary surfaces:

- `pgloom_engineering/workflow_driver.py`
- `pgloom_engineering/roles/planner.py`
- `pgloom_engineering/planner/council.py`
- `pgloom_engineering/planner/panelist.py`
- `pgloom_engineering/planner/consolidator.py`
- `pgloom_engineering/planner/critic.py`
- `pgloom_engineering/contract_store.py`
- `tests/unit/test_workflow_driver.py`
- `tests/unit/test_planner_council.py`

### First-class council persistence

Status: landed.

Migration `017_councils.sql` promotes councils to normalized tables:
`engineering_councils` and `engineering_council_panelists`, with
`engineering_worker_runs.council_run_id` for linkage and Command Center
NOTIFY triggers. The store/API expose both live normalized councils and
legacy plan-contract `council_reports` through a stable read shape.

Planner council persistence must call `connect(None)` when no explicit DB
URL is supplied so runtime env configuration is honored; this was fixed
after the initial migration.

Primary surfaces:

- `pgloom_engineering/db/schema/017_councils.sql`
- `pgloom_engineering/contract_store.py`
- `pgloom_engineering/planner/council.py`
- `pgloom_engineering/command_center/store.py`
- `pgloom_engineering/command_center/routes/councils.py`
- `tests/unit/test_command_center.py`
- `tests/unit/test_planner_council.py`

## Live follow-up required

The next fresh full-orchestration run started after these changes should be
used to verify the data path end to end:

1. Planner councils create `engineering_councils` and
   `engineering_council_panelists` rows while the planner runs.
2. `engineering_worker_runs.council_run_id` links planner worker runs to
   the council row where available.
3. QA benchmark visitor signature mismatches, if they recur, block at
   QA author with the structured semantic finding.
4. Implementer QA-owned path mutations, if they recur, stop at
   `engineering.implementation_path_violation` without cascading into
   reviewer or QA verify handoff failures.
5. Interrupted or aborted runs leave feature, task, worker, or eval-output
   terminal evidence instead of opaque missing outcomes.
6. `replan_from_milestone` can be exercised from CLI or Command Center and
   produces a planner task with baseline/frozen-prefix payload.

R61 (`wf_af598a20167542fcb6431caa76e57af7`) started before the council DB
URL persistence fix and before the variant-gate planner prompt repair. Do
not use R61 as evidence for those two fixes. Use R62 or later.

## Validation run for this batch

The full local suite was green after the workflow and council fixes:

```bash
set -a; source .env; set +a; uv run pgloom-engineering db migrate
uv run --extra dev ruff check pgloom_engineering tests
uv run --extra dev mypy pgloom_engineering tests/unit/test_command_center.py tests/unit/test_workflow_driver.py tests/unit/test_planner_council.py
uv run --extra dev pytest -q
```

GitHub Actions were green on `main` for the pushed workflow/council commits.

## Next engineering focus

Do not reopen broad workflow plumbing unless fresh evidence points there.
The current priority is live-eval convergence and output quality. R66
(`wf_5f7e45d95e4649a684a8639db497aaf8`) reached end-to-end completion, so
accepted artifacts are now evidence for production-grade review gaps, not only
orchestration progress.

## Command Center persistence follow-up

Status: active follow-up. Most schema/API surfaces have landed, but keep
patching producer persistence gaps as fresh live runs expose missing facts.

The Command Center should render persisted workflow facts, not inferred or
speculative UI state. Keep this work below the workflow semantics layer:
persist values that already exist in worker, model, task, artifact, handoff,
or validation objects, and avoid changing dispatch or recovery behavior unless
the change is required to store already-known data.

Landed behavior to preserve and verify:

- Codex-backed `model_usage` rows must store non-zero cost when token usage is
  known. `engineering_worker_runs.cost_usd` rolls up the same calculated cost.
- Worker runs carry `model_provider`, `model`, `model_profile`, and
  `reasoning_level` from recorded model usage when the model provider reports
  those values.
- Worker timing splits use persisted queue, lease, model, verification, and
  blocked durations where those values are available from task, subprocess, or
  command evidence.
- Task milestone membership is persisted on task contracts through first-class
  `milestone_id` and `task_slice_id` columns so the DAG can expose milestone
  progression without reconstructing it from nested payloads.
- User-test slot state is exposed from persisted `slots`, `tasks`, and
  `resource_locks` rows. The UI/API must not invent slot occupancy.
- Artifact rows expose kind, display name/path, size, source command, source
  worker run, and evidence linkage where the producer reported it.
- Handoffs persist concise `title` and `summary` display fields at creation
  time while keeping the full contract/objective intact.
- Scrutiny and user-test QA signoffs persist validator type, verdict, result
  contract, validation evidence, artifact ids, and metadata in the same table
  shape.

Current fixes to keep narrow:

- Codex usage rows that report `total_cost_usd=0` must be repriced through the
  same canonical Codex formula used by Command Center aggregation and worker-run
  rollups.
- Artifact metadata should be enriched from real producer evidence. If QA
  validation evidence names `evidence_id` and `artifact_ids`, persist that
  evidence linkage onto the artifact row; do not invent labels or evidence.

Validation expectations for future changes in this area:

```bash
set -a; source .env; set +a; uv run pgloom-engineering db migrate
uv run --extra dev ruff check pgloom_engineering/command_center tests/unit/test_command_center.py
uv run --extra dev mypy pgloom_engineering/command_center tests/unit/test_command_center.py
uv run --extra dev pytest tests/unit/test_command_center.py -q
```

If a future live run shows missing display values, fix the producing
persistence path first. Keep UI fallbacks as presentation fallbacks only, not as
the source of truth.

Primary surfaces:

- `pgloom_engineering/model_provider.py`
- `pgloom_engineering/contract_store.py`
- `pgloom_engineering/worker.py`
- `pgloom_engineering/command_center/store.py`
- `pgloom_engineering/db/schema/009_qa_signoffs.sql`
- `pgloom_engineering/db/schema/011_command_center_persistence.sql`
- `pgloom_engineering/db/schema/014_codex_cost_backfill.sql`
- `pgloom_engineering/db/schema/015_worker_run_model_usage_sync.sql`
- `tests/unit/test_command_center.py`
- `tests/unit/test_worker.py`

R66 follow-up:

- Keep the per-feature validation shape: feature-scoped lint/style,
  build/compile, feature tests, direct benchmark smoke, then model-driven
  user-test. Do not substitute broad regression scripts for feature validation.
- QA may add feature-specific `RangeScanBenchmark` smoke thresholds, including
  a realistic allocation noise margin for the new benchmark.
- QA must not relax unrelated existing `CiSmokeBenchmark` allocation thresholds
  to make a new feature pass. That weakens established project gates and should
  fail semantic QA review before implementer or reviewer consume the handoff.
- Treat accepted-run artifacts as review inputs. If an end-to-end run passes
  but changes unrelated gates, add deterministic semantic review coverage so
  the next run rejects the same class of drift.

1. Inspect accepted planner and QA-author artifacts from the current
   lvc-standard run, not only contracts.
2. Confirm per-feature verification avoids broad full regression and uses
   feature-specific lint/build/tests plus benchmark smoke before user-test.
3. Verify user-test remains model-driven and records evidence.
4. Watch for repeated corrective loops that regenerate broad slices instead
   of narrow recovery work.
5. Continue measuring token efficiency, especially implementer context
   growth and whether context capsules, Token Savior recall, and pgloom
   memory reduce input tokens without reducing implementation quality.
