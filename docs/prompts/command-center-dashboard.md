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
    handoffs.py
    qa.py
    telemetry.py
    interventions.py      # POST handlers; insert + NOTIFY
  serializers.py          # row -> JSON (cents-of-dollar, ISO timestamps)
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
- Click a node → side panel with worker-run history, handoffs in/out, QA
  signoffs, artifact links.
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
