# Roles

Planned task handlers:

- `engineering.plan`
- `engineering.implement`
- `engineering.review`
- `engineering.qa.author`
- `engineering.qa.verify.scrutiny`
- `engineering.qa.verify.usertest`
- `engineering.historian`

Each handler will implement pgloom's handler contract and keep engineering
policy outside the core runtime.

`engineering.qa.author` is partially implemented. It writes failing tests in a
feature worktree, validates required project gates, runs deterministic semantic
checks over changed tests and benchmarks, and emits `QAAuthorContract`.

`engineering.implement` and `engineering.review` are live enough for the
production workflow path but still need richer handoff, telemetry, and
adversarial-review contracts.

The legacy single `engineering.qa.verify` role should be split before
production sign-off work lands:

- `engineering.qa.verify.scrutiny` runs static checks, deterministic gates, and
  fresh-context per-feature code-review agents.
- `engineering.qa.verify.usertest` launches the project app/service and drives
  declared user or system flows through Playwright, browser/computer-use, or CLI
  replay.

Both validators must approve before finalization. For pure-library projects,
project metadata may declare `usertest_harness.kind = "none"` so scrutiny alone
can satisfy the validation contract.

## Required Worker Behavior

Every role must produce a structured handoff with completed work, undone work,
commands run, required procedure attestation, issues discovered, telemetry, and
artifact references. Task contracts should declare `required_procedures`; worker
results should declare `procedures_attestation` and `commands_run`. The worker
should not rely on future agents remembering its reasoning; any fact needed by
the next worker or validator belongs in the handoff or an artifact.

Every role invocation and repair phase must record wall-clock duration, model
cost, token usage, Token Savior savings, RTK/log-filter savings, model routing,
status, blocker, repair count, and artifact ids. Failed and blocked runs are as
important as successful runs for autonomy.

Validators are adversarial by default. They should try to disprove the worker's
claim, record attempted breaks, and emit typed evidence rather than just a
verdict.
