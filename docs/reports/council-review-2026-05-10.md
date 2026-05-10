# Council review — 2026-05-10

> Scope: code changes in pgloom and pgloom-engineering since the r37 review;
> Command Center brief and shipped subsystem; live runs r38 through r57.
> Sources: git log of both repos; on-disk inspection of `pgloom_engineering/`
> and `command_center/web/`; brief at `docs/prompts/command-center-dashboard.md`;
> 52 worktrees under `lvc-standard/.local/worktrees/`; per-suite outcome
> evidence in `docs/reports/live-role-suite-2026-05-10-*/`; gates run from
> sandbox.

## TL;DR

- **Code health:** both repos green on ruff, mypy, and pytest (pgloom 34/27,
  pgloom-engineering 345/24 skipped DB-dependent). Quality bar is not
  slipping under the high commit velocity.
- **Velocity:** ~90 commits in pgloom-engineering since 2026-05-03, dominated
  by Command Center going from sketch to v1 (full backend + React + Playwright
  e2e), Codex cost telemetry hardening, and QA-author/verify autonomy work.
- **Command Center status:** the brief I just wrote reads as greenfield, but
  much of it is already shipped. Migrations 010-015 exist and the React app
  is running. The brief is therefore best read as a *delta + missing-piece*
  spec for council view, replan-from-milestone consumption, and
  per-task/per-council surfaces, plus a small list of fixes against drift it
  introduced.
- **Live runs r38-r57:** 20 new attempts, only 3 produced any artifacts; 17
  were aborted before outcome.md. **No clean qa.verify success.** The two
  blocker signatures from r37 are still live (reviewer rejection on bench
  fixture mismatch; implementer path violation cascading to handoff_missing).
- **New operational signal:** 17/20 runs aborted before producing an outcome.
  Either the suite harness is being interrupted (manual cancel, supervisor
  timeout), or something in the lifecycle is failing silently before artifact
  capture. Either way, signal-to-noise on the suite has gotten worse, not
  better.

---

## 1. What's new since the r37 review

### 1.1 Repo state

- pgloom: 3 commits since 2026-05-02. Notable: `a9fa5e3` adds a generic
  `pgloom.context` token-economy module (engineering already absorbed it).
  Otherwise quiet.
- pgloom-engineering: ~90 commits 2026-05-03 → 2026-05-10. Both repos clean
  on `main`, up to date with `origin/main`.

### 1.2 New prompt briefs

| File | Purpose (inferred) |
|---|---|
| `autonomy-verification-handoff.md` | Typed-contract autonomy + handoff architecture |
| `codex-goal-autonomous-orchestrator.md` | Compact Codex goal pointing at the autonomy plan |
| `handoff-telemetry-and-evidence.md` | Run-level audit surface on `engineering_worker_runs` |
| `migrate-token-economy-to-pgloom-context.md` | Adopt `pgloom.context` primitives in engineering |
| `command-center-dashboard.md` | Implementor brief I wrote/updated this session |

### 1.3 New migrations (010-015 are already shipped)

| Migration | Purpose |
|---|---|
| `010_command_center_notify.sql` | NOTIFY triggers + `engineering_feature_intervention_state` view |
| `011_command_center_persistence.sql` | Adds `milestone_id`, `task_slice_id` to task contracts; `title`, `summary` to handoffs |
| `012_command_center_backfill.sql` | Backfill the above from `input_contract` JSON |
| `013_task_contract_membership_trigger.sql` | Trigger keeping membership in sync |
| `014_codex_cost_backfill.sql` | Recomputes `model_usage.cost_usd` for codex rows |
| `015_worker_run_model_usage_sync.sql` | Function rolls `model_usage` into worker runs on terminal status |

### 1.4 New runtime modules

- `pgloom_engineering/command_center/` — full subsystem: `app.py`, `auth.py`,
  `realtime.py`, `serializers.py`, `static.py`, `events.py`, `store.py`,
  `__main__.py`. React app at `web/` with Playwright e2e at
  `web/tests/e2e/command-center.spec.ts`.
- `pgloom_engineering/planner/production_grade.py` — production-grade blocker
  gate (commit `18f5e30`). Tests under `tests/unit/test_production_grade.py`.
- `pgloom_engineering/qa_author_runtime.py`, `qa_runtime.py`,
  `qa_semantic_review.py` — heavy expansion. QA stays as top-level modules
  (no `qa/` package).
- `pgloom_engineering/autonomy_eval.py` — autonomous orchestration eval loop.
- No `recovery/` package; recovery logic lives inline in roles + qa modules.

### 1.5 Hotspots (HEAD~30 churn)

`tests/unit/` 25%, `pgloom_engineering/` top-level 14%,
`command_center/web/` 9%, `roles/` 7%, `command_center/` 7%,
`db/schema/` and `planner/` ~3.5% each. Test churn outpacing source churn
is healthy.

---

## 2. Council voices

### 2.1 Runtime — verdict: green with one caveat

The QA-author-only worktree-persistence model (orchestrator prunes
implementer/reviewer/qa-verify worktrees after completion, surfacing their
artifacts only via `worktree.diff` / `file-snapshots.json` / `outcome.md`)
is consistent with the design. Telemetry through `engineering_worker_runs`
is the right surface for ex-post review. **Caveat:** that means an
operator opening Command Center on a finished workflow has no live
filesystem to inspect, only the diff artifacts. Worth confirming Command
Center's artifact gallery (Task view §8a §8) loads `worktree.diff` + the
file-snapshots payload as first-class artifact kinds.

### 2.2 DB / schema — verdict: yellow, two real bugs in the brief

- **Column-name drift.** Brief uses `queue_seconds` and `lease_seconds`;
  reality (`008_autonomous_runs.sql`) is `queued_seconds` and
  `leased_seconds`. Frontend code already uses the correct names. Brief
  must be patched before it goes to a fresh implementer, or someone will
  copy the wrong identifiers.
- **Cost-unit drift inside the brief.** §5 DAG endpoint sample JSON uses
  `cost_usd_cents`. §9 locked `usd_micros`. Rename the §5 sample.
- **Migration numbering collision.** Brief still calls the NOTIFY migration
  "010" and the council migration "011_councils.sql". On-disk has 010-015
  already. Renumber new work to 016+.
- **Pause-state view duplication.** Brief proposes a correlated-subquery
  view (functionally correct but O(N²)). Shipped `010_command_center_notify.sql`
  ships a better `distinct on (feature_id) order by created_at desc, id desc`
  form. Drop the brief's view and reference the shipped one.
- **Aggregate cost-unit footgun.** `cumulative_cost_usd` and
  `model_usage.cost_usd` are `numeric(12,6)`, not micros. The §9 rule says
  "sum micros server-side", which is right per row; for SQL aggregates,
  `sum(round(cost_usd * 1000000)::bigint)` inside Postgres rather than
  fetching floats and summing in Python. Add this footnote.
- **`feature.update` event from pause/resume.** Brief says pause/resume
  emits `feature.update`. But §6 also says interventions don't mutate
  `engineering_features` — so the trigger doesn't fire. Either emit a
  synthetic event from the API layer when an intervention row is inserted,
  or drop the `feature.update` claim and let clients react to
  `intervention.added`.

### 2.3 QA — verdict: red on outcomes, yellow on signal-to-noise

- 20 new attempts since r37, 0 reached qa.verify cleanly.
- Two stable blocker families:
  - (a) `engineering.review_rejected` with `coder_repair` — bench fixture
    method-reference signature mismatch against the public `StoreVisitor`
    SAM contract (r40).
  - (b) `engineering.implementation_path_violation` cascading to
    `engineering.handoff_missing` for reviewer + qa.verify.scrutiny +
    qa.verify.usertest (r47, matching r37 pattern).
- The `qa.author` task itself is reaching `done` consistently. The wall is
  upstream (implementer scope discipline) and downstream (reviewer
  rejection on signature compat).
- **17/20 aborted before producing outcome.md.** This is new and concerning.
  Could be: (i) you're killing the suite manually because you can see it's
  going wrong; (ii) supervisor timeout firing; (iii) something in the
  lifecycle aborting before artifact capture. The data alone doesn't
  distinguish (i) from (iii). Worth instrumenting: log abort reason on
  every `engineering_features.state = 'aborted'` transition.

### 2.4 Ops / telemetry — verdict: green; codex cost finally honest

The codex cost backfill (`014_codex_cost_backfill.sql`) plus the
`engineering_worker_run_model_usage_sync` function (`015`) close the
double-count bug and give per-call cost truth on terminal status. Combined
with the production-grade blocker gate and provider-limit classification
(`1da9785`), the planner now refuses to dispatch when a provider is rate-
or quota-limited. This is the kind of operational hygiene that compounds.

### 2.5 Command Center frontend / security — verdict: yellow; auth gaps

Loopback enforcement is necessary but not sufficient for v1:

- **WS Origin not validated.** A page loaded from `http://evil.example`
  *can* connect WebSockets to `127.0.0.1:<port>` because the browser is
  the loopback peer. Add an Origin allowlist (`http://localhost:<port>`,
  `http://127.0.0.1:<port>`).
- **DNS rebinding.** Validate `Host` header against `127.0.0.1` /
  `localhost` to prevent a tricked browser from talking to your local API
  through a rebound DNS name.
- **CORS.** Brief is silent. Default FastAPI denies cross-origin; document
  that explicitly so the implementer doesn't slap on `allow_origins=["*"]`.

These are five-line fixes, not architecture. But for "v1 dev console" they
matter because dev consoles routinely show secrets, evidence, and worktree
contents.

### 2.6 Council abstraction — verdict: yellow; legacy adapter is degraded

The proposed `engineering_councils` + `engineering_council_panelists`
schema (brief §8b) is the right shape. The read-side adapter projecting
legacy `engineering_plan_contracts.council_reports` JSONB through the new
API will work, but it loses:

- per-tile started_at / finished_at (legacy stores per iteration, not per
  panelist run)
- per-tile cost (cost is on `model_usage` joined via `model_usage_id`;
  splitting requires another join)
- council_id (must be synthesised, e.g.
  `council_legacy_<plan_contract_id>_<iter>`)
- `panelist_slot` (legacy distinguishes consolidator/critic structurally,
  not by name)
- `purpose` (must be inferred from PlanContract status)

Recommendation: render legacy councils with a "legacy" badge in the UI and
do not promise feature parity. New councils get full per-tile detail.

Also: the `panelist_slot` enum in the brief uses fixed `panelist_a` /
`panelist_b`. Today's planner has N configurable panelists. Use
`panelist:<n>` (ordinal) or store `role + ordinal`.

### 2.7 Code quality — verdict: green

ruff and mypy are clean across both repos. 345 unit tests in
pgloom-engineering, 34 in pgloom, 0 failures. Sandbox can't reach Postgres,
so the 51 DB-dependent tests are skipped — Josh, run those locally to keep
the integration surface honest.

---

## 3. What's flagged for follow-up

### 3.1 Brief patches (5-line fixes)

1. `queue_seconds` → `queued_seconds`, `lease_seconds` → `leased_seconds`
   throughout `command-center-dashboard.md`.
2. §5 DAG sample JSON: `cost_usd_cents` → `cost_usd_micros`.
3. Renumber "Migration 010" and "011_councils.sql" mentions to the next
   free numbers (016+).
4. Drop the §6 prose pause-state view; reference shipped 010 view.
5. Add the SQL-aggregate footnote to §9 cost-unit conventions.
6. Resolve `feature.update`-on-pause: synthetic emission from API layer,
   not trigger-driven.
7. Council `panelist_slot` enum: drop `panelist_a/b`, use ordinal.

### 3.2 Brief additions

8. WS Origin allowlist in §3 auth.
9. Host-header validation in §3 (DNS-rebind defence).
10. CORS default-deny in §3.
11. Worktree-diff and file-snapshots as first-class artifact kinds in §8a
    artifact gallery.
12. Legacy-council "legacy" badge clause in §8b.
13. Acceptance test #11: `engineering_features.state = 'aborted'`
    transitions log a structured abort reason.

### 3.3 Replan-from-milestone — code work that doesn't exist yet

CLI surface exists (`pgloom_engineering/cli.py:653 feature_replan_from_milestone`)
but only writes the intervention row. The recovery-worker handler that
consumes the intervention and enqueues a planner task does not exist.
Touch points for the implementer:

- `pgloom_engineering/workflow_driver.py` — add intervention consumption.
- `pgloom_engineering/roles/planner.py` (`PlannerHandler._handle_council`,
  ~lines 80-107) — accept `payload['baseline_plan']` and
  `payload['replan_from_milestone_id']`.
- `pgloom_engineering/planner/council.py` (`PlannerCouncil.run`, line 125;
  `_council_reports`, line 350) — thread baseline + frozen-prefix to
  consolidator/critic prompts.
- `pgloom_engineering/planner/prompts/consolidator.md` and `panelist.md` —
  new "INHERIT_BASELINE_MODE" section.
- `pgloom_engineering/planner/critic.py` (~line 729) — new check_id that
  rejects deltas mutating the frozen prefix.
- `pgloom_engineering/contract_store.py` (`create_plan_contract`) — mark
  superseded prior-plan tasks (`status = 'superseded'`) rather than delete.

### 3.4 Live-suite blockers (still red since r37)

- **Reviewer rejection family.** R-002 / R-003 bench fixtures keep producing
  method-reference signatures that don't match the public `StoreVisitor` SAM
  contract. Two ways to address: (a) tighten qa.author's contract for what
  benchmark visitors must look like (add a contract validator over the
  generated benchmark file's signature); or (b) change the public
  `StoreVisitor` SAM to be more lenient. (a) is cleaner.
- **Path-violation family.** Implementer touching qa-owned paths cascades to
  `handoff_missing` for the entire downstream. The implementer needs a
  pre-commit gate that refuses any write under qa-owned roots and blocks
  before producing artifacts. Pre-gate is cheaper than post-gate cascade.
- **Abort opacity.** Instrument abort reasons. A 17/20 abort rate without
  a structured reason field hides whether it's external (you killed it) or
  internal (lifecycle bug).

---

## 4. Council verdict

**Approved with revisions.** The week's code work is high-quality and
landing in the right places: cost telemetry truth, production-grade gating,
provider-limit classification, command-center subsystem, persistent task
contract membership. Tests, types, and lints are clean. The brief I wrote
this session has small but real drift that needs correcting before it
ships to an implementer (column names, cost units, migration numbering,
auth completeness).

The live suite is the elephant. The infrastructure is healthier than at
r17. The remaining failures are *correct* failures — the system is now
catching things it should catch — but no run has crossed the qa.verify
finish line. The two stable blocker families need a focused intervention,
not another round of orchestrator work. Pick one (recommend reviewer
rejection family first, since it's a contract-tightening fix on the
qa.author side and unblocks both R-002 and R-003) and close it before
adding more dashboard surface area.

---

## 5. Top recommendations, in order

1. **Fix brief drift now (1 hour).** Items §3.1 above. I can apply these
   on request.
2. **Tighten qa.author's benchmark-visitor contract (1-2 days).** Add a
   contract validator that checks the generated benchmark's method
   references resolve against the current public `StoreVisitor` SAM. Close
   the r40-shaped failure.
3. **Pre-commit path-violation gate in the implementer (half day).** Refuse
   any write under qa-owned roots before artifact capture. Close the r47
   cascade.
4. **Instrument abort reasons (1 hour).** Add a structured `abort_reason`
   column or evt to `engineering_features` state transitions. Make the
   17/20 abort rate analysable.
5. **Implement replan-from-milestone consumption (2-3 days).** Brief is
   ready; touch points listed in §3.3. Required to make the operator-pause
   loop actually do something useful.
6. **Add WS Origin / Host-header / CORS hardening to Command Center (half
   day).** Even for v1 dev-only, these are five-line fixes.
7. **Council schema migration (016) + read adapter for legacy
   council_reports (2-3 days).** Promote council to first-class entity
   before the reviewer council work assumes it.
