# Architecture

`pgloom-engineering` is a domain layer over `pgloom`.

`pgloom` owns durable workflow primitives:

- workflows
- tasks
- slots
- leases
- approvals
- model usage
- artifacts
- notifications
- memory
- dashboard collector registration

`pgloom-engineering` owns engineering behavior:

- feature aggregation
- role handlers
- typed plan, task, QA, review, and recovery contracts
- planner council and rubric-style critics
- QA author semantic checks and project-gate validation
- milestone validation contracts and bidirectional assertion coverage
- compact structured handoffs and worker-run telemetry
- adversarial scrutiny and user-test validators
- Git and GitHub integrations
- Telegram command handling
- Command Center collectors and operator interventions
- PPD reports

BRAID is parked. The runtime primitive for autonomous review is typed contracts
plus Python rubric runners, deterministic gates, and recorded recovery actions.
BRAID can be revisited only if human-authored graph templates or lint-time
workflow-shape guarantees become a concrete need.

## Autonomy Model

The target execution model is autonomous until final PR merge:

1. Human registers a project and states a feature goal with requirements.
2. Planner emits a typed plan with milestones, task slices, validation
   contracts, grading criteria, and required worker procedures.
3. QA author writes and validates red tests before implementation.
4. Implementer makes the QA worktree green while preserving QA-authored tests.
5. Review and QA validators attack the work with fresh context.
6. The orchestrator creates targeted corrective slices or replans from a
   milestone when validators find issues.
7. Humans review visibility, intervene through Command Center when needed, and
   merge the final PR.

Validation failure is normal. It should produce specific corrective work, not a
blind retry loop or a human escalation by default.

## Milestones And Validators

`PlanContract` should grow a first-class milestone layer. A milestone groups
task slices into a coherent checkpoint and carries the validation contract for
that checkpoint. Downstream milestone work is not claimable until both
validators approve:

- **Scrutiny validator**: runs lint, type checks, test suites, deterministic
  gates, and fresh-context code-review agents for each completed feature in the
  milestone.
- **User-test validator**: starts the application or service and exercises real
  flows through Playwright, browser/computer-use, or CLI replay.

Both validators receive the worktree HEAD and validation contract, not the
upstream worker's reasoning. They are adversarial by design and must record
`attempted_breaks`, residual risks, confidence, and typed evidence.

## Handoffs And Evidence

Worker completion is not just a status transition. Each worker should emit a
compact `HandoffEnvelope` containing:

- what was completed and what was left undone
- commands run with exit code and duration
- required procedure attestation
- issues discovered in passing
- next-worker context and reviewer context
- telemetry summary and artifact ids
- typed validation evidence
- the role-specific contract payload
- cumulative cost, wall-clock, token usage, token savings, and model-call
  totals

Large material stays out of handoffs. Prompts, responses, logs, changed-file
diffs, screenshots, network traces, benchmark reports, and outcome JSON are
stored as pgloom artifacts and referenced by id.

## Worker Telemetry

Every worker invocation and repair phase should produce a durable run record
with wall-clock timing, model cost, token usage, cached/reasoning token detail,
Token Savior savings, RTK/log-filter savings, model routing, status, blockers,
repair counts, checks, artifacts, and handoff ids. This run record is the join
surface for human dashboards and automated diagnosis.

The target table is `engineering_worker_runs`. It links to `model_usage`,
`engineering_token_savior_usage`, artifacts, task events, and handoffs while
preserving compact per-run summaries for Command Center.

## Command Center

Command Center is the operator surface for visibility and intervention. It is a
control surface, not just a status page. It should render task DAGs, milestones,
handoffs, worker runs, validation evidence, artifacts, cost burn, token savings,
wall-clock bottlenecks, recovery actions, and operator interventions. Supported
interventions include pause, resume, skip slice, drop slice, replan from
milestone, and adding an orchestrator note for the next planning pass.
