# Handoff prompt — workflow developer (post 2026-05-10 council review)

You are a senior implementor on **pgloom-engineering** (the autonomous
engineering orchestrator). Josh has just finished a council review of the
state of the system as of 2026-05-10. The review identified the workflow /
runtime / orchestration / qa items that need to land next.

## 0. Read first (in this order)

1. `docs/reports/council-review-2026-05-10.md` — full review. Read it end
   to end. Sections most relevant to you: §2.3 (QA verdict), §2.4 (ops),
   §3.3 (replan-from-milestone touch points), §3.4 (live-suite blockers),
   §5 (top recommendations).
2. `docs/prompts/command-center-dashboard.md` — the Command Center brief.
   Sections most relevant to you: §2 (migration note 010-016 already
   shipped once the abort-reason fix lands; council work starts at 017),
   §6 (operator interventions API and audit semantics),
   §8b (council schema migration 017 — you'll write the
   migration and the orchestrator-side persistence).
3. `docs/plans/engineering-orchestrator-port.md` — master plan. Read the
   "Phase 2 acceptance" and "Autonomy Contract" sections; everything you
   ship has to satisfy that contract.
4. `docs/prompts/qa-engineer-impl.md` — QA author/verify brief. The
   benchmark-visitor contract validator (item 1 below) lives in this
   surface.
5. `docs/prompts/planner-impl-and-review.md` — planner + reviewer brief.
   The replan-from-milestone semantics (item 4) require planner changes.

You should also skim the previous review at `docs/notes/token-economy.md`
for the historical context on cost telemetry and council pathologies, but
don't get stuck there — the council review supersedes it for current state.

## 1. Tighten qa.author's benchmark-visitor contract — close blocker family (a)

**Problem.** Run r40 (and earlier r-runs) keep failing with reviewer
verdict `coder_repair` because the generated benchmark fixture's method
references don't match the public `StoreVisitor` SAM (Single Abstract
Method) contract. The benchmark compiles only against an out-of-date
visitor signature. Reviewer correctly catches it; we lose the run.

**What to do.**

1. In `pgloom_engineering/qa_author_runtime.py` (or the closest
   benchmark-fixture surface — Read the file to find the right hook),
   add a contract validator that runs **after** qa.author has produced
   the benchmark Java file but **before** it emits its handoff to the
   implementer. The validator must:
   - parse the generated benchmark file's method-reference expressions
     (or, more simply, regex-extract them — `<ClassName>::<methodName>`)
     that target a `StoreVisitor`-typed parameter
   - resolve them against the *current* public `StoreVisitor` interface
     in the target project (in lvc-standard:
     `core/src/main/java/.../api/StoreVisitor.java`); compare arity and
     the simple types of each parameter
   - on mismatch, **fail the qa.author task** with
     `blocker_code = engineering.qa_semantic_quality_failed`, a structured
     finding code such as `qa_semantic_benchmark_visitor_signature_mismatch`,
     and an artifact that lists each offending method-reference + the expected
     signature. Do not introduce a new blocker code unless you also add it to
     workflow recovery config and tests.
2. Add a unit test in `tests/unit/test_qa_author_runtime.py` (or the
   closest existing test module) with a fixture pair: one passing benchmark
   (matches a sample StoreVisitor SAM) and one failing benchmark
   (deliberately wrong arity). The test must assert the validator passes
   the first and emits the structured blocker for the second.
3. Add an integration test that generates a benchmark against the actual
   lvc-standard `StoreVisitor` (you can copy the file into `tests/fixtures/`
   to keep the test hermetic) and verifies the validator catches the r40
   shape.

**Acceptance.** The next end-to-end run that would have hit r40's failure
shape now stops at qa.author with the structured blocker, and the planner
recovery loop gets a clean, machine-readable handoff instead of a
downstream cascade.

## 2. Pre-commit path-violation gate in the implementer — close blocker family (b)

**Problem.** Run r47 (matching r37's r-runs) failed because the implementer
wrote files under qa-owned paths. The handoff to reviewer was missing the
expected qa-owned files (because the implementer had touched them), so
reviewer + qa.verify.scrutiny + qa.verify.usertest all blocked with
`engineering.handoff_missing`. The current gate is post-hoc.

**What to do.**

1. Read `pgloom_engineering/roles/implementer.py` (or the equivalent
   surface that wraps the implementer's filesystem writes — find it via
   `Grep "implementation_path_violation"` to land on the existing check).
2. The current implementer runs through an external Codex CLI, so pgloom
   cannot intercept every Edit/Write/shell write inside the model process.
   Implement a feasible **pre-handoff diff gate** instead:
   - snapshot qa-owned paths before invoking the implementer
   - after the model returns but before verification, artifact capture, or
     handoff, diff the worktree
   - if any qa-owned path changed, immediately fail with
     `blocker_code = engineering.implementation_path_violation`
   - write an artifact listing every offending path and a before/after diff
     excerpt
   - restore/revert qa-owned path changes before downstream roles can consume
     the worktree, so the cascade cannot happen.
3. If true pre-write prevention is needed later, implement it as a separate
   sandbox/overlay-fs project: run Codex in a disposable copy or overlay,
   inspect the diff, then promote only allowed production files into the
   canonical worktree.
4. Unit test: a synthetic implementer run that mutates a qa-owned root must
   fail at the pre-handoff gate, must produce the violation artifact, and must
   leave no qa-owned mutation visible to reviewer / qa.verify.

**Acceptance.** The next run that would have hit r47's shape now stops
the implementer immediately with the structured blocker, no qa-owned
files are mutated, and the downstream cascade does not happen.

## 3. Instrument abort reasons — resolve r38-r57 abort opacity

**Problem.** 17 of 20 runs r38-r57 aborted or were interrupted before
producing `outcome.md`. The data alone can't distinguish operator-kill from
supervisor-timeout from a lifecycle bug. Some live interruptions do not set
`engineering_features.state = 'aborted'`; they leave tasks / worker runs
cancelled or abandoned while the feature row stays open. We need structured
termination reasons on the feature, task, worker-run, and eval-artifact
surfaces that actually record interruption.

**What to do.**

1. Add migration `016_abort_reason.sql`:
   ```sql
   alter table engineering_features
     add column if not exists abort_reason text,
     add column if not exists abort_detail text,
     add column if not exists aborted_at timestamptz;

   alter table tasks
     add column if not exists terminal_reason text,
     add column if not exists terminal_detail text;

   alter table engineering_worker_runs
     add column if not exists terminal_reason text,
     add column if not exists terminal_detail text;

   create index if not exists engineering_features_aborted_idx
     on engineering_features (state) where state = 'aborted';
   ```
2. Find every code path that transitions `engineering_features.state` to
   `'aborted'`. Use `Grep "state.*=.*aborted"` and
   `Grep "set_state\\|update_state"`. Each call site must pass a structured
   `abort_reason` from the enum:
   - `operator_kill` — explicit cancellation via CLI / Command Center
   - `supervisor_timeout` — wall-clock supervisor hit its limit
   - `lifecycle_error` — exception inside the orchestrator itself
   - `external_signal` — SIGTERM / SIGKILL from outside
   - `unknown` — unhandled fall-through (should be vanishingly rare; alert)
   plus a free-text `abort_detail`. Also update code paths that mark tasks
   `cancelled` / `abandoned` or worker runs `cancelled` to populate
   `terminal_reason` / `terminal_detail`.
3. The signal handler that catches SIGTERM (look in `workflow_driver.py`
   or the main entrypoint) must record `external_signal` before the
   process exits; do not rely on the default Python handler.
4. Update `scripts/pgloom-review.sh` section 0 to include feature
   `abort_reason` / `abort_detail` and task/worker-run terminal reasons when
   present.
5. Ensure the live eval harness writes an `outcome.md` or interruption
   artifact even when it stops before the workflow reaches a terminal feature
   state.
6. Unit tests for each transition path: each one writes the right reason.

**Acceptance.** Next time a run aborts or is interrupted, either
`engineering_features.abort_reason` is non-null or every cancelled /
abandoned task and worker run has `terminal_reason`; the eval output
directory contains an interruption outcome artifact; and Command Center can
show the reason on the feature header and task detail.

## 4. Implement replan-from-milestone consumption — wire the operator loop

**Problem.** `pgloom_engineering/cli.py:653 feature_replan_from_milestone`
writes the intervention row, but no code consumes it. Pause / resume work;
replan does nothing.

**What to do.** The Command Center brief §6 specifies the contract; you
implement the orchestrator side.

1. In `pgloom_engineering/workflow_driver.py`, add an interventions
   consumer that polls (or, better, LISTENs on `cc_events`) for new
   `replan_from_milestone` rows. On each one:
   - load the prior consolidated plan for the feature
   - mark all tasks at or after the milestone with `state = 'superseded'`
     (do not delete; we keep them for audit — see brief §6 replan
     semantics)
   - construct a planner task with `payload.baseline_plan` set to the
     prior consolidated plan and `payload.replan_from_milestone_id` set
     to the requested milestone id, plus `payload.frozen_prefix_task_ids`
     listing the task ids before the milestone
   - enqueue it through the normal planner dispatch path so all the
     existing gates fire
2. In `pgloom_engineering/roles/planner.py` (`PlannerHandler._handle_council`,
   approximately lines 80-107 — re-Read to confirm), accept the new
   payload fields and pass them into `PlannerCouncil.run`.
3. In `pgloom_engineering/planner/council.py` (`PlannerCouncil.run` ~125;
   `_council_reports` ~350), thread `baseline_plan` and
   `replan_from_milestone_id` into both consolidator and critic prompts.
4. Update `pgloom_engineering/planner/prompts/consolidator.md` and
   `panelist.md`: add an `INHERIT_BASELINE_MODE` section that tells the
   model the prior plan is a baseline and the frozen prefix must remain
   identical byte-for-byte, with the milestone id and task list to honour.
5. In `pgloom_engineering/planner/critic.py` (~line 729), add a new
   check_id `check_frozen_prefix_unchanged` that does an exact-string
   compare of the frozen prefix tasks in the new plan against the
   baseline. If any byte differs, the critic verdict is `reject` with a
   structured failure artifact pointing at the first mismatch.
6. In `pgloom_engineering/contract_store.py`, when `create_plan_contract`
   is called with a `replan_from_milestone_id` set, the new plan_contract
   row records the prior contract id in `payload.replaced_plan_contract_id`
   so the audit chain is queryable.
7. Add an integration test: a fake feature with a 3-milestone plan, an
   operator intervention to `replan_from_milestone` at milestone 2,
   asserts: (a) tasks before m2 stay in their prior `state`; (b) tasks at
   and after m2 become `state='superseded'`; (c) a new planner task is dispatched
   with the right payload; (d) a planner output that mutates the frozen
   prefix is rejected by the critic.

**Acceptance.** Operator can hit "Replan from milestone" in Command
Center, the CLI command becomes useful, and a deliberate frozen-prefix
mutation is rejected.

## 5. Council schema migration (017_councils.sql) — promote council to first-class

**Problem.** Today, planner council output is JSONB on
`engineering_plan_contracts.council_reports`. It does not generalise to
reviewer councils (or any future role's councils). Command Center brief
§8b specifies the normalised schema.

**What to do.**

1. Write migration `pgloom_engineering/db/schema/017_councils.sql`. Copy
   the schema from brief §8b verbatim (`engineering_councils` +
   `engineering_council_panelists` with the `panelist_kind` /
   `panelist_ordinal` shape, indexes, and FK back to
   `engineering_features` and `engineering_worker_runs`).
2. Add `council_run_id text references engineering_councils(id)` to
   `engineering_worker_runs`. Backfill is best-effort — leave NULL for
   pre-016 runs.
3. In `pgloom_engineering/planner/council.py`, on `PlannerCouncil.run`
   start, INSERT a `engineering_councils` row and capture its id; then
   for each panelist invocation, BEFORE invoking the model, INSERT a
   `engineering_council_panelists` row in `running` state and set the
   `council_run_id` on the corresponding `engineering_worker_runs` row
   when it's created. After the panelist returns, UPDATE the panelist
   row with the verdict, vote, totals, and timestamps.
4. Add the matching NOTIFY triggers (`council.update`,
   `council_panelist.update`) — refer to brief §4 for the payload
   schema. The triggers go in the same migration or a sibling
   `018_council_notify.sql`; pick one and document it in the migration
   header.
5. Build the read-side legacy adapter (brief §8b "Legacy councils"):
   project pre-016 plan-contract `council_reports` JSONB through the
   same API shape with `legacy: true` set. Synthesise stable ids of
   the form `council_legacy_<plan_contract_id>_<iter>`.
6. Tests: for the new path, a planner council run produces one
   `engineering_councils` row + N panelist rows with totals matching the
   sum of joined worker-runs. For the legacy path, an existing
   plan_contract with non-empty `council_reports` projects through the
   API with `legacy: true` and stable ids.
7. Coordinate with the UI developer (separate handoff) — they need
   `engineering_councils` to exist before they can wire the Council view
   live. Land the migration first, expose the API stubs in
   `command_center/routes/councils.py`, then they can build against it.

**Acceptance.** New planner councils land normalised; legacy councils
project; the UI Council view (built by the UI dev) renders both.

## 6. Order of work + verification

1. Brief fixes are already applied — start with the council review.
2. Items 1, 2, 3 are independent — pick whichever you have appetite for
   first. Items 1 and 2 unblock the live suite; item 3 unblocks
   diagnostics.
3. Item 4 depends on the brief being current (it is).
4. Item 5 should land before the UI developer picks up the Council view.
5. After each item, run `ruff check`, `mypy`, and the unit suite. Both
   repos are currently green; keep them green.
6. After items 1-3 ship, run a fresh live-role-suite against lvc-standard
   R-002 and post the new run id back to Josh.

## 7. Hand-back

For this session, land the fixes together and push directly to `main` once
the docs, implementation, migrations, and focused tests are complete. The
final hand-back must reference the council review sections addressed and
include the test commands run + their pass output.

If something in the brief or the review is wrong, push back. The brief is
not gospel — Josh corrects it when implementor work surfaces a real
problem.
