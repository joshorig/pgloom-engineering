# Codex goal - autonomous engineering orchestrator implementation

Use this as the compact Codex goal when starting the implementation feature.
The full plan lives in docs; keep the goal under platform limits by referencing
those docs rather than pasting the architecture.

```text
Implement the autonomous engineering orchestrator plan for pgloom-engineering.
This is an implementation goal, not a request to create another feature-goal
file or planning prompt.

Read these docs first and treat them as source of truth:
- README.md
- docs/architecture.md
- docs/roles.md
- docs/operations.md
- docs/prompts/autonomy-verification-handoff.md
- docs/prompts/handoff-telemetry-and-evidence.md
- docs/prompts/command-center-dashboard.md
- docs/prompts/planner-impl-and-review.md
- docs/prompts/qa-engineer-impl.md
- docs/prompts/iter-2-and-output-economy.md
- docs/prompts/token-savior-advanced.md
- docs/prompts/rtk-subprocess-filter.md

Goal:
Build pgloom-engineering into an autonomous engineering orchestrator. Humans
define project goals and requirements up front. Agents plan, author QA,
implement, review, validate, self-heal, collect typed evidence, and stop at
human final PR review/merge.

Required implementation outcomes:
- First-class milestone planning in PlanContract, including validation
  contracts, bidirectional acceptance assertion coverage, required_procedures,
  grading criteria, context budgets, model route hints, and corrective_slice
  recovery.
- Split validation into engineering.qa.verify.scrutiny and
  engineering.qa.verify.usertest with separate slots, fresh-context validators,
  milestone/final signoff gates, and metadata-authorized user-test skip for
  pure-library projects.
- Mandatory engineering_worker_runs telemetry for every planner, QA author,
  implementer, reviewer, validator, recovery worker, repair phase, blocked run,
  retry, and crash.
- Compact HandoffEnvelope outputs with commands_run, procedures_attestation,
  typed ValidationEvidence, artifact ids, cumulative cost, wall-clock, tokens,
  token savings, and model calls.
- Recovery treats validation failure as normal orchestration data and prefers
  targeted corrective slices before full milestone replan or human escalation.
- Command Center shows feature state, task DAG, milestones, handoff chain,
  worker runs, validation evidence, artifacts, model cost, token usage, Token
  Savior and RTK savings, wall-clock bottlenecks, recovery actions, and audited
  operator interventions.

Constraints:
- Do not build a prompt-only orchestrator. Keep typed contracts, durable data,
  deterministic gates, and explicit role handlers.
- Do not add meta-orchestration or mutable skill-learning yet.
- No backward compatibility is required.
- Store raw prompts, responses, logs, screenshots, traces, diffs, and reports as
  artifacts; keep handoffs compact.
- Final human gate remains PR review/merge.

Validation:
- Update docs only where needed to match implemented behavior.
- Unit tests must cover milestones, assertion coverage, required procedures,
  telemetry row creation and failure recording, cumulative aggregation, typed
  evidence, handoffs, corrective_slice recovery, usertest skip metadata,
  user-test resource locks, and Command Center aggregates.
- Integration coverage must exercise planner -> QA author -> implementer ->
  reviewer -> QA scrutiny -> QA usertest -> milestone signoff -> final evidence.
- Failure tests must prove blocked, crashed, retried, and repaired workers still
  record wall-clock, costs/tokens if any, artifact ids, blocker, and recovery
  handoff.
```
