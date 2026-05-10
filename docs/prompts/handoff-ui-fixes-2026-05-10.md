# Handoff prompt — UI developer (post 2026-05-10 council review)

You are a senior frontend developer on **pgloom-engineering Command
Center** — a React + Vite SPA backed by a FastAPI service that talks to
Postgres. Josh has just finished a council review of the system and the
brief that drives this UI. Your job is to land the UI work that follows
from the review.

## 0. Read first (in this order)

1. `docs/reports/council-review-2026-05-10.md` — full review. Read end to
   end. Sections most relevant to you: §2.5 (frontend / security), §2.6
   (council abstraction), §3.1 (brief patches — already applied to the
   brief), §3.2 (brief additions — also applied), §5 (top recommendations
   — items 6 and 7 are squarely on you).
2. `docs/prompts/command-center-dashboard.md` — implementor brief. The
   review's brief patches are already in. Read the whole thing, with
   particular attention to:
   - §2 architecture and the migration note (010-015 already shipped;
     new schema work is 016+ and lives with the workflow developer)
   - §3 auth (now includes Origin / Host / CORS hardening you must
     implement)
   - §4 realtime (LISTEN/NOTIFY events and payload schema)
   - §5 DAG view (Cytoscape)
   - §6 operator interventions (every-click-is-a-row; pause emits
     synthetic feature.update from API)
   - §8 views list, §8a Task view, §8b Council view (with legacy adapter
     clause)
   - §9 endpoints and the cost-unit conventions (`usd_micros` everywhere
     on the wire)
   - §13 acceptance criteria — points 8, 9, 10, 11, 12 are yours
3. Existing code at `pgloom_engineering/command_center/` — backend is
   live; v1 React app is at `command_center/web/`. Playwright e2e at
   `command_center/web/tests/e2e/command-center.spec.ts` — make any new
   surface land with an e2e test alongside.
4. Migrations 010-015 in `pgloom_engineering/db/schema/` — read them so
   you know what columns and views exist server-side. The brief
   intentionally does not redefine 010's view; you read it directly.

## 1. Auth hardening — Origin / Host / CORS

**Problem.** Today the backend binds 127.0.0.1 and checks
`request.client.host`. Brief §2.5 in the review notes this is necessary
but not sufficient: a malicious page in any browser the operator opens is
also a loopback peer. Brief §3 (now patched) specifies the additional
checks; you implement them.

**What to do.**

1. In `pgloom_engineering/command_center/auth.py` (or the equivalent
   middleware surface — Read to find), add:
   - **Host-header allowlist**: reject HTTP and WS requests whose `Host`
     header is not `127.0.0.1[:<port>]` or `localhost[:<port>]`.
   - **Origin allowlist on WS upgrade**: reject WS handshakes whose
     `Origin` is not `http://localhost:<port>` or `http://127.0.0.1:<port>`.
   - **CORS default-deny**: do not enable `CORSMiddleware` with permissive
     origins. The SPA is same-origin (FastAPI mounts `web/dist`). The
     only override is dev-mode (`CC_DEV_MODE=1` env var) which adds
     `http://localhost:5173` (Vite dev port) — gate this behind the env
     var, do not leave it on by default.
2. Tests:
   - HTTP request with `Host: evil.example` → 403
   - HTTP request with `Host: localhost:<port>` → 200
   - WS upgrade with `Origin: http://evil.example` → rejected
   - WS upgrade with `Origin: http://localhost:<port>` → accepted
   - With `CC_DEV_MODE=1`, requests from `Origin: http://localhost:5173`
     → accepted
3. Update the README in `command_center/` to document the v1 hardening
   posture and the dev-mode env var.

**Acceptance.** Brief acceptance test #12 passes.

## 2. Surface synthetic `feature.update` for pause / resume

**Problem.** Brief §6 specifies that pause / resume don't mutate
`engineering_features`, so the trigger-based `feature.update` event won't
fire. The API handler must synthesise the event after inserting the
intervention row, otherwise clients have to wait for an unrelated event
to refresh paused-state.

**What to do.**

1. In `pgloom_engineering/command_center/routes/interventions.py` (find
   the existing POST handler), after inserting the intervention row, if
   the action_type is `pause_feature` or `resume_feature`, emit a
   synthetic event on the realtime fan-out matching brief §6 endpoints
   shape:
   ```json
   {"v":1, "kind":"feature.update", "feature_id":"...",
    "fields":["paused"], "ts":"..."}
   ```
2. The frontend `realtime.ts` should already mutate state from this
   event kind; verify (and add to the SWR/React Query cache mutation if
   not).
3. Test: post a `pause_feature` intervention, assert the WS receives
   exactly one `feature.update` event with `fields=["paused"]` within
   100ms.

**Acceptance.** Pause flips the feature header banner without a manual
refresh.

## 3. Task view — implement §8a

**Problem.** Brief §8a fully specifies the Task view (route
`/feature/:featureId/task/:taskId`). It is the canonical "everything
about one task" page and is the destination for clicks from DAG nodes,
Handoff view rows, Validation view rows, and Feature overview "current /
next / blocked task" cells.

**What to do.** Build it per §8a. Specifically:

1. Backend: `pgloom_engineering/command_center/routes/tasks.py` exposing
   the eight per-task endpoints listed in §9 (header, runs, handoffs, qa,
   recovery, interventions, artifacts, telemetry).
2. Frontend: `web/src/routes/TaskView.tsx` with the nine sections from
   §8a (header strip + 9 sections incl. contract pane, worker-runs
   timeline with stacked wall-clock bar, handoffs, qa signoffs, recovery,
   self-repair, interventions filtered to task, artifact gallery,
   telemetry roll-up).
3. Wire cross-links: every `task_id` rendered anywhere else is a `<Link>`
   into the Task view (DAG nodes, Handoff view rows, Validation view
   rows, Feature overview cells, Intervention view rows whose payload
   carries `task_id`, Self-repair entries).
4. The artifact gallery (section 8 of the Task view) must recognise the
   new artifact kinds the brief added: `worktree_diff` (split-diff
   viewer) and `file_snapshots` (file-tree-with-hashes). These are the
   *only* surviving evidence for non-persisted worktrees (today:
   implementer, reviewer, qa.verify.scrutiny, qa.verify.usertest), so
   they are the most-clicked artifacts in the gallery — make them
   first-class.
5. Wall-clock stacked bar uses the **correct column names**:
   `queued_seconds`, `leased_seconds`, `model_seconds`,
   `verification_seconds`, `blocked_seconds`. Brief patched but the bug
   was easy to copy-paste; double-check what you write.
6. All cost values are `usd_micros` on the wire; render via
   `lib/money.ts` `formatMicros(n, {precision})` — 4 decimals for
   per-call, 2 for cumulative.
7. Live updates: subscribe to events with matching `feature_id` and
   either `task_id = :taskId` or relevant feature-wide kinds. Verify in
   the existing `realtime.ts`.
8. Playwright e2e: open Task view for a known task_id in a fixture
   feature, assert all sections render, simulate an
   `engineering_worker_runs` insert (via test fixture or direct DB write
   in test setup) and assert the new row appears within 1s.

**Acceptance.** Brief acceptance test #8 passes.

## 4. Council view — implement §8b (with legacy adapter)

**Problem.** Brief §8b fully specifies the Council view, including the
new normalised schema (migration 016 — landed by the workflow developer;
**check that it has shipped before starting**) and a read-side adapter
projecting pre-016 `engineering_plan_contracts.council_reports` JSONB
through the same API.

**What to do.**

1. Coordinate with the workflow developer's handoff item 5 — wait for
   migration 016 + planner council persistence to land before wiring
   live data. You can build the UI against fixture data in parallel.
2. Backend: `pgloom_engineering/command_center/routes/councils.py`
   exposing the five endpoints listed in §9 (list, header, panelists,
   runs, diffs). The endpoints serve both new and legacy councils
   uniformly; the legacy adapter projects pre-016 plan-contract
   `council_reports` JSONB into the same shape with `legacy: true`.
3. Frontend: `web/src/routes/CouncilView.tsx` and `CouncilsList.tsx`
   per §8b, with the seven sections (iteration timeline, diff lane,
   dissent panel, critic verdict pane, joined worker_runs, outcome,
   telemetry roll-up).
4. **Legacy badge.** When the API response carries `legacy: true`,
   render a "legacy" badge in the header and disable the panes that
   need per-tile data. Replace numeric placeholders with "unavailable
   (legacy)" rather than zero. Brief §8b "Legacy councils" lists the
   exact rules.
5. Telemetry roll-up uses `panelist_kind` + `panelist_ordinal` (not the
   old `panelist_slot`) — brief patched but worth flagging.
6. Live updates: two new event kinds on the WS (`council.update`,
   `council_panelist.update`). Update `realtime.ts` to fan them in.
7. Cross-links: plan contract rows link to their council; reviewer task
   pages link to their council; worker runs in the Task view's
   worker-runs section show "View council" pill when `council_run_id`
   is set; Telemetry view gains a "Cost by council" breakdown.
8. Playwright e2e: open Council view for a fixture council, assert all
   panes render, assert legacy badge appears for a fixture legacy
   council.

**Acceptance.** Brief acceptance tests #9 and #10 pass.

## 5. Replan-from-milestone UI

**Problem.** The CLI command exists; the workflow developer is wiring
consumption (their item 4). You add the Command Center button and the
inheritance-banner UX.

**What to do.**

1. Add a "Replan from milestone" action to the DAG view and Feature
   overview. The action takes a `milestone_id` argument (operator picks
   from the milestone list) and a free-text reason. POST to
   `/api/features/{id}/interventions` with
   `action_type: "replan_from_milestone"`.
2. Confirmation dialog must show: "This will mark all tasks at and after
   <milestone label> as superseded. The planner will be re-invoked with
   the prior plan as a baseline. Tasks before this milestone will not
   change. Proceed?" plus the reason field (required).
3. After submit, do not optimistically update — wait for the
   `intervention.added` event and the resulting plan dispatch event.
4. On the Plan / DAG view, when a plan_contract has
   `replaced_plan_contract_id` set, render an inheritance breadcrumb
   ("Inherited from plan v3, milestone m2") with a link to the prior
   plan. The breadcrumb is the operator's audit trail.
5. The intervention button is **never disabled after one click** (brief
   §6 lock #6). Double-click writes two rows. The UI may show a "you
   already replanned at this milestone" affordance but does not prevent
   the second click — the audit log is the source of truth.

**Acceptance.** Operator can trigger a replan from the DAG view, the
intervention row appears in the audit log, and (once the workflow
developer's item 4 lands) the planner re-runs with the baseline.

## 6. Surface `abort_reason` on Feature overview

**Problem.** The workflow developer's item 3 instruments
`engineering_features.abort_reason` and `abort_detail`. You surface them
in the UI.

**What to do.**

1. When `engineering_features.state = 'aborted'`, show a banner at the
   top of the Feature overview with the `abort_reason` enum value
   rendered as a labelled pill (`operator_kill`, `supervisor_timeout`,
   `lifecycle_error`, `external_signal`, `unknown`) and the
   `abort_detail` text below it.
2. In the Worker runs timeline (Task view §8a section 2), the row for
   the abort transition gets a small icon and tooltip showing the
   reason.
3. The features list table (`/features`) gains an `abort_reason`
   column, sortable, defaulting to hidden but toggleable from a
   column-picker.
4. Test: a fixture feature with each enum value renders the correct
   pill text and color.

**Acceptance.** Brief acceptance test #11 passes (the workflow side
writes the column; you make it visible).

## 7. Order of work + verification

1. Items 1 and 2 are independent and can land first; they're small.
2. Item 3 (Task view) is the biggest piece and is what makes the rest
   useful. Land it second.
3. Items 4, 5, 6 all depend on workflow-developer landings — coordinate
   on a slack/PR comment basis. You can build against fixtures while
   waiting.
4. Each PR runs `npm run build`, `npm run lint`, the Playwright e2e
   suite, and the backend pytest suite (touch only your routes; tests
   under `tests/unit/` for the FastAPI routes you edit). All must be
   green.
5. After items 1, 2, 3 ship, take a screenshot of the Task view loaded
   for a real feature and post it back to Josh.

## 8. Hand-back

Open a PR per item (don't bundle). Each PR description must reference the
council review section it addresses (e.g. "closes council-review-2026-05-10
§3.2 #8 (worktree-diff artifact kind)") and include the test commands
run + their pass output. Include screenshots for any visual change.

If the brief contradicts itself or is wrong, push back. Two recent rounds
of brief patching have shaken out drift, but more is possible. Better to
flag and fix than to copy a stale identifier into shipping code.
