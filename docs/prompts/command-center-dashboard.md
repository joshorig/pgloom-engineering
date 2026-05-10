# Implementor brief — Command Center dashboard and operator controls

> **Status: implementor brief.** Command Center is the operator control surface
> for autonomous engineering. It is not only a status page. It must show
> progress, evidence, cost, token economy, wall-clock bottlenecks, recovery,
> and allow audited interventions.
>
> **Locked decisions (Josh, 2026-05-09):**
> 1. **Stack:** React + frontend build (Vite). Backend serves JSON; the SPA is
>    its own build artifact.
> 2. **Realtime:** Postgres `LISTEN/NOTIFY` bridged to a single WebSocket per
>    client. No polling.
> 3. **Auth (v1):** Local only. Backend binds `127.0.0.1` and refuses
>    non-loopback peers. No login.
> 4. **DAG renderer:** Cytoscape.js (with `dagre` or `klay` layout).
> 5. **Replan-from-milestone semantics:** Planner inherits the prior
>    consolidated plan as a baseline; it does not start from scratch.
> 6. **Intervention audit:** Every operator click writes one row. Double-pause
>    writes two rows. Truthful audit > deduplicated audit.
> 7. **Cost unit:** `usd_micros` (bigint) on the wire and in all aggregates.
>    DB float columns are converted at the serializer boundary. Render-side
>    only.

## 1. Scope

Command Center renders, end to end:

- feature list and feature state
- task DAG with slice dependencies and milestone boundaries
- milestone signoff state
- handoff chain
- worker runs by role, phase, model, status, and attempt
- validation evidence from `qa.verify.scrutiny` and `qa.verify.usertest`
- artifact links for prompts, responses, logs, diffs, screenshots, traces, and
  reports
- model cost and token usage by feature, milestone, task, worker, role, model,
  phase, and validator type
- Token Savior and RTK / log-filter savings
- wall-clock bottlenecks, including queue, lease, model, verification, blocked,
  and user-test time
- recovery actions and corrective slices
- operator interventions (immutable audit log)

Use the name **Command Center** everywhere — package, route, page title, docs.

## 2. Architecture

```
                +-------------------------------------------+
                |  Postgres (engineering_* tables)          |
                |    NOTIFY 'cc_events', '<json payload>'   |
                +-----------------+-------------------------+
                                  |  asyncpg LISTEN
                                  v
+-------------------+   +---------+----------+   +-------------------+
|  React SPA (Vite) |<->|  FastAPI backend   |<->|  Operator action  |
|  Cytoscape DAG    |   |  /api/* (JSON)     |   |  endpoints        |
|  WebSocket client |   |  /ws  (broadcast)  |   |  insert into      |
|                   |   |  binds 127.0.0.1   |   |  engineering_*    |
+-------------------+   +--------------------+   +-------------------+
```

Backend layout (new package, lives inside `pgloom-engineering`):

```
pgloom_engineering/command_center/
  __init__.py
  app.py                  # FastAPI factory; binds 127.0.0.1 only
  auth.py                 # loopback check middleware
  realtime.py             # asyncpg LISTEN bridge -> websocket fan-out
  events.py               # NOTIFY channel + event schema
  routes/
    features.py           # /api/features, /api/features/{id}
    dag.py                # /api/features/{id}/dag
    runs.py               # /api/features/{id}/runs
    tasks.py              # /api/features/{id}/tasks/{task_id}/...
    councils.py           # /api/features/{id}/councils[/{council_id}]
    handoffs.py
    qa.py
    telemetry.py
    interventions.py      # POST handlers; insert + NOTIFY
  serializers.py          # row -> JSON (usd_micros, ISO timestamps)
  schema/
    010_command_center_notify.sql  # triggers + intervention indexes
```

Frontend layout (sibling tree, also versioned in the repo):

```
pgloom_engineering/command_center/web/
  package.json
  vite.config.ts
  index.html
  src/
    main.tsx
    api.ts                # typed fetch wrappers, SWR cache
    realtime.ts           # WebSocket client; reconnect + replay
    routes/
      FeaturesList.tsx
      FeatureOverview.tsx
      DagView.tsx          # Cytoscape canvas
      TaskView.tsx         # per-task detail page
      CouncilView.tsx      # per-council deliberation page
      CouncilsList.tsx     # all councils for a feature
      HandoffView.tsx
      ValidationView.tsx
      TelemetryView.tsx
      InterventionView.tsx
    components/
      RoleBadge.tsx
      StatusPill.tsx
      CostCell.tsx
      TokenCell.tsx
      WallClockBar.tsx
    lib/
      cytoscape-setup.ts   # styles, layout, event handlers
      money.ts             # all costs are usd_micros bigint client-side
```

Build artefact lives at `pgloom_engineering/command_center/web/dist/` and is
mounted by FastAPI as the SPA. Production build is a single `vite build`; dev
is `vite dev` with proxy to the API.

## 3. Auth and binding (v1)

- FastAPI app binds `host="127.0.0.1"`. Refuse to start if config requests a
  non-loopback host in v1.
- Middleware additionally inspects `request.client.host` and rejects anything
  that is not `127.0.0.1` or `::1` with HTTP 403. Belt and braces in case
  someone misconfigures a reverse proxy in v2.
- WebSocket handshake performs the same loopback check.
- No login, no cookies, no CSRF in v1. Document in the README that v1 is a
  developer console, not an internet-facing surface.
- v2 will add an env-var bearer token + reverse-proxy posture; out of scope
  here, but `auth.py` should expose a single check function that v2 can
  replace without touching routes.

## 4. Realtime via LISTEN / NOTIFY

### Channel

Single channel: `cc_events`. Payload is JSON, capped at 7500 bytes (Postgres
NOTIFY hard cap is 8000). Larger objects are referenced by ID; clients fetch
the row over `/api/...`.

```json
{
  "v": 1,
  "kind": "worker_run.update",
  "feature_id": "wf_03418122e944475492056b7264ce0772",
  "row_id": 1234,
  "fields": ["status", "cost_usd", "running_seconds"],
  "ts": "2026-05-09T12:34:56.789Z"
}
```

Event kinds (initial set):

- `feature.update` — `engineering_features` row mutation
- `worker_run.update` — `engineering_worker_runs` insert/update
- `handoff.update` — `engineering_handoffs` insert/update
- `qa.signoff` — `engineering_qa_signoffs` insert
- `intervention.added` — `engineering_operator_interventions` insert
- `recovery.update` — `engineering_recovery_actions` insert/update
- `plan.update` — `engineering_plan_contracts` insert/update
- `task.update` — `engineering_task_contracts` insert/update
- `council.update` — `engineering_councils` insert/update
- `council_panelist.update` — `engineering_council_panelists` insert/update

### Triggers

Migration `010_command_center_notify.sql` adds `AFTER INSERT OR UPDATE`
triggers on each table above. Each trigger constructs the minimal payload and
calls `pg_notify('cc_events', payload::text)`. Triggers must:

- compute `fields[]` by diffing OLD/NEW; on INSERT, list the salient columns
  only (not every column)
- never reference fields larger than 1KB (e.g. don't include diffs, payloads,
  long error blobs — those are fetched via REST)
- short-circuit and emit nothing when only `updated_at` changed

### Bridge

`realtime.py` opens one dedicated asyncpg connection at startup, runs `LISTEN
cc_events`, and fans messages out to every subscribed WebSocket. Each WS gets
its own asyncio queue with a bounded size (drop oldest on overflow + send a
`{"kind":"resync"}` hint so the client refetches).

### Client

`realtime.ts` exposes a single `subscribe(featureId, handler)`. The hook
performs initial REST fetches, then opens the WebSocket. On `resync` or
reconnect it refetches. Per-page React Query / SWR caches mutate from the
incoming events.

## 5. DAG view (Cytoscape)

- Renderer: `cytoscape@^3` + `cytoscape-dagre` for layered layout (LR, with
  milestone columns).
- Nodes: one per `engineering_task_contracts` row. Style by role (`planner`,
  `implementer`, `reviewer`, `qa.author`, `qa.verify.scrutiny`,
  `qa.verify.usertest`). Status pill via border colour + glyph.
- Edges: dependencies from the plan contract; milestone-locking edges drawn
  with a dashed amber stroke.
- Group/compound nodes per milestone, collapsible.
- Click a node → opens the Task view (`/feature/:id/task/:taskId`, see §8a).
  A lightweight side-panel preview is acceptable on hover, but the canonical
  detail surface is the dedicated Task view.
- Live updates: `task.update` events patch the node in place. New tasks
  trigger an incremental layout (don't relayout the world on every tick).
- Persist user view state (zoom, pan, expanded milestones) in `localStorage`
  keyed by feature id.

The dataset endpoint `/api/features/{id}/dag` returns:

```json
{
  "milestones": [{"id": "m1", "label": "Range API foundation", "task_ids": [...]}],
  "tasks": [{"id": "task_...", "role": "implementer", "status": "running",
             "depends_on": ["task_..."], "milestone_id": "m1",
             "last_run": {"started_at": "...", "cost_usd_cents": 312}}],
  "edges": [{"from": "task_a", "to": "task_b", "kind": "dep"|"milestone_lock"}]
}
```

## 6. Operator interventions

### Schema

The existing planning brief proposed `engineering_operator_interventions`.
Lock that schema in migration `010` (or whichever number is next):

```sql
create table engineering_operator_interventions (
  id            bigserial primary key,
  feature_id    text not null references engineering_features(id),
  actor         text not null,                 -- "operator:<email|hostuser>"
  action_type   text not null,                 -- see enum below
  payload       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

create index engineering_operator_interventions_feature_idx
  on engineering_operator_interventions (feature_id, created_at);

-- Optional but recommended: derive the "current" feature pause state from a
-- view rather than mutating engineering_features. The truth is the audit log.
create view engineering_feature_intervention_state as
select
  feature_id,
  bool_or(action_type = 'pause_feature' and not exists (
    select 1 from engineering_operator_interventions x
     where x.feature_id = e.feature_id
       and x.action_type = 'resume_feature'
       and x.created_at > e.created_at
  )) as paused
from engineering_operator_interventions e
group by feature_id;
```

### Supported action types (v1)

- `pause_feature`
- `resume_feature`
- `skip_slice`         (payload: `{ "task_id": "...", "reason": "..." }`)
- `drop_slice`         (payload: `{ "task_id": "...", "reason": "..." }`)
- `replan_from_milestone` (payload: `{ "milestone_id": "...", "reason": "..." }`)
- `add_orchestrator_note` (payload: `{ "note": "..." }`)

### Idempotency rule (locked)

**Every click is a row.** The handler does not consult prior state. A
double-pause writes two `pause_feature` rows. The current `paused` state is
derived from the audit log, not stored. This is the source of truth for
"what did the operator actually do" and is necessary for behavioural review.

Frontend: the Pause button is *not* disabled after one click. It can be
clicked again. It does not show a "you already paused this" toast unless the
operator wants belt-and-braces affordance — and even then the row is still
written. UI affordance ≠ data dedup.

### Replan-from-milestone semantics

When `replan_from_milestone` fires for `milestone_id=mX`:

1. The intervention row is written and `intervention.added` is broadcast.
2. The recovery worker emits a `replan` handoff to the planner.
3. The planner is invoked with the **prior consolidated plan as baseline
   context**. The planner brief is told: this is a refinement, not a
   greenfield plan; tasks before `mX` are frozen and must remain identical;
   tasks at or after `mX` may be rewritten, added, removed, or reordered.
4. Planner output is validated as a *delta* against the baseline. If the
   frozen prefix differs by even one byte the plan is rejected.
5. On acceptance, downstream tasks at or after `mX` are marked
   `superseded` (not deleted — keep them queryable for audit) and replaced
   with the new tasks. Worker runs from the previous plan remain attached
   to their original task rows.

Document this contract in `planner-impl-and-review.md` as well. The planner
needs to know it might be invoked in inherit-baseline mode and how to detect
that (`plan_contract.replan_from_milestone_id` on the input contract).

### Dispatch gates (locked from prior brief)

Worker pre-gates must refuse:

- tasks for paused features (derive from view above)
- downstream work locked behind an unsigned milestone
- tasks whose required operator intervention has not been resolved
  (`skip_slice` / `drop_slice` resolution is the existence of the
  corresponding recovery handoff)

Skipping or dropping a slice creates a recovery handoff so downstream
workers consume the change uniformly. Dashboard-only state is a smell.

### Endpoints

```
POST /api/features/{id}/interventions
  body: { "action_type": "...", "payload": {...} }
  side effects: insert row; emit NOTIFY 'intervention.added'; for
                pause/resume also recompute view-derived state and emit
                'feature.update'.
```

The frontend never PATCHes feature state directly. All mutations flow
through interventions.

## 7. User-test resources

`engineering.qa.verify.usertest` uses a separate `qa-usertest` slot (already
specified in `qa-engineer-impl.md`). It acquires a per-project
`full_app_run` resource lock, so two user tests for the same project cannot
fight over ports / databases / teardown, while user tests for different
projects run in parallel.

This validator is usually more wall-clock expensive than token expensive, so
Command Center must show:

- slot occupancy (current, queued, max)
- per-feature lock wait time (queue → lease → model → verification breakdown
  already present on `engineering_worker_runs`)
- the project key currently holding `full_app_run`

Surface in the Telemetry view and as a small status strip on the feature
overview.

## 8. Views

Minimum useful views, each its own route under `/feature/:id/...`:

- **Feature overview**: requirements snippet, plan hash, current milestone,
  next claimable task, blockers, cumulative cost, elapsed time, paused
  banner if applicable, recent interventions strip.
- **DAG view**: Cytoscape canvas (see §5) with side panel.
- **Task view** (see §8a): everything about a single task — contract,
  worker runs, handoffs in/out, QA signoffs, recovery, interventions
  scoped to the task, artifacts, telemetry roll-up.
- **Council view** (see §8b): per-council deliberation surface — panelists,
  consolidator, critic loop, iterations, dissent. Used by planner today;
  reviewer (and future roles) use the same surface.
- **Handoff view**: compact chain (`from_task → to_task`) with handoff_type
  badges, artifact links, cumulative telemetry per hop.
- **Validation view**: scrutiny and user-test evidence side by side, attempted
  breaks, residual risks, screenshots / traces / logs gallery.
- **Telemetry view**: cost (per role, phase, model, validator type), tokens
  (input / cached / cache-creation / output / reasoning), Token Savior
  reductions, RTK savings, wall-clock breakdowns (queue / lease / model /
  verification), repair counts.
- **Intervention view**: immutable timeline of every operator action with
  actor, action_type, payload, created_at; filter and search; export as CSV.

Top-level `/features` route is a sortable table with the columns from
`pgloom-review.sh list`: feature_id, project, branch, state, runs, cost_usd,
roles_seen, last_blocker, created_at.

## 8a. Task view

Route: `/feature/:featureId/task/:taskId`. This is the canonical "everything
about one task" page and is the primary destination from DAG node clicks,
Handoff view rows, Validation view rows, and the feature overview's
"current / next / blocked task" links.

### Header strip

- task_id, role badge, current status pill (`pending`, `claimable`,
  `running`, `passed`, `failed`, `superseded`)
- milestone badge (clickable → DAG view focused on that milestone)
- contract_version, `input_contract_hash` prefix
- attempt count, repair_count, cumulative cost (micros → formatted),
  cumulative tokens
- last blocker_code if any, with timestamp
- pause-state inheritance: if the parent feature is currently paused (per
  `engineering_feature_intervention_state`), show a banner

### Sections

1. **Contract pane** (`engineering_task_contracts`): full input contract
   JSON, validation_errors array, contract diff vs prior version if the task
   was replanned. JSON viewer is collapsible per top-level key.

2. **Worker runs timeline**: every row from `engineering_worker_runs` for
   this task_id, oldest first. Each row shows:
   - role / phase / validator_type / model_provider / model / reasoning_level
   - status, attempt, repair_count
   - wall-clock stacked bar (`queue_seconds`, `lease_seconds`,
     `model_seconds`, `verification_seconds`, `blocked_seconds`)
   - tokens (input / cached_input / cache_creation / output / reasoning)
   - cost (per-run + cumulative)
   - Token Savior reduction (`token_savior_saved_tokens`,
     `token_savior_reduction_ratio`) and `rtk_saved_tokens`
   - `blocker_code` if present
   - expand → prompt artifact link, response artifact link, log link, diff
     link, screenshot/trace gallery
   - Live-update via `worker_run.update` events filtered to this task_id.

3. **Handoffs** in and out: rows from `engineering_handoffs` where
   `from_task_id = :taskId` or `to_task_id = :taskId`. Each row links to the
   peer task's Task view. Show handoff_type badge, status, artifact link
   bundle, cumulative telemetry on the hop.

4. **QA signoffs** scoped to this task: rows from
   `engineering_qa_signoffs` where `task_id = :taskId`. Group by
   `validator_type` (`scrutiny` / `usertest`), show verdict, evidence count,
   artifact count, expand to full evidence/artifact lists. If the task is
   itself a `qa.author` task, also show the *authored* contract that the
   verify slices are checking against.

5. **Recovery actions**: rows from `engineering_recovery_actions` where the
   recovery is scoped to this task. Show blocker_code, action (including
   `corrective_slice`), status, attempt / max_attempts, outcome snippet,
   linked recovery handoff if one was emitted.

6. **Self-repair issues** (when present): rows from
   `engineering_self_repair_issues` where `task_id = :taskId`. Expand to
   show the deliberation thread.

7. **Interventions affecting this task**: filter
   `engineering_operator_interventions` to interventions whose
   `payload->>'task_id' = :taskId` (covers `skip_slice`, `drop_slice`) plus
   feature-wide interventions (`pause_feature`, `resume_feature`,
   `replan_from_milestone`) that bracket the task's lifetime. Render as a
   small inline timeline.

8. **Artifacts** (cross-cutting gallery): every artifact id referenced by
   any of the rows above, deduplicated, grouped by kind (prompt, response,
   log, diff, screenshot, trace, report). Click → opens the artifact in a
   side drawer or new tab depending on type.

9. **Telemetry roll-up**: same shape as Telemetry view (§8) but scoped to
   this task. Cost by phase / model / validator_type, token breakdown,
   wall-clock split, repair counts, cache hit rate.

### Realtime

Subscribes to events with `feature_id` matching the parent and either
`task_id = :taskId` (worker_run, handoff, qa.signoff, recovery, task) or
relevant feature-wide kinds (intervention, plan). The page is live without a
manual refresh.

### Cross-linking

Every other view that mentions a task_id renders it as a link to this view.
Specifically: DAG nodes, Handoff view rows, Validation view rows, Feature
overview "current / next / blocked task" cells, Intervention view rows
whose payload carries `task_id`, and Self-repair entries. The Worker runs
timeline surfaces a "View council" pill on any run whose `council_run_id`
is set, linking to the Council view (§8b).

## 8b. Council view

Councils are not planner-only. The same multi-agent pattern (panelists →
consolidator → critic → optional revision loop) is already used by reviewer
and will be used by future roles (e.g. recovery, design). Command Center
must therefore treat "council" as a first-class entity, not a planner
sub-tab.

### Schema (proposed; lives in migration `011_councils.sql`)

Today, planner council output is stored as a JSONB blob on
`engineering_plan_contracts.council_reports`. That worked for one role; it
does not generalise. Add a normalised set of tables:

```sql
create table engineering_councils (
  id              text primary key,             -- "council_<32hex>"
  feature_id      text not null references engineering_features(id),
  task_id         text,                          -- nullable for feature-level
  role            text not null,                 -- planner | reviewer | ...
  purpose         text not null,                 -- "initial_plan" | "revise_plan"
                                                 -- | "review_implementation" | ...
  status          text not null,                 -- running | passed | failed | aborted
  iteration_max   int  not null default 1,
  iterations_used int  not null default 0,
  consolidated_artifact_id text,                 -- final agreed output
  critic_verdict  text,                           -- accept | revise | reject
  cost_usd_micros bigint not null default 0,
  total_tokens    bigint not null default 0,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz
);

create table engineering_council_panelists (
  id               bigserial primary key,
  council_id       text not null references engineering_councils(id),
  iteration        int  not null,                -- 0-based
  panelist_slot    text not null,                -- "panelist_a" | "consolidator" | "critic"
  model_provider   text not null,
  model            text not null,
  reasoning_level  text,
  worker_run_id    bigint references engineering_worker_runs(id),
  artifact_id      text,                          -- their produced output
  verdict          text,                           -- panelist's own self-verdict if applicable
  vote             text,                           -- accept | revise | reject (for critic only)
  cost_usd_micros  bigint not null default 0,
  input_tokens     int,
  output_tokens    int,
  reasoning_tokens int,
  started_at       timestamptz not null,
  finished_at      timestamptz,
  unique (council_id, iteration, panelist_slot)
);

create index engineering_councils_feature_idx
  on engineering_councils (feature_id, started_at);
create index engineering_councils_task_idx
  on engineering_councils (task_id, started_at);
create index engineering_council_panelists_council_idx
  on engineering_council_panelists (council_id, iteration, panelist_slot);
```

Add `council_run_id text references engineering_councils(id)` to
`engineering_worker_runs` so an individual model invocation knows which
council (if any) it belongs to. Backfill is best-effort: pre-migration plan
councils stay represented by `engineering_plan_contracts.council_reports`
JSONB, with a read-side adapter exposing them through the same API.

### Route

`/feature/:featureId/council/:councilId`. The feature also exposes
`/feature/:featureId/councils` — a list of every council that has run for
the feature with role, purpose, status, iteration count, cost, and a link
in.

### Header strip

- council_id, role badge, purpose, status pill
- iterations used / max
- final critic verdict (`accept` / `revise` / `reject` / pending)
- total cost (micros → formatted) and total tokens
- linked task_id (clickable → Task view) when the council is task-scoped
- linked feature_id (always)
- elapsed wall-clock (`finished_at − started_at`); a live ticker if
  `finished_at` is null

### Sections

1. **Iteration timeline**: one column per iteration (0..n). Each column
   stacks panelist tiles top-to-bottom in execution order: panelists →
   consolidator → critic. Tile shows panelist_slot, model, cost, tokens,
   self-verdict (if any), and an "Open output" link to the artifact.

2. **Diff lane**: between iterations, render a side-by-side or unified
   diff of consolidator outputs (iteration N vs N-1). Operators need to
   see *what the critic forced to change*. If outputs are JSON, use a
   structural diff; if prose, a textual diff.

3. **Dissent panel**: panelists whose self-verdict or output disagreed
   meaningfully with the consolidator. Show the panelist's full text + the
   consolidator's chosen direction. This is how an operator sanity-checks
   "did the consolidator drop a real concern?"

4. **Critic verdict pane**: full critic output for the final iteration —
   the rubric checks (the 14→17 named checks the planner brief defines for
   plan-critic; reviewer-critic has its own rubric), pass/fail per check,
   the binary `accept`/`revise`/`reject` summary, and any specific revision
   demands that triggered the next iteration.

5. **Worker runs**: every `engineering_worker_runs` row joined via
   `council_run_id`. Same row format as the Task view's worker-runs section
   (wall-clock bar, tokens, cost, model, blocker_code). Shows the operator
   the raw cost composition of the council.

6. **Outcome**: the consolidated artifact id (final accepted output) plus
   downstream pointers — for a planner council, the accepted plan_contract
   row(s); for a reviewer council, the review_verdict_contract; etc.

7. **Telemetry roll-up**: cost by panelist_slot (panelists vs consolidator
   vs critic), by model, by iteration. Highlights the common pathology of
   "critic over-tightening" where critic + revise iterations dominate the
   bill — Operator can see it at a glance.

### Realtime

Subscribe to two event kinds (added to §4): `council.update` (status,
iterations_used, critic_verdict, totals) and `council_panelist.update`
(individual panelist tile). When a panelist tile starts running, the column
shows it as in-flight; when it finishes, the cost/tokens/verdict appear
live.

### Cross-linking

- Plan contract rows show "Council: council_..." with a link in.
- Reviewer task pages show their associated council.
- Worker runs with `council_run_id` set show "View council" in the Task
  view's worker-runs section (already noted above).
- Telemetry view (§8) gains a "Cost by council" breakdown alongside the
  existing per-role/per-model breakdowns, since councils can dominate the
  bill on a slice and that needs to be queryable.

### Reviewer councils — first concrete user beyond planner

The reviewer brief (planner-impl-and-review.md) defines reviewer as a
critic over implementer output. When reviewer is run as a council
(panelists each form a verdict, consolidator merges, critic verifies the
merge), every reviewer run produces an `engineering_councils` row with
`role='reviewer'`, `purpose='review_implementation'`, scoped to the
implementer task. The Council view renders identically — no reviewer-
specific UI is added. This is the test that the abstraction is right: if
adding reviewer to Council view requires touching the view, the
abstraction leaked.

## 9. Data shapes — single source of truth

Reuse the queries already encoded in `scripts/pgloom-review.sh` as the
contract for what the API returns. Wrap each section as a dedicated endpoint:

| Endpoint                                       | Backed by                                  |
|-----------------------------------------------|--------------------------------------------|
| `GET /api/features`                           | `pgloom-review.sh list`                    |
| `GET /api/features/{id}`                      | section 0                                  |
| `GET /api/features/{id}/runs`                 | section 1                                  |
| `GET /api/features/{id}/runs/aggregate`       | section 2                                  |
| `GET /api/features/{id}/model-usage`          | section 3                                  |
| `GET /api/features/{id}/token-savior`         | section 4                                  |
| `GET /api/features/{id}/plans`                | section 5                                  |
| `GET /api/features/{id}/tasks`                | section 6                                  |
| `GET /api/features/{id}/tasks/{task_id}`      | task view header + contract pane           |
| `GET /api/features/{id}/tasks/{task_id}/runs` | worker_runs filtered to task               |
| `GET /api/features/{id}/tasks/{task_id}/handoffs`  | handoffs in/out for task              |
| `GET /api/features/{id}/tasks/{task_id}/qa`        | qa_signoffs scoped to task            |
| `GET /api/features/{id}/tasks/{task_id}/recovery`  | recovery_actions scoped to task       |
| `GET /api/features/{id}/tasks/{task_id}/interventions` | interventions touching task       |
| `GET /api/features/{id}/tasks/{task_id}/artifacts` | dedup artifact roll-up                |
| `GET /api/features/{id}/tasks/{task_id}/telemetry` | per-task telemetry roll-up            |
| `GET /api/features/{id}/councils`             | list of councils for the feature           |
| `GET /api/features/{id}/councils/{council_id}`     | council header + outcome              |
| `GET /api/features/{id}/councils/{council_id}/panelists` | panelists by iteration          |
| `GET /api/features/{id}/councils/{council_id}/runs`      | worker_runs joined by council_run_id |
| `GET /api/features/{id}/councils/{council_id}/diffs`     | iteration N vs N-1 consolidator diffs|
| `GET /api/features/{id}/handoffs`             | section 7                                  |
| `GET /api/features/{id}/recovery`             | section 8                                  |
| `GET /api/features/{id}/qa-signoffs`          | section 9                                  |
| `GET /api/features/{id}/interventions`        | section 10                                 |
| `GET /api/features/{id}/self-repair`          | section 11                                 |
| `GET /api/features/{id}/dag`                  | derived from plans + tasks + worker_runs   |

Every cost field travels as integer `usd_micros` (1 USD = 1_000_000 micros).
Pick once, document, never mix — micros gives headroom for sub-cent
reasoning-token charges and avoids float drift in aggregates. Timestamps are
ISO 8601 with explicit `Z`. Token counts are integers, never strings.

Conversion conventions:

- DB columns named `*_usd` (numeric) are converted to `*_usd_micros` (bigint)
  at the serializer boundary: `int(round(value * 1_000_000))`.
- Aggregate endpoints sum micros server-side; never sum the float column then
  convert.
- Frontend formats with a single `formatMicros(n, {precision})` helper in
  `lib/money.ts`. Default display precision: 4 decimals for per-call costs
  (`$0.0123`), 2 decimals for cumulative / aggregate costs (`$8.86`).
- Never round to cents on the wire. Rounding is a render concern.

## 10. Telemetry surfaces (cross-cutting)

For each row that has a cost or token total, also surface:

- `cumulative_cost_usd` (already in `engineering_worker_runs`)
- `token_savior_saved_tokens`, `token_savior_reduction_ratio`
- `rtk_saved_tokens`
- wall-clock split: `queue_seconds`, `lease_seconds`, `model_seconds`,
  `verification_seconds`, `blocked_seconds` — render as a stacked bar in
  the worker-run row
- model identity: `model_provider`, `model`, `reasoning_level`

This is what makes Command Center a *control surface* rather than a status
page. Operators must be able to look at a slice and immediately answer "did
this slice cost too much, and where did the time go?"

## 11. Tests for later implementation

- Aggregates render cost, tokens, savings, wall-clock, status, evidence, and
  model routing from `engineering_worker_runs`.
- Paused features cannot dispatch new work until resumed (gate test).
- Milestone-locked downstream tasks cannot dispatch before both validators
  approve.
- User-test locks serialize same-project app runs and allow different-project
  runs in parallel.
- Operator interventions are included in planner / recovery context.
- Double-pause produces exactly two `pause_feature` rows.
- Replan-from-milestone: planner is invoked with baseline plan; rejecting a
  delta that mutates the frozen prefix.
- LISTEN/NOTIFY: an INSERT on `engineering_worker_runs` produces a
  `worker_run.update` event on the WS within 100ms (in-process test).
- Loopback enforcement: HTTP and WS requests from non-loopback peers are
  rejected with 403.
- NOTIFY payload size: triggers never emit > 7500 bytes; oversized rows fall
  through to a `resync` hint.

## 12. Out of scope (v1)

- Multi-user auth, RBAC, audit-of-the-auditor.
- Remote access (TLS, reverse proxy, SSO).
- Mobile layout. Desktop only.
- Editing plans / tasks directly. All mutation flows through interventions.
- Killing in-flight worker processes. Pause stops *new* dispatch only;
  in-flight runs complete or fail naturally. Hard-kill is a v2 feature with
  its own audit semantics.
- Notifications outside the app (email, Slack). v2.

## 13. Acceptance — done means

1. `python -m pgloom_engineering.command_center` starts the API on
   `127.0.0.1:<port>`, refuses non-loopback peers, and serves the built SPA.
2. `cd pgloom_engineering/command_center/web && npm run build` produces a
   working `dist/`.
3. With one live feature in Postgres, the SPA's feature overview matches
   `pgloom-review.sh review <id>` for every numeric field.
4. Inserting a new `engineering_worker_runs` row appears in the open SPA
   within 1 second without a manual refresh.
5. Clicking Pause twice writes two rows in `engineering_operator_interventions`.
6. Triggering replan-from-milestone produces a planner invocation whose input
   contract carries the prior consolidated plan as baseline, and whose output
   is rejected if the frozen prefix is mutated.
7. The Cytoscape DAG renders the R17-shape feature (planner + qa.author +
   implementer + reviewer + qa.verify.scrutiny + qa.verify.usertest) with
   correct dependency edges and milestone columns.
8. The Task view renders for any task_id in the feature and shows: contract,
   every worker run, in/out handoffs, scoped QA signoffs, scoped recovery,
   scoped interventions, deduped artifacts, and a per-task telemetry
   roll-up. A new `engineering_worker_runs` insert for the open task appears
   live.
9. The Council view renders for any council_id and shows the iteration
   timeline with panelist tiles, the consolidator-diff lane between
   iterations, the dissent panel, the critic verdict pane, joined
   worker_runs, the outcome pointer, and a per-council telemetry roll-up.
   Reviewer councils render through the same view with no reviewer-specific
   code path. Inserting an `engineering_council_panelists` row for an open
   council updates the relevant iteration column live.
