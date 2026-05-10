# Implementor brief - autonomy verification and handoff architecture

> **Status: planning brief.** Do not implement this as a prompt-only
> orchestrator. The target is typed contracts, deterministic gates, durable
> telemetry, and adversarial validators. Humans define goals up front and merge
> the final PR; agents handle planning, QA, implementation, review, validation,
> repair, and evidence collection.

## 1. Target Architecture

The autonomous path is:

1. Human registers project metadata and creates a feature goal.
2. Planner emits a `PlanContract` with milestones, task slices, validation
   contracts, grading criteria, required procedures, and assertion coverage.
3. QA author writes red tests and records `QAAuthorContract` evidence.
4. Implementer makes the QA worktree green and emits a compact handoff.
5. Reviewer performs adversarial contract review.
6. `engineering.qa.verify.scrutiny` runs lint, type, tests, deterministic gates,
   and fresh-context code-review agents per completed feature.
7. `engineering.qa.verify.usertest` launches the app/service and exercises real
   flows through browser, computer-use, Playwright, or CLI replay.
8. Both validators approve before milestone or final signoff.
9. Validation failures create targeted corrective slices or milestone replans.
10. Human finalization remains PR review and merge.

Do not adopt a mutable skill-learning loop or meta-orchestration yet. Preserve
the current pgloom task runtime and grow typed engineering contracts around it.

## 2. Milestones

Add a first-class `MilestoneContract` to `PlanContract`:

```python
class MilestoneContract(BaseModel):
    milestone_id: str
    name: str
    slice_ids: list[str]
    acceptance_assertions: list[str]
    validation_contract: dict[str, Any]
    depends_on: list[str] = []
    signoff_policy: Literal["scrutiny_and_usertest", "scrutiny_only"]
```

Downstream slices for a later milestone are not claimable until all prerequisite
milestones are signed off. If validation fails, the recovery path should prefer
a `corrective_slice` action before a full replan.

## 3. Validator Split

`engineering.qa.verify.scrutiny`:

- uses fresh context and should not receive implementation reasoning
- runs declared lint/build, feature-specific tests, and smoke/benchmark-smoke commands
- fans out fresh code-review agents for each completed feature or slice
- records attempted breaks, findings, residual risks, confidence, and evidence

`engineering.qa.verify.usertest`:

- uses fresh context and should not receive implementation reasoning
- acquires a per-project full-app resource lock
- starts the app/service or CLI system under test
- drives real user/system flows, including forms, clicks, navigation, state
  transitions, API/CLI replay, and rendered output checks
- stores screenshots, traces, logs, command output, and final state artifacts

If project metadata declares `usertest_harness.kind = "none"`, the user-test
phase may be skipped for pure-library projects. The signoff must record that
the skip was metadata-authorized.

## 4. Handoff Envelope

Every role result should be wrapped in a compact `HandoffEnvelope`:

```python
class HandoffEnvelope(BaseModel):
    handoff_id: str
    handoff_type: Literal[
        "plan_to_task", "worker_result", "validation", "recovery", "final"
    ]
    feature_id: str
    task_id: str | None = None
    role: str
    summary: str
    completed: list[str]
    left_undone: list[str]
    commands_run: list[CommandRun]
    procedures_attestation: dict[str, bool | str]
    issues_discovered: list[dict[str, Any]] = []
    next_worker_context: str
    reviewer_context: str
    diagnostics: dict[str, Any] = {}
    telemetry_summary: dict[str, Any]
    evidence: list[ValidationEvidence] = []
    artifact_ids: list[str] = []
    payload: dict[str, Any]
```

Large prompts, responses, logs, diffs, screenshots, traces, and reports must be
stored as artifacts. Handoffs keep summaries and artifact ids.

## 5. Recovery

Treat recovery as a normal handoff with `handoff_type="recovery"`. The recovery
payload should include prior plan hash, milestone id, validator findings, failed
commands, artifact ids, blocker codes, and a recommended action:

- `corrective_slice`: planner narrow mode emits at most three new slices and
  appends them to the existing plan.
- `milestone_replan`: planner replans from a milestone boundary while preserving
  signed-off earlier milestones.
- `human_escalation`: reserved for policy ambiguity, missing requirements, or
  repeated failure after retry budget.

Validation failure is expected orchestration data, not an exceptional crash.

## 6. Acceptance Tests For Later Implementation

- Unit tests for milestone locks, validator signoff policy, `HandoffEnvelope`
  validation, recovery handoff parsing, and metadata-authorized user-test skip.
- Integration test:
  planner -> QA author -> implementer -> reviewer -> QA scrutiny -> QA
  user-test -> milestone signoff -> final evidence.
- Failure test where scrutiny rejects a feature and recovery creates a
  `corrective_slice` without rewriting unrelated plan slices.
