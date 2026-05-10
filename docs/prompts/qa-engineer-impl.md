# Implementor brief — QA Engineer (test author + scrutiny/user-test validators + sign-off)

> **Status: QA AUTHOR LIVE; QA VERIFY PENDING.** The `engineering.qa.author`
> path now runs in isolated worktrees, writes tests, validates required project
> gates, runs deterministic semantic quality checks, and emits a
> `QAAuthorContract`. The remaining work in this brief is to split
> `engineering.qa.verify` into `engineering.qa.verify.scrutiny` and
> `engineering.qa.verify.usertest`, add typed validation evidence and handoffs,
> and wire sign-off/finalization gating.

---

## 1. The shift from legacy QA

Legacy QA was a `subprocess.run` wrapper around `qa/smoke.sh` and `qa/regression.sh` that captured logs and graded pass/fail. The new QA is a **code‑producing engineer agent** whose responsibilities span:

1. Writing failing tests *before* the Implementer (test‑first, against `acceptance_test_matrix`).
2. Running scrutiny validation *after* Reviewer: lint, type checks, deterministic
   gates, full suites, and fresh-context code-review agents.
3. Running user-test validation *after* scrutiny: live app/service launch plus
   browser/computer-use/Playwright/CLI replay evidence.
4. Identifying residual coverage gaps and closing them with additional tests
   (still under the same add‑or‑strengthen constraint).
5. Issuing a structured **sign‑off verdict** that gates the future
   `engineering.feature_finalize` task. No PR finalizes unless scrutiny and
   user-test both approve, except metadata-authorized pure-library skips.

This is a meaningful authority shift. QA is no longer advisory; it is the merge gate.

---

## 2. Architecture (locked decisions)

### 2.1 Three task types, one handler

| Task type | When | Reads | Writes |
|---|---|---|---|
| `engineering.qa.author` | After Planner, before Implementer | `PlanContract.acceptance_test_matrix` | One failing test per matrix row (or per logical group with declared coverage map) |
| `engineering.qa.verify.scrutiny` | After Reviewer | Upstream `task_result` + `review_verdict` + `qa_author_contract` handoffs, validation contract | Lint/type/test evidence, deterministic gate output, fresh-context code-review findings |
| `engineering.qa.verify.usertest` | After scrutiny | Scrutiny result, validation contract, `usertest_harness` project metadata | Live app/service exercise evidence, screenshots/traces/logs, user-test verdict |

All QA task types route to a single handler module
`pgloom_engineering/roles/qa_engineer.py` with entry methods
`handle_author`, `handle_scrutiny`, and `handle_usertest` selected by
`task["task_type"]`.

### 2.2 Slot routing

- New row in pgloom `slots` table: `slot_name='qa-scrutiny'`,
  `concurrency=1`.
- New row in pgloom `slots` table: `slot_name='qa-usertest'`,
  `concurrency=1`.
- `engineering.qa.author` and `engineering.qa.verify.scrutiny` declare
  `slot='qa-scrutiny'`.
- `engineering.qa.verify.usertest` declares `slot='qa-usertest'`.
- Initially both worker processes can be colocated with other engineering
  workers (`pgloom-engineering worker --slot qa-scrutiny` and
  `pgloom-engineering worker --slot qa-usertest`).
- When dedicated hardware is provisioned, operators can move either slot by
  stopping the colocated worker and starting an equivalent process elsewhere.
  **No schema change. No code change.** Just process placement.
- **SPOF accepted by design.** If the worker host is offline, QA queues and feature finalization waits. This is the hardware‑reliability forcing function discussed in the Autonomy Contract decision.
- Per‑project resource lock via `pgloom.resource_locks` keyed
  `(project, "full_app_run")` so two features against the same project do not
  race the full‑app teardown. Acquired by `qa.verify.usertest` for the duration
  of the full‑app run. User tests for different projects must be able to run in
  parallel.

### 2.3 Add‑or‑strengthen post‑gate

This is the load‑bearing safety mechanism. QA's `allowed_paths` is restricted to `tests/**` and `qa/fixtures/**`; the post‑gate further enforces *what kinds of edits* are allowed within those paths. Implemented in a new module `pgloom_engineering/qa/diff_policy.py`.

**Allowed.**
- New test files (no prior version on the branch base).
- New test methods or `def test_*` blocks within existing files.
- New `assert*` lines added inside existing tests.
- New `pytest.param` entries within parameterized tests.
- New fixtures.
- Strict tightening of an assertion: a numeric bound becoming smaller (`< 100ms` → `< 10ms`, `epsilon=0.1` → `epsilon=0.01`, `timeout=30` → `timeout=5`).
- Documented test removal where the `QAResultContract.deletions[]` declares `intent="remove_redundant_coverage"` AND names the surviving test that covers the same surface.

**Refused.**
- Deleted `assert*` / `assertEqual` / `assertTrue` / `assertNotNull` / `pytest.raises` / `expectThrows` / `assertThat` etc. lines.
- Deleted test methods or `def test_*` blocks (without the documented‑removal exception above).
- Deleted test files (without the documented‑removal exception above).
- Numeric tolerance / timeout / epsilon / threshold widening (`< 10ms` → `< 100ms`).
- Any `@pytest.mark.skip`, `@pytest.mark.xfail`, `@Disabled`, `@Ignore` annotation added to an existing test.
- Removal of `@pytest.mark.parametrize` cases.
- Replacement of a strict equality with a softer comparison (e.g. `assertEqual(x, 1.0)` → `assertAlmostEqual(x, 1.0, delta=0.5)`).

The post‑gate's `enforce_add_or_strengthen(diff: str) -> list[Violation]` runs against the unified diff between the QA branch and its base. v1 implementation is regex‑based with conservative defaults — flagged‑but‑uncertain cases become advisory findings rather than hard rejects. The 80/20 cut is fine; the QA critic rubric covers the remaining edge cases.

Violations transition the QA task to `blocked` with `RecoveryDecisionContract(blocker_code="engineering.qa_test_weakening", action="block_execution")` and the violation list serialized into `outcome`.

### 2.3a Required gate validation and semantic QA review

The QA author path has two deterministic gates after model generation and
before the task can report success:

1. `validate_required_qa_gates(worktree, project_metadata)` checks that every
   project-declared feature QA gate has a concrete script or command in the
   worktree. Runtime, smoke, UI, and benchmark-smoke commands must come from
   project metadata, not model inference. Full regression gates are periodic
   project validation, not per-feature QA-author or QA-scrutiny blockers.
2. `review_semantic_quality(changed_files, project_metadata)` blocks weak tests
   that only inspect scripts/build files, call Spring controllers directly when
   HTTP routing is the contract, use brittle raw JSON/stringification checks,
   mismatch journal cursor semantics, or create JMH benchmarks that reuse targets
   or allocate measured-state objects after setup.

Project metadata is the source of truth for generic conventions. Examples:

- `required_gates` names per-feature smoke/benchmark-smoke/full-app commands
  that must exist; `periodic_gates` names full regression sweeps.
- `qa_metadata.test_roots`, `source_roots`, and `explicit_test_examples` bound
  discovery without asking the model to guess.
- `semantic_conventions.build_hook_tests.deterministic_gate_validation_required`
  prevents tests from pretending script-string assertions are runtime coverage.
- `semantic_conventions.benchmarks` declares project-neutral JMH expectations
  such as cold restore setup and zero garbage after benchmark setup.

The live worker uses the same gate and semantic modules as the eval harness, so
an eval pass corresponds to production dispatch behavior.

### 2.4 Sign‑off table

New migration `pgloom_engineering/db/schema/007_qa_signoffs.sql`:

```sql
create table if not exists engineering_qa_signoffs (
  id bigserial primary key,
  feature_id text not null references engineering_features(workflow_id) on delete cascade,
  qa_task_id text not null,
  verdict text not null check (verdict in ('approved', 'rejected', 'needs_implementer_fix', 'needs_planner_replan')),
  rationale text not null,
  qa_result_contract jsonb not null,
  created_at timestamptz not null default now()
);
create unique index if not exists idx_qa_signoffs_feature_approved
  on engineering_qa_signoffs(feature_id) where verdict = 'approved';
create index if not exists idx_qa_signoffs_feature_recent
  on engineering_qa_signoffs(feature_id, created_at desc);
```

The unique partial index on `(feature_id) where verdict = 'approved'` is the gate primitive. A future `engineering.feature_finalize` worker pre‑gate runs:

```sql
select 1 from engineering_qa_signoffs
where feature_id = $1 and verdict = 'approved';
```

and refuses dispatch if no row.

Verdict semantics:

- `approved` → finalize unblocked.
- `rejected` → feature is dead; the handler additionally writes a `RecoveryDecisionContract(action="planner_replan")` so the council can re‑plan if appropriate.
- `needs_implementer_fix` → handler enqueues a fresh `engineering.implement` slice with `depends_on` pointing back at the verify task and a `TaskContract` whose `inputs.qa_findings` carries the structured failure list. Implementer addresses the findings; QA.verify re‑runs (a new task; previous verdict stays for audit).
- `needs_planner_replan` → handler enqueues a planner replan with the QA verdict as input.

### 2.5 Contract extensions

Add to `pgloom_engineering/contracts.py`:

```python
class QAAuthorContract(BaseModel):
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    tests_added: list[str]                      # filenames or fully-qualified test names
    matrix_coverage: dict[str, list[str]]       # acceptance criterion → tests covering it
    red_proof: list[dict[str, Any]]             # [{test: str, command: list[str], exit_code: int, output_excerpt: str}]
    paths_touched: list[str]                    # for diff-policy validation

class ValidationEvidence(BaseModel):
    evidence_id: str
    kind: Literal[
        "test_run",
        "code_review",
        "ui_exercise",
        "integration_check",
        "lint_type_check",
        "benchmark",
        "screenshot",
        "network_trace",
        "command_log",
    ]
    summary: str
    verdict: Literal["pass", "fail", "inconclusive"]
    artifact_ids: list[str] = []
    metadata: dict[str, Any] = {}

class CommandRun(BaseModel):
    cmd: list[str]
    exit_code: int | None
    duration_s: float
    artifact_ids: list[str] = []

class QAResultContract(BaseModel):              # extend existing
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    verdict: Literal["pass", "fail", "inconclusive"]
    commands_run: list[CommandRun]              # commands with exit codes/durations/artifacts
    evidence: list[ValidationEvidence]          # typed evidence, not only artifact ids
    findings: list[dict[str, Any]]              # structured failures (file, line, message, severity)
    procedures_attestation: dict[str, bool | str]
    tests_added: list[str] = []                 # NEW — gap-closing tests added during verify
    tests_strengthened: list[str] = []          # NEW — assertions tightened during verify
    deletions: list[dict[str, Any]] = []        # NEW — documented removals (intent, surviving_coverage)
    full_app_run: dict[str, Any] | None = None  # NEW — {duration_s, exit_code, log_artifact_id, project_lock_id}
    scrutiny_verdict: Literal["approved", "rejected", "inconclusive"] | None = None
    usertest_verdict: Literal["approved", "rejected", "skipped", "inconclusive"] | None = None
    usertest_skip_reason: str | None = None
    signoff_verdict: Literal[
        "approved", "rejected", "needs_implementer_fix", "needs_planner_replan"
    ]                                            # NEW — drives the engineering_qa_signoffs row
    signoff_rationale: str                      # NEW
```

`QAAuthorContract` is new. `QAResultContract` extension is additive; existing fields preserved.

### 2.6 Handler entry points

```python
class QAEngineerHandler:
    def __init__(self, *, provider: CLIModelProvider, diff_policy: DiffPolicy,
                 project_repo: ProjectRepoAccessor) -> None: ...

    def handle(self, task: dict[str, Any]) -> HandlerResult:
        ttype = task["task_type"]
        if ttype == "engineering.qa.author":
            return self.handle_author(task)
        if ttype == "engineering.qa.verify.scrutiny":
            return self.handle_scrutiny(task)
        if ttype == "engineering.qa.verify.usertest":
            return self.handle_usertest(task)
        return HandlerResult(status="blocked",
                             blocker_code="engineering.qa_unknown_task_type",
                             blocker_reason=f"unsupported task_type: {ttype}")

    def handle_author(self, task: dict[str, Any]) -> HandlerResult: ...
    def handle_scrutiny(self, task: dict[str, Any]) -> HandlerResult: ...
    def handle_usertest(self, task: dict[str, Any]) -> HandlerResult: ...
```

Both methods use a worktree (Track D — must be done before this brief), `CLIModelProvider` invocations to author tests, `run_bounded` for executing tests, and `pgloom.artifacts.register_artifact` for log capture.

### 2.7 Project metadata for user-test

Project metadata must declare `usertest_harness`:

```yaml
usertest_harness:
  kind: playwright | browser | computer_use | cli_replay | none
  launch_command: ["./qa/full-app.sh"]
  health_check: "http://127.0.0.1:8080/health"
  flows:
    - id: create-and-edit-record
      entrypoint: "http://127.0.0.1:8080/"
      assertions:
        - "form accepts valid input"
        - "saved item appears after reload"
```

`kind = "none"` is only valid for pure-library projects. The user-test result
must record the metadata path that authorized the skip.

---

## 3. Hard prerequisites

`engineering.qa.author` has satisfied the first implementation threshold. The
remaining handler work is unsafe to start until all of these are true:

1. The planner brief at `docs/prompts/planner-impl-and-review.md` remains the
   source of valid `qa.author`, `qa.verify.scrutiny`, and `qa.verify.usertest`
   slices. The live QA eval suite now
   covers LVC R-003, LVC R-002 JMH, TRP R-003, and DAG R-003 from real planner
   outputs/fixtures.
2. Track D worktree support is partially landed for local eval and QA author.
   The next production step is branch commit/push/PR wiring for worker-created
   worktrees.
3. The shared rubric layer is still desirable for scrutiny and user-test, but
   QA author v1 uses deterministic semantic checks plus targeted repair instead
   of a full model rubric loop.
4. Task contracts include `required_procedures`, and worker result contracts
   include `procedures_attestation` and `commands_run`.
4. `engineering.feature_finalize` task type spec exists (even as a stub) so the
   sign-off pre-gate has a concrete consumer.

If any prerequisite is missing, write the missing piece first and come back.

---

## 4. Test surface (sketch — to be expanded)

### Unit tests
- `tests/unit/test_qa_diff_policy.py` — exhaustive: each allowed transformation, each refused transformation, edge cases around parametrize / fixtures / docstring‑only changes / whitespace / rename‑with‑content‑intact.
- `tests/unit/test_qa_engineer_handler.py` — both entry points with FakeCLIModelProvider; assert structured outputs match the contract schemas.
- `tests/unit/test_qa_signoffs.py` — table CRUD + unique index enforcement + cascade on feature delete.
- `tests/unit/test_qa_runtime.py` — required-gate metadata and prompt-safe metadata extraction.
- `tests/unit/test_qa_semantic_review.py` — generic semantic checks for endpoint, payload, journal, benchmark, and script-string anti-patterns.
- `tests/unit/test_qa_author_eval_metadata.py` — eval prompt context, route coverage inventory, and project metadata consumption.

### Integration tests (Postgres‑gated)
- `tests/integration/test_qa_author_lvc_r002.py` — given a persisted `PlanContract` for R‑002 (snapshot/restore), `engineering.qa.author` writes ≥ 1 test per acceptance criterion, proves each red on the as‑read worktree, persists `QAAuthorContract`, records `qa_author` handoff to each downstream implementer task.
- `tests/integration/test_qa_scrutiny_signs_off.py` — given a feature past Reviewer, `engineering.qa.verify.scrutiny` runs lint/type/full suite, persists typed evidence, and records fresh-context code-review findings.
- `tests/integration/test_qa_usertest_signs_off.py` — after scrutiny approval, `engineering.qa.verify.usertest` launches the app/service or CLI harness, records UI/CLI replay evidence, writes `engineering_qa_signoffs` only when both validators approve.
- `tests/integration/test_qa_verify_blocks_finalization.py` — same setup but verify returns `verdict='needs_implementer_fix'`; assert no approved row exists, finalize pre‑gate would refuse, a new `engineering.implement` slice is enqueued with the QA findings in its TaskContract.
- `tests/integration/test_qa_diff_policy_blocks_weakening.py` — feed a deliberately weakening fixture diff (e.g. `< 10ms` → `< 100ms`); assert post‑gate refuses with `engineering.qa_test_weakening` recovery row.

### CLI smoke
- `pgloom-engineering qa author --feature <id>`,
  `pgloom-engineering qa scrutiny --feature <id>`, and
  `pgloom-engineering qa usertest --feature <id>` — mirror the planner
  `plan dry-run` pattern.

### Live eval

The repeatable QA author suite is:

```bash
uv run python scripts/run_qa_author_eval_suite.py \
  --suite docs/evals/qa-author-model-suite.json \
  --output-dir docs/reports/<run-name> \
  --model gpt-5.5 \
  --jobs 2
```

The current production threshold is one accepted QA author result for each
configured case, no deterministic semantic findings, required gates present,
and per-case API-equivalent cost under the suite threshold. The last reviewed
full run accepted all four cases after raising the TRP threshold to reflect the
real endpoint/UI coverage cost:

- LVC R-003 range: accepted, API-equivalent cost about `$0.80`.
- LVC R-002 JMH snapshot/restore: accepted, about `$1.25`.
- TRP R-003 config/diagnostics parity: accepted, about `$3.09`.
- DAG R-003 YAML loader: accepted, about `$1.03`.

---

## 5. Acceptance gate (preliminary — to be hardened on full write‑up)

1. `ruff check` + `mypy` clean across new modules.
2. All new unit + integration tests green; existing test suite unaffected.
3. Migration `007_qa_signoffs.sql` applied idempotently; unique partial index enforced (concurrent insert of two `approved` rows for the same feature fails the second).
4. End‑to‑end happy path on `lvc-standard` R‑002: planner produces plan with QA author, scrutiny, and user-test phases → qa.author writes stateful acceptance tests and cold/zero-garbage JMH coverage for snapshot/restore → implementer turns them green → reviewer approves → scrutiny approves → user-test approves or records metadata-authorized skip → signoff row with `approved` verdict → finalize pre‑gate accepts.
5. Add‑or‑strengthen enforcement: a parallel test feeds a weakening diff and asserts the gate refuses with a structured violation list.
6. Slot routing: `qa-scrutiny` claims `qa.author` and `qa.verify.scrutiny`; `qa-usertest` claims `qa.verify.usertest`.
7. Resource lock: two concurrent `qa.verify.usertest` tasks for the same project serialize on the project lock; for different projects they run in parallel.
8. No edits to `pgloom` itself, no edits to the planner critic, no edits to the worker pre/post gates beyond what is required to teach the verify pre‑gate to require the upstream `qa_author_contract` handoff.

---

## 6. Reference paths

| What | Where |
|---|---|
| Master plan §Track B (QA split) | `/Volumes/devssd/repos/oss/pgloom/docs/plans/engineering-orchestrator-port.md` |
| Autonomy Contract rule 9 (QA gates merge, add‑or‑strengthen) | same file, § Autonomy Contract |
| Planner brief (defines `qa.author` + split verify slice contracts via critic checks 7b/7c/7d) | `/Volumes/devssd/repos/oss/pgloom-engineering/docs/prompts/planner-impl-and-review.md` |
| Existing QA stub (the file this brief replaces) | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/roles/qa.py` |
| Contract definitions (extension target) | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/contracts.py` |
| Contract store CRUD | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/contract_store.py` |
| Worker pre/post gates (verify gate update target) | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/worker.py` |
| Resource lock primitive | `pgloom.resource_locks` |
| `lvc-standard` qa scripts | `/Volumes/devssd/repos/ull/lvc-standard/qa/{smoke,regression}.sh` |
| `lvc-standard` full‑app run target (TBD: identify exact gradle target or shell entry) | `/Volumes/devssd/repos/ull/lvc-standard/` — to be located when this brief is fully written |
| Legacy QA (read‑only, for reference) | `/Volumes/devssd/orchestrator/bin/worker.py` (search for `run_qa_task`) |

---

## 7. Until implementation starts

## 7. Next implementation wave

The next autonomous workflow phase is Implementer, not more QA author tuning.
Use the accepted QA author outputs as the red tests that Implementer must turn
green.

1. Complete the Git/worktree foundation for production: create a branch per
   feature or slice, detect changed files, commit, push, and later open PRs.
2. Implement `engineering.implement` against existing `TaskContract` and
   `PlanContract` handoffs. It must preserve QA-authored tests and may only edit
   allowed implementation paths.
3. Add an implementer post-gate that fails if tests are weakened, QA files are
   deleted, generated gate scripts are bypassed, or required project gates are no
   longer present.
4. Build an implementer eval suite using the same cases as QA author: LVC R-003,
   DAG R-003, TRP R-003, then LVC R-002 JMH.
5. Only after Implementer can reliably turn QA author red tests green should
   split `engineering.qa.verify.scrutiny` / `engineering.qa.verify.usertest`
   and finalization sign-off become the main focus.
