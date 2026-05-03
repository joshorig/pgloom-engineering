# Implementor brief — QA Engineer (test author + verify + sign‑off)

> **Status: CONTRACT READY; HANDLER BLOCKED.** The planner now has explicit `qa.author`
> and `qa.verify` task-slice contracts enforced by checks 7b/7c/7d, so this brief is the
> QA contract source for the next handler. Do not implement the QA worker yet: it still
> depends on Track D worktree/GitHub support, shared rubric extraction, and a concrete
> `engineering.feature_finalize` pre-gate consumer.

---

## 1. The shift from legacy QA

Legacy QA was a `subprocess.run` wrapper around `qa/smoke.sh` and `qa/regression.sh` that captured logs and graded pass/fail. The new QA is a **code‑producing engineer agent** whose responsibilities span:

1. Writing failing tests *before* the Implementer (test‑first, against `acceptance_test_matrix`).
2. Running the full test suite + the full app under per‑project resource lock *after* the Reviewer.
3. Identifying residual coverage gaps and closing them with additional tests (still under the same add‑or‑strengthen constraint).
4. Issuing a structured **sign‑off verdict** that gates the future `engineering.feature_finalize` task. No PR finalizes without an `approved` row in `engineering_qa_signoffs`.

This is a meaningful authority shift. QA is no longer advisory; it is the merge gate.

---

## 2. Architecture (locked decisions)

### 2.1 Two task types, one handler

| Task type | When | Reads | Writes |
|---|---|---|---|
| `engineering.qa.author` | After Planner, before Implementer | `PlanContract.acceptance_test_matrix` | One failing test per matrix row (or per logical group with declared coverage map) |
| `engineering.qa.verify` | After Reviewer | Upstream `task_result` + `review_verdict` + `qa_author_contract` handoffs | Full‑suite + full‑app evidence; additional gap‑closing tests; sign‑off verdict |

Both task types route to a single handler module `pgloom_engineering/roles/qa_engineer.py` with two entry methods (`handle_author` / `handle_verify`) selected by `task["task_type"]`.

### 2.2 Slot routing

- New row in pgloom `slots` table: `slot_name='qa-engineer'`, `concurrency=1`.
- Both QA task types declare `slot='qa-engineer'`.
- Initially the worker process for that slot is colocated with other engineering workers on the same host (`pgloom-engineering worker --slot qa-engineer` runs alongside the planner / implementer / reviewer worker processes).
- When the dedicated Mac mini is provisioned, the operator stops the colocated `--slot qa-engineer` worker and starts an equivalent process on the Mac mini. **No schema change. No code change.** Just process placement.
- **SPOF accepted by design.** If the worker host is offline, QA queues and feature finalization waits. This is the hardware‑reliability forcing function discussed in the Autonomy Contract decision.
- Per‑project resource lock via `pgloom.resource_locks` keyed `(project, "full_app_run")` so two features against the same project do not race the full‑app teardown. Acquired by `qa.verify` for the duration of the full‑app run.

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

class QAResultContract(BaseModel):              # extend existing
    contract_version: str = CONTRACT_VERSION
    feature_id: str
    task_id: str
    verdict: Literal["pass", "fail", "inconclusive"]
    commands: list[list[str]]                   # full set of commands run (smoke, regression, full-app, etc.)
    evidence: list[str]                         # artifact ids of logs / coverage reports
    findings: list[dict[str, Any]]              # structured failures (file, line, message, severity)
    tests_added: list[str] = []                 # NEW — gap-closing tests added during verify
    tests_strengthened: list[str] = []          # NEW — assertions tightened during verify
    deletions: list[dict[str, Any]] = []        # NEW — documented removals (intent, surviving_coverage)
    full_app_run: dict[str, Any] | None = None  # NEW — {duration_s, exit_code, log_artifact_id, project_lock_id}
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
        if ttype == "engineering.qa.verify":
            return self.handle_verify(task)
        return HandlerResult(status="blocked",
                             blocker_code="engineering.qa_unknown_task_type",
                             blocker_reason=f"unsupported task_type: {ttype}")

    def handle_author(self, task: dict[str, Any]) -> HandlerResult: ...
    def handle_verify(self, task: dict[str, Any]) -> HandlerResult: ...
```

Both methods use a worktree (Track D — must be done before this brief), `CLIModelProvider` invocations to author tests, `run_bounded` for executing tests, and `pgloom.artifacts.register_artifact` for log capture.

---

## 3. Hard prerequisites

This handler implementation is unsafe to start until all of these are true:

1. The planner brief at `docs/prompts/planner-impl-and-review.md` is fully landed; planner produces valid `PlanContract`s containing both `qa.author` and `qa.verify` slices for `lvc-standard` R‑002 (planner critic checks 7b/7c/7d enforce this). **Status: satisfied at contract level; keep the live R‑002 verification as the regression gate.**
2. Track D (worktree + git + GitHub integration) is at least partially landed — QA needs to create branches, push, and either open PRs or attach commits to the existing feature branch.
3. The shared rubric layer extracted from the planner critic (`CheckDefinition`, `RubricRunner`, `RubricVerdict`, `revise_until_clean`) is available; QA reuses it for its own per‑gap critique loop.
4. `engineering.feature_finalize` task type spec exists (even as a stub) so the sign‑off pre‑gate has a concrete consumer.

If any prerequisite is missing, write the missing piece first and come back.

---

## 4. Test surface (sketch — to be expanded)

### Unit tests
- `tests/unit/test_qa_diff_policy.py` — exhaustive: each allowed transformation, each refused transformation, edge cases around parametrize / fixtures / docstring‑only changes / whitespace / rename‑with‑content‑intact.
- `tests/unit/test_qa_engineer_handler.py` — both entry points with FakeCLIModelProvider; assert structured outputs match the contract schemas.
- `tests/unit/test_qa_signoffs.py` — table CRUD + unique index enforcement + cascade on feature delete.

### Integration tests (Postgres‑gated)
- `tests/integration/test_qa_author_lvc_r002.py` — given a persisted `PlanContract` for R‑002 (snapshot/restore), `engineering.qa.author` writes ≥ 1 test per acceptance criterion, proves each red on the as‑read worktree, persists `QAAuthorContract`, records `qa_author` handoff to each downstream implementer task.
- `tests/integration/test_qa_verify_signs_off.py` — given a feature past Reviewer, `engineering.qa.verify` runs full suite, persists extended `QAResultContract`, writes `engineering_qa_signoffs` row with `verdict='approved'`, finalize pre‑gate now passes (when finalize ships).
- `tests/integration/test_qa_verify_blocks_finalization.py` — same setup but verify returns `verdict='needs_implementer_fix'`; assert no approved row exists, finalize pre‑gate would refuse, a new `engineering.implement` slice is enqueued with the QA findings in its TaskContract.
- `tests/integration/test_qa_diff_policy_blocks_weakening.py` — feed a deliberately weakening fixture diff (e.g. `< 10ms` → `< 100ms`); assert post‑gate refuses with `engineering.qa_test_weakening` recovery row.

### CLI smoke
- `pgloom-engineering qa author --feature <id>` and `pgloom-engineering qa verify --feature <id>` — mirror the planner `plan dry-run` pattern.

---

## 5. Acceptance gate (preliminary — to be hardened on full write‑up)

1. `ruff check` + `mypy` clean across new modules.
2. All new unit + integration tests green; existing test suite unaffected.
3. Migration `007_qa_signoffs.sql` applied idempotently; unique partial index enforced (concurrent insert of two `approved` rows for the same feature fails the second).
4. End‑to‑end happy path on `lvc-standard` R‑002: planner produces plan with both QA phases → qa.author writes ≥ 5 red tests covering snapshot/restore acceptance criteria → implementer (mocked or real) turns them green → reviewer approves → qa.verify runs full app under resource lock → signoff row with `approved` verdict → finalize pre‑gate accepts.
5. Add‑or‑strengthen enforcement: a parallel test feeds a weakening diff and asserts the gate refuses with a structured violation list.
6. Slot routing: a worker registered with `--slot qa-engineer` claims both `qa.author` and `qa.verify` tasks; a worker registered with any other slot does not claim them.
7. Resource lock: two concurrent `qa.verify` tasks for the same project serialize on the project lock; for different projects they run in parallel.
8. No edits to `pgloom` itself, no edits to the planner critic, no edits to the worker pre/post gates beyond what is required to teach the verify pre‑gate to require the upstream `qa_author_contract` handoff.

---

## 6. Reference paths

| What | Where |
|---|---|
| Master plan §Track B (QA split) | `/Volumes/devssd/repos/oss/pgloom/docs/plans/engineering-orchestrator-port.md` |
| Autonomy Contract rule 9 (QA gates merge, add‑or‑strengthen) | same file, § Autonomy Contract |
| Planner brief (defines `qa.author` + `qa.verify` slice contracts via critic checks 7b/7c/7d) | `/Volumes/devssd/repos/oss/pgloom-engineering/docs/prompts/planner-impl-and-review.md` |
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

- The planner critic enforces that every plan includes both `qa.author` and `qa.verify` slices with disjoint paths from Implementer (planner brief §4 checks 7b/7c/7d).
- The Reviewer brief, when written, must consume QA's red tests as the definition‑of‑done signal — Implementer's `TaskResultContract.checks` should include the QA‑authored tests' exit codes flipping from non‑zero to zero.
- If during planner shipping you discover that the QA contract surface needs additions (e.g. flake history, coverage targets, tolerance budgets), record them in `docs/reports/planner-impl-and-review-completion.md` under a "QA contract gaps" section so this brief picks them up when fully written.
- Do not begin handler implementation until Track D, shared rubric extraction, and `engineering.feature_finalize` are specified enough for QA sign-off to have a concrete branch and merge gate to control.
