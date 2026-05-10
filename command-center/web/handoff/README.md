# Handoff: Command Center — operator control surface for pgloom-engineering

## Overview

Command Center is the operator console for autonomous engineering runs in
`pgloom-engineering`. It is **not** a status page — it is a control surface
that shows progress, evidence, cost, token economy, wall-clock bottlenecks,
recovery, and allows audited interventions.

This handoff bundles the design system and a hi-fi prototype of every view the
v1 brief asks for. The locked technical decisions in
`docs/prompts/command-center-dashboard.md` (React + Vite SPA, FastAPI on
loopback, Postgres `LISTEN/NOTIFY` over a single WebSocket, Cytoscape.js DAG,
intervention audit log, `usd_micros` on the wire) are the implementation
contract — this package is how the UI should look and behave on top of it.

## About the Design Files

The files in `design/` are **design references created in HTML/JSX** —
prototypes showing intended look and behavior on a `<DesignCanvas>` wrapper.
They are not production code to copy directly.

The task is to **recreate these designs in the target codebase
(`pgloom_engineering/command_center/web/`) using the React + Vite + TypeScript
environment specified in §2 of the brief.** Reuse the design tokens
(`tokens.css`) verbatim — they are the design system contract — and recreate
the components against the real `/api/*` and `/ws` endpoints.

## Fidelity

**High-fidelity (hifi).** Final colors, typography, spacing, status semantics,
interaction patterns, animation timing, empty/error states, and all six
operator views are pixel-accurate. Recreate them faithfully against the live
data shapes from §9 of the brief.

The prototype data is synthetic but follows the brief's data contracts (R17-shaped
feature; cost in `usd_micros`; ISO timestamps with explicit `Z`; integer token
counts). Replace with live API responses; do not change shapes.

## Routes / Views

The `<DesignCanvas>` artboards map 1:1 to React routes under
`pgloom_engineering/command_center/web/src/routes/`:

| Artboard                              | Route                                | Source file                       |
|---------------------------------------|--------------------------------------|-----------------------------------|
| Foundations                           | (design-system reference, not a route) | `design/00-foundations.*`       |
| Features list                         | `/features`                          | `FeaturesList.tsx`                |
| Feature overview                      | `/feature/:id`                       | `FeatureOverview.tsx`             |
| DAG view                              | `/feature/:id/dag`                   | `DagView.tsx`                     |
| Handoff view                          | `/feature/:id/handoffs`              | `HandoffView.tsx`                 |
| Validation view                       | `/feature/:id/validation`            | `ValidationView.tsx`              |
| Telemetry view                        | `/feature/:id/telemetry`             | `TelemetryView.tsx`               |
| Interventions audit                   | `/feature/:id/interventions`         | `InterventionView.tsx`            |
| Replan-from-milestone (modal)         | overlays Feature overview            | `dialogs/ReplanDialog.tsx`        |
| Live event firehose                   | `/realtime` (debug surface)          | `RealtimePanel.tsx`               |
| qa-usertest slot occupancy            | `/feature/:id/telemetry/slots`       | `SlotOccupancy.tsx`               |
| Recovery & corrective slices          | `/feature/:id/recovery`              | `RecoveryView.tsx`                |
| Token economy detail                  | `/feature/:id/telemetry/tokens`      | `TokenEconomyView.tsx`            |
| Empty / error states                  | (state primitives, not a route)      | `components/states/`              |
| Project registry                      | `/projects`                          | `ProjectRegistry.tsx`             |

## Layout Skeleton (every authenticated view)

```
┌─────────────────────────────────────────────────────────────┐ ← TopBar (44px)
│  cmdcc · pgloom-engineering · feature picker · op_…@local   │   panel-2, 1px line
├─────────────────────────────────────────────────────────────┤ ← FeatureBar (variable)
│  ⏵ Range API · cross-shard reader (R)   [paused?] · runs..  │
├─────────────────────────────────────────────────────────────┤ ← Tabs (38px)
│  ▼ Overview · DAG · Handoffs · Validation · Telemetry · Int │   border-bottom 1px line
├─────────────────────────────────────────────────────────────┤
│                                                              │
│                       View content                           │ ← scroll region
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Design Tokens — reproduce verbatim

All values live in `design/tokens.css`. Use as-is; load it once at app root.

### Colors (dark default; dim & light variants flip surfaces only)

Surfaces:
- `--bg`        `#0a0c0e`
- `--bg-grid`   `#0c1014`
- `--panel`     `#11151a`
- `--panel-2`   `#161b21`
- `--panel-3`   `#1c222a`
- `--inset`     `#07090b`

Lines (alpha on white): `--line` 6%, `--line-2` 10%, `--line-3` 16%, `--line-keep` 4%.

Text: `--t1` `#e6ebf2` (primary), `--t2` `#aab2bc`, `--t3` `#6e7782`, `--t4` `#495159`.

Accent (cyan default, swappable):
- `--accent` `oklch(0.82 0.13 196)`
- `--accent-soft` 18% alpha · `--accent-line` 42% alpha · `--accent-ink` `#07090b`
- Curated swaps: amber `oklch(0.82 0.13 70)`, emerald `oklch(0.80 0.16 152)`, violet `oklch(0.78 0.10 280)`

Status (constant across themes — never fudge):
- `--st-run`     cyan      `oklch(0.82 0.13 196)`  — running
- `--st-pass`    emerald   `oklch(0.80 0.16 152)`  — passed
- `--st-fail`    rose      `oklch(0.74 0.18 22)`   — failed
- `--st-block`   amber     `oklch(0.82 0.13 70)`   — blocked
- `--st-pause`   orange    `oklch(0.78 0.14 45)`   — paused
- `--st-queue`   neutral   `oklch(0.65 0.01 250)`  — queued
- `--st-super`   muted purple `oklch(0.62 0.06 305)` — superseded

Each status has a `*-soft` 14% alpha companion for fills.

Role hues (for badges and DAG node rings):
- planner `oklch(0.78 0.10 280)`
- impl    `oklch(0.82 0.13 196)`
- review  `oklch(0.78 0.14 320)`
- qa-author `oklch(0.80 0.16 152)`
- qa-scrut  `oklch(0.82 0.13 70)`
- qa-test   `oklch(0.78 0.14 45)`
- recovery  `oklch(0.74 0.18 22)`

### Typography

- Sans (UI): `Inter Tight`, fallback `Inter`, system-ui
- Mono (data, IDs, costs, timestamps, status, code): `JetBrains Mono`, fallback `SF Mono`, Menlo
- Tweakable pairings: `plex` (IBM Plex Sans + Mono), `berkeley` (Berkeley Mono + Söhne), `mono` (everything mono)

Sizes: 9, 10, 11, 12, 13 (body default), 14, 16, 20, 24, 32 px.
Compact density shifts body to 12, 11, 10, 9 — preserve the cascade.

Always use mono for: feature ids, task ids, timestamps, byte counts, costs,
token counts, wire-format JSON, command lines, role badges.

### Spacing scale

`2 4 6 8 10 12 14 16 20 24 32 40 48` (px). Compact density compresses the
upper end (16→12, 20→14, 24→18) but never the lower end.

### Row heights

- Top bar 44 (compact 40)
- Tabs 38 (compact 34)
- Table row 32 (compact 26)
- Input 28 (compact 24)
- Pill 18 · Chip 22

### Radii

Cards 4px · pills 999 · chips 3. Never round more than 4px on chrome.

### Shadows

Used sparingly. Hairlines do most of the work.
- card: `0 1px 0 rgba(255,255,255,0.02) inset, 0 1px 2px rgba(0,0,0,0.4)`
- pop:  `0 8px 32px rgba(0,0,0,0.45), 0 0 0 1px var(--line-2)`

## Components — by purpose

### Status pill (`StatusPill`)
Used everywhere a row/task/run has a state.
- 18px tall · 6px h-padding · radius 3 · uppercase 10px mono · 0.06em letter-spacing
- color = `var(--st-{status})` text on `var(--st-{status}-soft)` background
- states: `running`, `passed`, `failed`, `blocked`, `paused`, `queued`, `superseded`
- runs add a 5×5 dot prefix that pulses (`.cc-pulse`) when `data-pulse="on"`

### Role badge (`RoleBadge`)
- compact form: 16×16 mono initial in a 2px-radius square colored by role hue
- default form: dot + role name in mono; both share the role hue with 14% soft fill
- maps: planner P, implementer I, reviewer R, qa.author Q, qa.scrutiny S, qa.usertest U

### Cost cell (`CostCell` / `formatMicros`)
- input: integer `usd_micros` (bigint)
- per-call display: 4 decimals (`$0.0123`)
- aggregate display: 2 decimals (`$8.86`)
- always mono · tabular-nums · right-aligned in tables
- never round on the wire — render concern only

### Token cell (`TokenCell`)
- mono · tabular-nums · `toLocaleString` for grouping
- saved/cached values use `--t3` (dim); `saved` shows leading `−` in `--accent`

### Wall-clock bar (`WallClockBar`)
A stacked horizontal bar showing the §10 split for one worker run:
queue (gray) · lease (gray-2) · model (`--st-run`) · verification (`--r-review`) · blocked (`--st-block`).
Total width = sum of seconds. Tooltip shows ms per segment.

### Data table (`.cc-table`)
- 11.5px mono in cells · 10px uppercase 0.06em headers in `--t3`
- row height 32 (compact 26) · hairline `--line` between rows
- left-align text, right-align numbers, mono for ids/costs/tokens
- header is sticky when the table scrolls; sort glyph is a 6px chevron

### Tabs (`.cc-tab`)
- 38px tall · 14px h-padding · 12px label · mono icon prefix
- active: 1px bottom border in `--accent`; inactive: hover lightens to `--t1`
- numeric badges (e.g. "DAG · 25") live to the right of the label in mono `--t3`

### Panel (`.cc-panel`)
The base content container. `--panel-2` background, 1px `--line`, 4px radius.
Header strip (`PanelHd`): 10px uppercase `--t3` kicker + 13px `--t1` title +
optional right-aligned `action`. Body has 12–14px padding.

### Stat (`<Stat k v d />`)
Three-row metric card.
- `k` 10px uppercase `--t3`
- `v` 22px sans `--t1` tabular-nums
- `d` 11px mono `--t3` (delta or context)

### Buttons
- Primary: `--accent` background, `--accent-ink` text, 28px tall, 4px radius, no shadow
- Ghost: 1px `--line-2` outline, transparent, hover fills with `--panel-3`
- Destructive: 1px `--st-fail` outline, `--st-fail` text, hover fills with `--st-fail-soft`
- Always 11.5px mono on buttons in this console (engineering tool, not consumer)

### Chips (filters / facets)
22px tall · 11px mono · 3px radius · 1px `--line` · `is-on` adds `--accent` border + soft fill.

### Dialog / Modal
- centered, max-width 960, 6px radius, `--shadow-pop`
- header: 16/20 padding, 1px line, kicker + 18px title + close button
- footer: 12/20 padding, top 1px line, status mono on left, ghost+primary buttons on right
- backdrop: `rgba(7,9,11,0.55)` + 2px blur
- never auto-dismiss — operator must confirm

### Empty / disconnected / error states
Six canonical patterns rendered in `feature-states`. Use them in this order of
loudness: skeleton (loading) → muted illustration (empty) → dashed amber border
(reconnecting) → solid amber chip (resync hint) → solid rose card (auth/error)
→ accent-tinted card (paused). Never silently fail closed without surfacing.

## Interactions & Behavior

- **Tabs:** click navigates · keyboard ←/→ when focused · numeric badges live-update
- **Tables:** click header to sort (asc → desc → none) · click row to drill in
- **Filters:** chip toggle is multi-select; clearing all returns to default
- **Pause toggle:** writes one row to `engineering_operator_interventions`. *Not* disabled after click. Double-click writes two rows (per §6).
- **Replan modal:** confirm button posts `replan_from_milestone`; reason textarea is required and stamped on the audit row.
- **DAG:**
  - click node → side panel with worker-run history, handoffs in/out, QA signoffs, artifact links
  - milestone groups are collapsible (compound nodes)
  - `task.update` events patch nodes in place — no relayout per tick
  - persist zoom/pan/expanded milestones in `localStorage` keyed by feature id
- **Live pulse:** running dots animate at 1.6s ease-in-out. Suspended when `data-pulse="off"` is on the root.
- **Realtime:** SWR caches mutate from incoming events. On `resync` or reconnect, refetch the open feature.

## State / Data

Each route uses SWR (or React Query) keyed by `featureId + endpoint`. The
realtime layer (`realtime.ts`) opens a single WebSocket and dispatches `mutate()`
per incoming event kind. Endpoint contracts are §9 of the brief — wrap each
section of `pgloom-review.sh` as one endpoint, no fancier.

`localStorage` keys (all prefixed `cc:`):
- `cc:tweaks` — design-system tweaks (theme, accent, density, type, pulse)
- `cc:dag:<featureId>` — zoom/pan/expanded milestones
- `cc:filters:<route>:<featureId>` — table filter state

## Tweaks (from `Tweaks` toolbar)

Persisted on the prototype side via `__edit_mode_set_keys`. In production, drop the host integration and store under `cc:tweaks`:

```json
{ "theme": "dark", "accent": "cyan", "density": "comfortable", "typePairing": "default", "pulse": true, "paused": false }
```

Apply by setting `data-theme`, `data-accent`, `data-density`, `data-type`,
`data-pulse` on the SPA root. The CSS in `tokens.css` does the rest.

## Files in this bundle

```
design/
  Command Center.html          ← canvas wrapper that loads everything below
  tokens.css                   ← design system tokens (USE VERBATIM)
  components.css               ← base component styles
  components-extra.css         ← view-specific styles
  extras-2.css … extras-5.css  ← additional view styles
  shared.jsx                   ← Stat, RoleBadge, PanelHd, CCApp shell
  chrome.jsx                   ← TopBar, FeatureBar, Tabs
  data.jsx                     ← synthetic data shaped per §9
  artboards-1.jsx … artboards-6.jsx  ← one or more views per file
  design-canvas.jsx            ← canvas/focus overlay (prototype only — drop in prod)
  tweaks-panel.jsx             ← tweaks UI (prototype only — drop in prod)
```

`Command Center.html` is the entry point. Open it in a browser to see all
artboards laid out on a pannable canvas. Double-click any artboard to focus it
fullscreen; ←/→/Esc navigates siblings.

## Assets

No third-party brand assets are used. Type loads from Google Fonts
(`JetBrains Mono`, `Inter Tight`). Status icons are inline SVG glyphs sized to
the row's mono font size; lift them from the JSX or replace with `lucide-react`
equivalents (preferred — they are already in the icon vocabulary used).

## Out of scope (per §12 of the brief)

Multi-user auth · remote access · mobile · plan/task editing · in-flight kill ·
out-of-app notifications. Pause stops new dispatch only.

## Acceptance — done means

1. SPA mounts at `pgloom_engineering/command_center/web/dist/` and is served by
   the FastAPI app on `127.0.0.1:<port>`.
2. Every view in this README renders against the real `/api/*` endpoints.
3. WebSocket replay/resync/reconnect work as in §4 of the brief.
4. Pause clicked twice writes two rows (§6).
5. Replan-from-milestone shows the design's confirm dialog and writes one row.
6. Cytoscape DAG renders the R17-shape feature with milestone columns.
7. All status colors match the tokens above (cyan running / emerald pass /
   rose fail / amber blocked / orange paused).
