# Council review — 2026-05-11

> Scope: workflow + council fixes that landed since the 2026-05-10 review;
> live runs since r57. Sources: git log + diff against pgloom and
> pgloom-engineering (88 commits in pgloom-engineering, 0 in pgloom);
> `pgloom_engineering/db/schema/{016,017}` headers; in-tree code surfaces
> for each of the five fix areas; per-suite outcome evidence in
> `docs/reports/live-role-suite-2026-05-1[01]-*/`; gates run from sandbox.

## TL;DR

- **First real end-to-end PASS:** r66 reached `workflow.state=done` on
  lvc-standard R-003 with both qa.verify.scrutiny and qa.verify.usertest
  green. r74, r80, r81 followed. **All four passes are the same R-003
  feature**, so this is repeatable on one spec, not yet generalised.
- **All five claimed workflow fixes are present** — ruff/mypy clean, 439/25
  skipped pytest. Two of the five have honest deviations the status doc
  acknowledges; one (abort instrumentation) is half-done in a way the
  status doc does not flag.
- **New dominant failure shape:** `engineering.planner_council_exhausted`
  with the recovery loop spinning up 50 corrective-slice planners and no
  convergence cap (r109). This is a direct consequence of the new replan
  path going live and is the next blocker.
- **Throughput is worse than the status doc implies.** Of 61 run
  directories since r58, only 5 produced an outcome.md. The other 56 are
  bare case.json shells. Until `abort_reason` is actually populated by
  call-sites (the migration shipped, no caller writes it yet), we can't
  tell why.
- **Beyond the 5 fixes:** 80+ tightening commits dominate the churn (QA
  semantics, planner critic, recovery routing). Quality bar still high.
  One unmentioned new module (`role_gate_contracts.py`, 315 lines) and a
  Codex live-eval ergonomics burst worth a brief mention in the next
  status update.

---

## 1. What landed since 2026-05-10

### 1.1 Repo state

- pgloom: 0 new commits. Tree clean except an untracked `uv.lock`.
- pgloom-engineering: 88 commits, all on `main`, fully pushed. Tree clean.
  Stale local branch `codex/autonomous-qa-author-gates` is gone on remote,
  harmless.

### 1.2 Migrations

- `016_abort_reason.sql` — adds `abort_reason`, `abort_detail`,
  `aborted_at` to `engineering_features`; `terminal_reason`,
  `terminal_detail` to `tasks` and `engineering_worker_runs`; partial
  index on aborted features. **No CHECK constraint on the enum.**
- `017_councils.sql` — `engineering_councils` +
  `engineering_council_panelists` with `panelist_kind` /
  `panelist_ordinal` (matches brief), unique
  `(council_id, iteration, panelist_kind, panelist_ordinal)`,
  `engineering_worker_runs.council_run_id` FK, and the
  `cc_notify_councils` + `cc_notify_council_panelists` triggers. Extra
  benign `metadata jsonb` column.

Both arrived in commit `75d39bc Wire workflow persistence fixes`; a
follow-up `08e9d4f Persist planner councils with env database` fixed a
`connect(None)` bug.

### 1.3 Hotspots (top 10 files by lines changed)

```
1074  tests/unit/test_planner_handler.py
1000  tests/unit/test_qa_semantic_review.py
 905  docs/prompts/handoff-workflow-fixes-2026-05-10.md
 838  pgloom_engineering/workflow_driver.py
 680  pgloom_engineering/roles/planner.py
 642  tests/unit/test_workflow_driver.py
 621  pgloom_engineering/qa_semantic_review.py
 595  tests/unit/test_planner_council.py
 435  pgloom_engineering/planner/council.py
 409  pgloom_engineering/planner/critic.py
```

Test churn outpaces source churn. Healthy.

### 1.4 Beyond the 5 named fixes (not in status doc)

- **`pgloom_engineering/role_gate_contracts.py`** (315 lines) added in
  `80fe438 Add explicit role gate contracts`. Worth naming in the next
  status update — it's load-bearing for the role-gate split.
- **Codex live-eval ergonomics** — 4 commits around the bypass-flag,
  approval-policy ordering, and approval-prompt disable. Operational
  hygiene, but real for anyone running live evals with codex.
- **80+ QA-semantics tightening commits** — accept legitimate variants
  (`Accept QA non-matching prefix wording`, `Accept camel case prefix QA
  names`, `Accept range no-alias QA guards`), reject brittle ones
  (`Reject brittle range smoke thresholds`, `Reject payload-prefix range
  QA drift`, `Reject null receiver range API tests`). Status doc only
  names the StoreVisitor gate, but the actual qa.author quality work is
  much broader. Worth crediting.

### 1.5 Gates

- pgloom-engineering: ruff clean, mypy clean (78 source files), 439
  passed / 25 skipped (was 24; one new skip marker — not a regression).
- pgloom: ruff clean, mypy clean (66 source files), 34 passed / 27
  skipped.

---

## 2. Council voices

### 2.1 Runtime — verdict: yellow; new convergence runaway is the live concern

`engineering.planner_council_exhausted` is a real new failure shape (r109).
The recovery loop creates 50 corrective-slice planner tasks for the same
blocker code, each with `terminal_reason: "workflow_recovery_replan"`,
because the planner panelists return empty bodies (`"raw_response": "",
"parse_error": "no JSON object found in model response"`). The recovery
path has no `same_blocker_recovery_count` cap before it gives up and
escalates to operator. r109 hit `same_blocker_recovery_count: 49` before
the snapshot.

This isn't the recovery system being broken — it's working exactly as
designed and surfacing a missing termination clause. The convergence cap
is a direct, additive fix.

### 2.2 DB / schema — verdict: yellow; abort_reason wired but unwritten

- 016 columns exist, `scripts/pgloom-review.sh` selects them, but
  `engineering_features.abort_reason` is **never populated** at any
  call-site in any of the 5 outcome-bearing runs. `features.py:81`
  accepts the kwarg; grep across the repo finds zero callers passing it.
- The only terminal-reason value ever written is
  `terminal_reason="lifecycle_error"` at `worker.py:137` and
  `terminal_reason=blocker_code or "handler_retry"` at `worker.py:208`.
  None of `operator_kill`, `supervisor_timeout`, `external_signal`, or
  `unknown` is ever emitted.
- No CHECK constraint on the enum either; freeform text. The brief enum
  is documented but unenforced.
- 017 council schema is correct and matches the brief (panelist_kind +
  panelist_ordinal, unique constraint, NOTIFY triggers).

### 2.3 QA / runs — verdict: yellow with green sprouts

**Passes:** r66, r74, r80, r81 — all R-003 range query on lvc-standard.
qa.verify.scrutiny + qa.verify.usertest both pass on r66 with
`compileJava`, two `:test --tests` invocations, and a `jmhSmokeCheck` all
exit 0. All 8 tasks `done`, 12 handoffs.

**Cost trajectory** (implementer phase, all R-003, runs=2):

| run | input M | $$$ | seconds |
|---|---|---|---|
| r66 | 1.92 | 2.09 | 565 |
| r74 | 2.13 | 2.22 | 366 |
| r80 | 1.34 | 1.57 | 302 |
| r81 | 1.47 | 1.68 | 298 |

Real downward drift on input tokens and elapsed; cost halved isn't
quite right but ~25% reduction is plausible.

**Throughput problem.** 56 of 61 run directories since r58 are bare
case.json. The status doc reads as if "R62 onward is valid"; on disk it's
"R66 / R74 / R80 / R81 / R109 are the only runs that wrote evidence."
Until `abort_reason` actually gets populated (above), we can't see why.

**Status doc evidence drift.** R90 and R92 are cited for cost claims;
neither produced an outcome.md, both are 830-byte case.json shells. The
real cost reduction is at r80/r81 — point status updates there.

### 2.4 Ops / telemetry — verdict: green where wired

- `worker_runs.council_run_id` is being populated. r66/r74/r80/r81 each
  reference a single distinct council id; r109 has 50 distinct council
  ids (each recovery attempt spawned its own). The persistence side of
  the council work is live and queryable — Command Center brief §8b
  acceptance is reachable.
- All outcome-bearing runs preserve the artifact gallery (`worktree.diff`,
  `file-snapshots.json`, `artifacts.json`, `telemetry-summary.json`,
  `runner-summary.json`, `outcome.md`). The Task view's first-class
  worktree_diff/file_snapshots clause (§8a in the brief) is well-aimed —
  these are the *only* surviving evidence for non-persisted worktrees, as
  predicted.

### 2.5 Implementer / containment — verdict: yellow; honest deviation

The implementer "pre-commit path-violation gate" landed as a *post-hoc
restoration* gate: `provider.invoke()` runs, then violations are checked,
then `_restore_paths` rolls back violating files before returning
`blocked`. The status doc is honest about this. Effect on cascading
failures: closed (we no longer see r47-shaped handoff_missing). Effect on
token cost: not closed — the LLM still consumes tokens producing
forbidden content that gets discarded. Acceptable v1, worth flagging on
the roadmap.

### 2.6 Replan-from-milestone — verdict: green-with-minor-deviation

All 7 §3.3 touch points from the previous review are present. The only
deviation is bookkeeping: task supersession lives in
`workflow_driver.py:499-528` rather than in `contract_store.py` as the
review prescribed. The plan-contract row's `status='superseded'` is set
in `contract_store.py:59`; task contracts and tasks are flipped by the
intervention handler. Functionally equivalent and arguably better-placed.
The critic's `check_frozen_prefix_unchanged` rejects mutations to the
frozen prefix as specified, with structured codes
`frozen_prefix_slice_missing` / `frozen_prefix_slice_changed`. Tests at
`test_workflow_driver.py:1526-1591` and
`test_planner_council.py:1614-1647` cover both paths.

### 2.7 Council persistence + legacy adapter — verdict: green-with-minor-deviation

Migration shape matches the brief. `contract_store` exposes
`create_council_run`, `finish_council_run`, `record_council_panelist`
with `panelist_kind`/`panelist_ordinal` first-class. `planner/council.py`
calls them on each panelist/consolidator/critic invocation. Legacy
adapter is in `command_center/store.py:583-678`, synthesises stable
`council_legacy_<plan_id>_<idx>` ids, marks `legacy: True`, infers
`purpose` from `plan.active`.

Two minor deviations:
- The brief named `pgloom_engineering/command_center/routes/councils.py`;
  the actual code has council endpoints inline in `command_center/app.py`
  (no `routes/` package). Functionally fine; cosmetic divergence the UI
  developer should be told about so they don't hunt for the file.
- Legacy detail returns empty arrays for panelists/worker_runs rather
  than the brief's "unavailable (legacy)" string placeholders. Render
  side concern, not data side.

### 2.8 Generalisation gap — verdict: yellow

R66/r74/r80/r81 all implement R-003 range query on lvc-standard with
near-identical diffs (`LvcStore.java`, `Single/DoubleDirectStore.java`,
`Single/DoubleMmapStore.java`, `benchmarks/build.gradle` +
`RangeScanBenchmark`, `docs/Stores.md`, `repo-memory/ROADMAP.md`). This
is not "the system passes end to end"; it is "the system passes one spec
end to end." Worth distinguishing in the next status update — the
celebratory framing skips this caveat.

### 2.9 Code quality — verdict: green

ruff and mypy clean across both repos. 439 unit tests in
pgloom-engineering, 34 in pgloom, 0 failures. 25/27 DB-dependent skips
(sandbox can't reach Postgres). One additional skip vs the previous
review — likely a new skip marker, not a regression.

---

## 3. What's flagged for follow-up

### 3.1 The next blocker — planner_council_exhausted runaway (P0)

R109 spawned 50 corrective-slice planners for the same blocker code.
Recovery loop has no `max_same_blocker_recovery_count` cap before
escalating to operator. Touch points:

- `pgloom_engineering/workflow_driver.py` — find the `same_blocker_recovery_count`
  increment site (likely in the handler that emits `corrective_slice`
  recoveries) and add a hard cap (suggest 3 for `engineering.planner_council_exhausted`,
  configurable per blocker code).
- When the cap is hit, transition the feature to `state='blocked'` with
  `blocker_code='engineering.recovery_loop_exhausted'` and a structured
  artifact listing each prior corrective slice + outcome. This becomes
  an operator-intervention point (the existing `replan_from_milestone`
  or `add_orchestrator_note` action surfaces).
- Diagnose the underlying empty-response cause. r109 panelists returned
  `"raw_response": ""` with `parse_error: "no JSON object found in model
  response"`. Likely either (a) prompt overflow truncating the response,
  (b) provider rate-limit returning empty, or (c) refusal wrapped in
  empty body. Add panelist-level logging of the raw provider response
  shape (status code, headers, body bytes) before the parser runs.

### 3.2 Abort instrumentation half-done (P1)

Migration shipped, no caller writes `abort_reason`. To make the
brief-acceptance #11 reachable:

- Add a CHECK constraint on the enum:
  `check (abort_reason is null or abort_reason in ('operator_kill','supervisor_timeout','lifecycle_error','external_signal','unknown'))`
  in a new `018_abort_reason_enum.sql` migration. Cheap correctness lock.
- Audit every `update_feature_state(state='aborted', ...)` call (grep
  finds zero passing `abort_reason` today). Each one passes a structured
  reason from the enum.
- The signal handler that catches SIGTERM (in workflow_driver.py or the
  CLI entrypoint) must record `external_signal` before the process
  exits.
- The supervisor timeout site (find it) records `supervisor_timeout`.
- The CLI cancellation paths record `operator_kill`.
- Otherwise the diagnostic intent of the column remains aspirational.

### 3.3 Throughput diagnostic (P1)

56/61 run directories since r58 are bare `case.json`. Either the
supervisor / harness is killing runs before subprocess output, the
runner is being cancelled at startup, or runs are simply not finishing.
The instrumentation that would tell us is item 3.2 — fix that and this
diagnoses itself.

If you're explicitly killing a lot of these runs (likely some you knew
were going wrong), please instrument that path too — the harness should
record an `operator_kill` reason on the way out.

### 3.4 Status-doc evidence drift (P2)

The doc references R90 and R92 for cost-trajectory claims; both are
830-byte case.json shells with no telemetry. Real evidence is at
r80/r81. Either:

- Re-run R90/R92 to capture actual evidence, or
- Update the status doc to reference r80/r81 (the actual cost-reduction
  signal).

### 3.5 Generalisation evidence (P2)

Pick a non-R-003 spec and run it end to end. The four passes prove
"system can pass R-003" not "system can pass arbitrary specs". A
second-feature pass would meaningfully change the readiness picture; a
second-feature failure would tell you exactly which surfaces are
R-003-overfit.

### 3.6 Cosmetic deviations (P3)

- `command_center/routes/councils.py` was promised by the brief, code
  lives inline in `command_center/app.py`. Tell the UI developer where
  to actually find the council endpoints so they don't hunt.
- Legacy-council detail returns empty arrays where the brief asked for
  "unavailable (legacy)" string placeholders. Render-side fix when the
  UI dev gets to the Council view.

### 3.7 Implementer pre-write gate (P3, roadmap)

Status doc honest: post-hoc restoration is shipped, true pre-write
prevention requires a sandbox or overlay design. Cascade is closed; token
cost on rejected attempts is not. Park on the roadmap; not urgent.

---

## 4. Council verdict

**Approved.** The five workflow items landed substantively and with
honest deviations. R66's end-to-end pass is a real milestone — first
time the orchestrator has cleanly threaded planner → design → qa.author
→ implement → review → qa.verify.scrutiny → qa.verify.usertest with
both validators green. R74/r80/r81 reproduced it; r80/r81 show real cost
reduction. The remaining work is now mostly about **closing the new
recovery-runaway shape (3.1)**, **finishing the abort instrumentation
that the migration set up but no caller exercises (3.2)**, and **proving
generalisation on a second spec (3.5)**.

Don't reopen broad workflow plumbing; the status doc is correct on that
stance. The shape of work is now incremental tightening + diagnostic
instrumentation, which is what we'd want at this stage.

---

## 5. Top recommendations, in order

1. **`max_same_blocker_recovery_count` cap on the recovery loop (½ day).**
   Stops r109-shaped runaways. Fail fast to operator-intervention with a
   structured artifact.
2. **Diagnose r109 empty-response cause (½ day).** Add panelist-level
   logging of raw provider response shape before parser runs. Could be
   prompt overflow, rate-limit, refusal, or genuine model failure;
   without raw bytes you can't tell.
3. **Wire `abort_reason` to actual call-sites + add CHECK constraint
   (½ day).** Migration is shipped; complete the round trip. Resolves
   the 56/61-bare-shell opacity and unblocks brief-acceptance #11.
4. **Run a non-R-003 spec end to end (1-2 days, depending on which
   spec).** Picks an R-002 or R-001 case from the lvc-standard backlog.
   First failure (or pass) on a different spec changes the picture.
5. **Update status doc with R80/R81 evidence (15 min).** Replace the
   R90/R92 references; add the cost-trajectory table.
6. **Fold the unmentioned changes into the next status doc (15 min).**
   `role_gate_contracts.py`, the Codex live-eval ergonomics, and the
   broad qa.author tightening cycle deserve credit + visibility.
7. **Cosmetic: move council endpoints to `routes/councils.py` (½ hour)
   or update the brief to point at `app.py` (5 min).** Pick whichever
   is cheaper. Do not block the UI developer on this.

---

## Appendix — verification posture

- Gates: ruff clean, mypy clean, 439/25 (pgloom-engineering), 34/27
  (pgloom). Status doc claim verified.
- 88 commits since 2026-05-10 in pgloom-engineering, 0 in pgloom. All
  pushed. Working trees clean.
- 5 outcome-bearing runs since r58 (r66, r74, r80, r81, r109); 56 bare
  case.json directories.
- Council ids appear on `worker_runs` rows in every outcome-bearing run.
  Council persistence is live.
- `abort_reason` column exists; zero call-sites populate it.
- Sandbox cannot reach Postgres; DB-dependent tests skipped (expected).
