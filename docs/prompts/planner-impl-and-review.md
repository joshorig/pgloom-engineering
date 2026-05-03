# Implementor brief — Planner council + critic loop, validated against `lvc-standard` R‑002

> **Audience.** A coding agent (Claude / Codex via `pgloom.models.cli.CLIModelProvider`) with full read/write on `/Volumes/devssd/repos/oss/pgloom-engineering` and read-only access to `/Volumes/devssd/repos/ull/lvc-standard` and `/Volumes/devssd/orchestrator`. The agent should treat this brief as the complete spec; the only acceptable outputs are the surfaces defined in §3 and the tests defined in §6, evaluated against the acceptance gate in §7.
>
> **Working conventions** (from `/Volumes/devssd/orchestrator/CLAUDE.md` + Josh's handoff): concrete answers with file paths and line numbers; council reviews are skeptical not polite; risk tolerance is high (no dual‑run, no hold‑downs); when reporting "done", run `ruff check`, `mypy`, and `pytest` against the live tree before declaring victory.

---

## 1. Why this work exists

The autonomy contract in `/Volumes/devssd/repos/oss/pgloom/docs/plans/engineering-orchestrator-port.md` (§ Autonomy Contract) requires that **planning is always multi‑agent.** Today's `pgloom_engineering/roles/planner.py` is a contract validator + decomposer only — it expects a fully‑formed `PlanContract` injected into the task payload upstream. The "multi‑agent council" piece does not exist as code yet. This brief fills that gap.

The legacy orchestrator `tick_planner()` at `/Volumes/devssd/orchestrator/bin/orchestrator.py:9830` planned features as a single‑agent prompt against a hand‑maintained BRAID template. That approach repeatedly failed on stateful work (see `repo-memory/CURRENT_STATE.md`: `lvc-implement-operator` template hit a 46% topology error rate / 14 errors out of 30 runs). The pgloom‑engineering rewrite must do better by construction: ≥ 2 panelist agents draft candidate plans, a critic agent reviews them, and the loop iterates until the resulting `PlanContract` both passes `validate_plan_contract` and clears the critic's blocking findings.

The bring‑up test case is `lvc-standard` R‑002 (§ 5). It is deliberately stateful (snapshot/restore + journal cursor reconciliation), which means it triggers the lifecycle branch of `validate_plan_contract` at `pgloom_engineering/contracts.py:268-311`. A planner that can produce a clean `PlanContract` for R‑002 has demonstrably handled the failure class that broke the legacy planner.

---

## 2. Scope

**In scope.**

- New subpackage `pgloom_engineering/planner/` containing the council orchestrator, panelist runner, critic, consolidator, and prompt templates.
- Extension to `pgloom_engineering/roles/planner.py:PlannerHandler.handle()` so it can run the council when the task payload carries a `feature_goal_contract` (no pre‑built `plan_contract`).
- Persisting the panel proposals + critic findings into `PlanContract.council_reports` so they are auditable later via `contract_store.list_plan_contracts()`.
- Unit tests for each component using a fake `CLIModelProvider` and an integration test for the full R‑002 round‑trip.
- A small CLI verb `pgloom-engineering plan dry-run --feature-goal <path>` that runs the council against a JSON `FeatureGoalContract` and prints the resulting `PlanContract` + iteration trace, without enqueuing tasks.

**Out of scope (do not touch in this brief).**

- The Implementer / Reviewer / QA / Historian handlers (Track B work; separate brief later).
- BRAID graph runtime (Track C is parked; the critic is a bounded rubric prompt).
- Worktree / git / GitHub integration (Track D).
- Real API calls to Anthropic. Everything must run through `pgloom.models.cli.CLIModelProvider`. The integration test uses a fake CLI script in `tests/fixtures/`.
- Editing the master plan, the contracts schema, or the worker pre/post gates.

---

## 3. Required surfaces

### 3.1 Subpackage layout

```
pgloom_engineering/planner/
├── __init__.py                # exports PlannerCouncil, run_council
├── council.py                 # PlannerCouncil orchestrator (entry point)
├── panelist.py                # PanelistRunner — single planner agent
├── critic.py                  # CriticRunner — review-planner agent
├── consolidator.py            # Consolidator — merge N candidates → 1
├── prompts/
│   ├── panelist.md            # Prompt template for a panelist agent
│   ├── consolidator.md        # Prompt template for the consolidator
│   ├── critic.md              # Prompt template for the critic
│   └── revise.md              # Prompt template for revise-round panelists
└── exceptions.py              # PlannerCouncilExhausted, CandidateInvalid
```

### 3.2 Public API (Pydantic models + functions)

Add to `pgloom_engineering/planner/council.py`:

```python
class CouncilConfig(BaseModel):
    panelist_count: int = 3                   # >= 2; enforced at runtime
    max_iterations: int = 3                   # before raising PlannerCouncilExhausted
    panelist_profile: str                     # CLIModelProfile.name
    critic_profile: str                       # CLIModelProfile.name
    consolidator_profile: str                 # CLIModelProfile.name; may equal critic
    timeout_seconds_per_invocation: float = 300.0

class CouncilProposal(BaseModel):
    panelist_id: str                          # e.g. "panelist-0", "panelist-1"
    candidate: PlanContract                   # parsed + Pydantic-validated
    raw_response: str                         # for council_reports audit trail
    model_usage_id: int | None = None         # link into pgloom.model_usage

class CriticFinding(BaseModel):
    severity: Literal["blocking", "advisory"]
    check_id: str                             # one of the numbered checks in §4 (e.g. "check_lifecycle_coverage")
    code: str                                 # short kebab-case identifier (legacy field, may be redundant with check_id)
    slice_id: str | None = None               # null = plan-level
    message: str

class CriticCheckResult(BaseModel):
    """Outcome of one named rubric check. Designed to be reused by future
    Reviewer/QA rubric panels — see §11."""
    check_id: str                             # stable identifier from §4
    name: str                                 # human-readable name
    passed: bool
    severity_if_failed: Literal["blocking", "advisory"]
    findings: list[CriticFinding] = Field(default_factory=list)

class CriticVerdict(BaseModel):
    verdict: Literal["accept", "revise", "reject"]
    rationale: str
    findings: list[CriticFinding]              # flat aggregate across all checks (legacy convenience)
    per_check_results: list[CriticCheckResult] # structured per-check outcomes; the RubricRunner surface
    model_usage_id: int | None = None

class CouncilIteration(BaseModel):
    iteration: int                            # 1-indexed
    proposals: list[CouncilProposal]
    consolidated: PlanContract
    critic: CriticVerdict
    validator_errors: list[dict[str, str]]    # output of validate_plan_contract

class CouncilOutcome(BaseModel):
    final: PlanContract
    iterations: list[CouncilIteration]
    accepted_at_iteration: int

class PlannerCouncil:
    def __init__(
        self,
        *,
        config: CouncilConfig,
        provider: CLIModelProvider,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None: ...

    def run(self, *, feature_goal: FeatureGoalContract,
            project_context: ProjectContext) -> CouncilOutcome: ...
```

`ProjectContext` is a small new Pydantic model (also in `council.py`) with: `project_root: Path`, `roadmap_excerpt: str`, `decisions_excerpt: str`, `qa_smoke_path: Path`, `qa_regression_path: Path`, `relevant_paths: list[str]`. The handler builds it from filesystem reads (or test fixtures); the council does not do filesystem I/O itself.

`run()` returns the `CouncilOutcome` on success and raises `PlannerCouncilExhausted(iterations: list[CouncilIteration])` on failure (no infinite loops).

### 3.3 Component contracts

| Module | Class | Method | Returns / Raises |
|---|---|---|---|
| `panelist.py` | `PanelistRunner` | `propose(feature_goal, project_context, prior_iteration: CouncilIteration \| None) -> CouncilProposal` | Raises `CandidateInvalid(raw_response, parse_error)` if the model output cannot be parsed into a `PlanContract`. The council catches this and treats it as a degraded panelist for that round (does not abort the iteration unless **all** panelists fail). |
| `consolidator.py` | `Consolidator` | `merge(proposals: list[CouncilProposal]) -> PlanContract` | Returns one `PlanContract`. v1 strategy: invoke the consolidator agent with all candidates as input, ask it to emit a single merged plan. The consolidator's prompt requires it to preserve task slices that ≥ ⌈N/2⌉ panelists agreed on, and explicitly note in `assumptions` any slice it dropped. Falls back to "pick the candidate with the fewest validator errors" if the consolidator agent itself returns invalid JSON. |
| `critic.py` | `CriticRunner` | `review(plan: PlanContract, project_context, validator_errors: list[dict]) -> CriticVerdict` | Reviews against the rubric in §4. Verdict `accept` only if no blocking findings AND the validator errors list is empty. |
| `council.py` | `PlannerCouncil` | `run(...)` | Loops: propose → consolidate → critic + validate → decision; raises `PlannerCouncilExhausted` on max_iterations exceeded. |

### 3.4 Handler integration

Modify `pgloom_engineering/roles/planner.py:PlannerHandler.handle()` to add a new entry path **without removing the existing one**:

```python
def handle(self, task: dict[str, Any]) -> HandlerResult:
    payload = task.get("payload") or {}

    # NEW PATH: feature_goal_contract present, no pre-built plan_contract
    if payload.get("feature_goal_contract") and not payload.get("plan_contract"):
        return self._handle_council(task, payload)

    # EXISTING PATH (unchanged): plan_contract already present
    raw_contract = payload.get("plan_contract")
    if not raw_contract:
        return HandlerResult(status="blocked",
                             blocker_code="engineering.plan_contract_missing", ...)
    ...
```

`_handle_council()` builds the `ProjectContext`, instantiates `PlannerCouncil`, calls `run()`, persists `outcome.final` via the existing `create_plan_contract` + `enqueue_task` + `record_handoff` machinery (re‑use the existing decomposition loop verbatim — do not duplicate it), and writes `outcome.iterations` into `PlanContract.council_reports` before persistence. On `PlannerCouncilExhausted`, return `HandlerResult(status="blocked", blocker_code="engineering.planner_council_exhausted", ...)` with the iteration trace in `result` so the worker post‑gate writes a `RecoveryDecisionContract` action `replan` or `human_escalation`.

`PlannerHandler.__init__` must accept an optional `council: PlannerCouncil | None = None` for dependency injection (tests pass a fake; production constructs it from settings).

### 3.5 CLI verb

Add to `pgloom_engineering/cli.py`:

```python
@app.command("plan dry-run")  # or @plan_app.command("dry-run") if you nest
def plan_dry_run(
    feature_goal: Path = typer.Option(..., "--feature-goal",
                                      help="Path to a JSON FeatureGoalContract"),
    project_root: Path = typer.Option(..., "--project-root"),
    panelist_profile: str = typer.Option("planner-panelist"),
    critic_profile: str = typer.Option("planner-critic"),
    consolidator_profile: str = typer.Option("planner-consolidator"),
    max_iterations: int = typer.Option(3, "--max-iterations"),
) -> None:
    """Run the planner council against a feature goal without enqueueing anything.
    Prints the final PlanContract and the per-iteration trace to stdout as JSON.
    Exit code 0 on accept, 2 on PlannerCouncilExhausted."""
```

This verb is what the bring‑up test in §7.5 exercises. It must not touch the database — it instantiates the council, runs it, prints the outcome, exits.

### 3.6 Settings

Extend `pgloom_engineering/config.py:Settings` with:

```python
planner_panelist_profile: str = "planner-panelist"
planner_critic_profile: str = "planner-critic"
planner_consolidator_profile: str = "planner-consolidator"
planner_panelist_count: int = 3
planner_max_iterations: int = 3
planner_invocation_timeout_seconds: float = 300.0
```

All `PGLOOM_ENGINEERING_*` prefixed via `pydantic-settings`.

### 3.7 Per‑role gating (planner‑side enforcement)

The bring‑up test must register `lvc-standard` with the planner role enabled but the implementer role disabled — the implementer is still a stub (Track B not done), and we want to exercise the full planning workflow without ever dispatching the stub. The current `engineering_projects` schema has only project‑level `state` (no per‑role column), and this brief explicitly does not edit `worker.py` (per §2). The clean answer is a **planner‑side role gate** keyed off `engineering_projects.metadata`. No schema migration; no worker change.

**Convention.** `engineering_projects.metadata.role_gates` is an optional dict mapping role name (`"planner" | "implementer" | "reviewer" | "qa" | "historian"`) to `"enabled" | "disabled"`. Default for any unspecified role is `"enabled"`. Example:

```json
{
  "role_gates": {
    "planner": "enabled",
    "implementer": "disabled",
    "reviewer": "enabled",
    "qa": "enabled",
    "historian": "enabled"
  }
}
```

**New helper in `pgloom_engineering/projects.py`:**

```python
def role_enabled(project: ProjectConfig, role: str) -> bool:
    gates = (project.metadata or {}).get("role_gates") or {}
    return gates.get(role, "enabled") == "enabled"
```

This is a pure dict read; no DB call. Add unit tests covering: missing `metadata`, missing `role_gates`, missing role key (default enabled), explicit `"enabled"`, explicit `"disabled"`, unknown value (treated as `"disabled"` — fail‑closed).

**Planner enforcement.** In the existing decomposition loop in `pgloom_engineering/roles/planner.py`, wrap the `for task_slice in contract.task_slices:` body with a role‑gate check. Slices whose role is gated to `"disabled"` must:

1. **Not** be enqueued (`enqueue_task` skipped, `attach_task` skipped, `upsert_task_contract` skipped, `record_handoff` skipped).
2. Cause a `RecoveryDecisionContract` row to be written via `contract_store.record_recovery_action(decision, status="deferred", outcome={"slice_id": ..., "role": ..., "project": ...})`. The decision uses `blocker_code="engineering.role_gate_disabled"`, `action="block_execution"`, `attempt=1`, `max_attempts=1`, `rationale="role gated to disabled in engineering_projects.metadata.role_gates"`.
3. The deferred slice is recorded in the handler's `HandlerResult.result` under a new `deferred_slices: list[dict]` field so the council audit trail surfaces it (each entry: `{slice_id, role, reason}`).

The handler's overall verdict is still `done` — it is correct planner behavior to plan the work and gate the dispatch, not to fail. The `RecoveryDecisionContract` rows let self‑repair (or a human) flip the gate later without re‑planning.

**Defense in depth (deferred to next brief).** A future Track G hardening will teach the worker pre‑gate to consult `role_gates` too, so a hand‑injected task can't bypass the planner. That is *not* in this brief; the planner gate is sufficient for the bring‑up test.

---

## 4. Critic rubric (numbered named checks — the bounded rubric surface)

The critic enforces a fixed set of named checks. Each check has a stable `check_id` (used in `CriticCheckResult.check_id`), a human‑readable name, a severity if it fails, and a rubric statement. The critic prompt (`prompts/critic.md`) must list these checks verbatim and require the agent to emit one `CriticCheckResult` per check in the response. The critic's `verdict` is mechanically derived (not asked of the model): `accept` iff every blocking check `passed=true` AND `validator_errors == []`; `revise` if any blocking check failed and the iteration budget remains; `reject` only if a check failed in a way that suggests the goal itself is malformed (e.g. `check_finalization_policy` failed because the plan explicitly opts out of human PR merge).

The check IDs are the contract — they must remain stable across implementations because `per_check_results` rows persist into `PlanContract.council_reports` and future Reviewer/QA rubric panels will use the same bounded-check pattern.

| # | check_id | Name | Severity if failed | Rubric statement |
|---|---|---|---|---|
| 1 | `check_design_contract_completeness` | Design contract completeness | blocking | For lifecycle / persistence / concurrency work (detected by goal text matching the same lifecycle terms as `pgloom_engineering.contracts._validate_lifecycle_acceptance`), `design_contract.persistence_protocol` and `design_contract.concurrency_protocol` must be non‑empty. |
| 2 | `check_slice_path_coverage` | Slice path coverage | blocking | For every entry in `affected_surfaces`, at least one task slice's `allowed_paths` must include a path under that surface, and at least one slice must include the tests area for that surface. |
| 3 | `check_forbidden_path_overlap` | Forbidden‑path overlap | blocking | No slice's `forbidden_paths` may overlap any sibling slice's `allowed_paths` (drift risk: two slices claiming the same surface). Use prefix matching with `/` boundary. |
| 4 | `check_verification_commands` | Verification command coverage | blocking | Every slice with `role in {"implementer", "qa"}` must list at least one `verification_commands` entry that is either rooted at the project's qa scripts (e.g. `["./qa/smoke.sh"]`) or invokes a Gradle test task (e.g. `["./gradlew", "test"]` or `[":benchmarks:jmhSmokeCheck"]`). |
| 5 | `check_lifecycle_coverage` | Acceptance matrix lifecycle coverage | blocking | If `validator_errors` contains `planner_contract_incomplete`, this check fails; emit one finding per missing category (`stale_or_invalid`, `invariant`, `failure_path`) with `slice_id=null`. |
| 6 | `check_topology_consistency` | Implementation topology consistency | blocking | `implementation_topology == SINGLE` is incompatible with `> 1` slice having `role == "implementer"`. |
| 7a | `check_reviewer_present` | Reviewer slice presence | blocking | At least one slice with `role == "reviewer"` must exist. (Multi‑agent review enforcement happens later, at Reviewer dispatch.) |
| 7b | `check_qa_author_present` | QA author slice present (test‑first) | blocking | At least one slice with `task_type == "engineering.qa.author"` must exist, scheduled **before** every implementer slice in the `depends_on` DAG. Its `allowed_paths` must be restricted to `tests/**` and/or `qa/fixtures/**` and must not overlap any implementer slice's `allowed_paths`. |
| 7c | `check_qa_verify_present` | QA verify + sign‑off slice present | blocking | At least one slice with `task_type == "engineering.qa.verify"` must exist, scheduled **after** every reviewer slice. Its `allowed_paths` is restricted to `tests/**` and `qa/fixtures/**`. Its `verification_commands` must include a full‑suite invocation (e.g. `["./qa/regression.sh"]` or equivalent) AND the project's smoke command. |
| 7d | `check_qa_paths_disjoint` | QA paths disjoint from source | blocking | No QA slice (`qa.author` or `qa.verify`) may have an `allowed_paths` entry that overlaps any implementer slice's `allowed_paths`. Conversely, no implementer slice may include a `tests/**` or `qa/fixtures/**` entry in its `allowed_paths` (Implementer is free to read tests but not to write them). |
| 8 | `check_orphan_slices` | Orphan slice detection | advisory | A slice that no later slice depends on, and whose role is neither `reviewer` / `qa` / `historian` (terminal roles), is likely orphan work. Emit advisory findings; do not block. |
| 9 | `check_finalization_policy` | Finalization policy locked to human merge | blocking | `finalization_policy == "open_final_feature_pr_for_human_merge"`. (Already enforced by `validate_plan_contract`; critic echoes it for audit symmetry.) |
| 10 | `check_objective_specificity` | Objective specificity | advisory | Each slice's `objective` is at least one sentence and references at least one concrete artifact (a file path, a class name, a test name, a metric). Vague objectives ("implement the feature") are flagged advisory. |
| 11 | `check_risk_register_present` | Risk register present | advisory | `PlanContract.risk_register` is non‑empty for any plan whose goal mentions `lifecycle / snapshot / restore / persistence / concurrency`. Empty registers on lifecycle work are flagged advisory. |
| 12 | `check_roadmap_dependency_handling` | Roadmap dependency handling | blocking | Plans for dependency-gated roadmap items must block, narrow, or explicitly sequence prerequisites instead of silently planning against unavailable foundations. Current deterministic coverage includes R‑004/R‑006 requiring the R‑002 snapshot prerequisite to be acknowledged. |
| 13 | `check_hot_path_invariants` | Hot-path invariant preservation | blocking | Plans must not schedule implementation work that violates explicit zero-allocation or hot-path constraints, such as putting LZ4 compression/allocation on the publish path. |
| 14 | `check_small_feature_compactness` | Small-feature compactness | blocking | Small or single-surface roadmap items should use a compact handoff, typically design → qa.author → implementer → reviewer → qa.verify, with 4-6 slices unless the feature has clear multi-surface risk. |

**Implementation note for the critic.** The current implementation is **a single CLI invocation** that emits one JSON document containing all `CriticCheckResult` rows listed in `RUBRIC_CHECKS`. The prompt template (`prompts/critic.md`) must instruct the agent to output the JSON with one entry per check by `check_id`. Missing `check_id`s in the response are treated as `passed=false, severity=blocking, findings=[{check_id, code="critic_did_not_evaluate_check", ...}]`. The `verdict` field on `CriticVerdict` is computed by the `CriticRunner` from the per‑check results, **not** asked of the model — the model is responsible for evidence per check, not for the final yes/no.

This structure is the contract that lets §11's shared `RubricRunner` extraction reuse the implementation pattern without changing the rest of the planner.

---

## 5. Test case — `lvc-standard` R‑002 (snapshot/restore for SINGLE + DOUBLE stores)

The R‑002 entry lives at `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/ROADMAP.md:19-27`. Quoted verbatim:

```
### [R-002] Snapshot/restore API for SINGLE + DOUBLE stores
- **Status:** TODO
- **Feature id:** null
- **Goal:** Scheduled checkpoint of the full store to disk + atomic swap on restore,
  collapsing cold-start time from journal replay to mmap'd reload.
- **Scope:** `Store.snapshot(Path)`, `Store.restore(Path)`, snapshot format with magic+version
  header, CRC per page, integration with guaranteed journal cursor so restore picks up from
  checkpoint offset.
- **Out of scope:** Incremental snapshots, cross-version schema evolution (R-005 territory),
  distributed coordination.
- **Acceptance:** Full round-trip test (write → snapshot → kill → restore → read), alloc
  gate unaffected, restore latency < 10ms for 1M keys.
- **Depends on:** none.
- **Notes:** Must respect DECISIONS entry on atomic `publishChecked` semantics — restore must
  leave staged-but-unjournaled writes visible only after journal cursor reconciliation.
```

Additional context the implementor brief includes for the planner's prompt (the `_handle_council` path passes these as the `ProjectContext` strings):

- **`publishChecked` semantics**, from `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/DECISIONS.md` 2026‑04‑xx entry: `GuaranteedPublisher.publishChecked` stages store + journal writes atomically; on journal failure the store write is aborted. Restore must reconcile against the journal cursor so partial writes do not become visible.
- **QA scripts**: `/Volumes/devssd/repos/ull/lvc-standard/qa/smoke.sh` runs `./gradlew --no-daemon test` + `:benchmarks:jmhSmokeCheck` (the alloc gate; do not skip the `rm -rf benchmarks/build` step or `-Pjmh.smoke=true` becomes a no‑op). `qa/regression.sh` is the full JMH sweep.
- **Module layout** (from `ls /Volumes/devssd/repos/ull/lvc-standard`): `core/`, `store/`, `signal/`, `guaranteed-aeron/`, `guaranteed-inproc/`, `sbe-adapters/`, `benchmarks/`, `conformance-tests/`, `qa/`, `repo-memory/`, top‑level `build.gradle` + `gradle.properties`.

The `FeatureGoalContract` the bring‑up test passes to the council:

```json
{
  "contract_version": "engineering.contracts.v1",
  "project": "lvc-standard",
  "goal": "Implement scheduled snapshot + atomic restore for SINGLE and DOUBLE stores so cold-start time collapses from journal replay to mmap'd reload, while preserving atomic publishChecked semantics on restore.",
  "requirements": [
    "Store.snapshot(Path) writes a snapshot with magic+version header and per-page CRC.",
    "Store.restore(Path) atomically swaps in the snapshot and reconciles with the guaranteed journal cursor.",
    "Restore must not surface staged-but-unjournaled writes until the journal cursor is reconciled.",
    "SINGLE and DOUBLE store implementations must both support snapshot and restore."
  ],
  "constraints": [
    "Zero allocation on the publish hot path stays invariant — snapshotting may not allocate on publish.",
    "qa/smoke.sh must still pass the :benchmarks:jmhSmokeCheck alloc gate after the change.",
    "Restore latency under 10ms for a 1M-key snapshot."
  ],
  "acceptance_criteria": [
    "Round-trip integration test: write keys → snapshot → kill JVM → restore → read keys → all keys present and identical.",
    "Crash-mid-journal test: write some keys, simulate journal-write failure, snapshot, restore — only journal-acknowledged writes are visible after restore.",
    "Alloc gate (qa/smoke.sh) passes with snapshot enabled.",
    "JMH benchmark restore-latency-1m-keys < 10ms p99.",
    "CRC mismatch on a page during restore aborts restore and reports a structured invariant failure."
  ],
  "autonomy_policy": "autonomous_until_final_pr",
  "final_human_gate": "final_feature_pr_merge"
}
```

This goal contract is what the integration test file should embed (or load from `tests/fixtures/r002_feature_goal.json`). The expected emergent `PlanContract` from a healthy council run should have at minimum the slices enumerated in § 7 step 6.

---

## 6. Tests

### 6.1 Unit tests — `tests/unit/test_planner_council.py`

Use a `FakeCLIModelProvider` (test fixture; see § 6.4) that returns scripted responses keyed by `profile.name`. Cover:

- `test_panelist_propose_returns_pydantic_plan_contract` — fake returns valid JSON; assert `proposal.candidate` is a `PlanContract`.
- `test_panelist_propose_raises_candidate_invalid_on_unparseable_output` — fake returns garbage; assert `CandidateInvalid` raised, and that the raw response is preserved on the exception.
- `test_consolidator_merge_picks_majority_slices` — three fake panelists, two agree on slice X, one on Y; assert merged plan keeps X.
- `test_consolidator_falls_back_to_lowest_validator_errors_when_agent_fails` — consolidator agent returns invalid JSON; assert fallback selects the candidate whose `validate_plan_contract` returns the fewest errors.
- `test_critic_blocks_on_missing_lifecycle_coverage` — feed a `PlanContract` whose `acceptance_test_matrix` has none of `stale|invariant|failure`; assert `verdict == "revise"` with a blocking finding referencing missing lifecycle terms.
- `test_critic_accepts_clean_plan` — feed a contract that passes both validators and the critic rubric; assert `verdict == "accept"`, `findings == []`.
- `test_council_run_succeeds_when_first_iteration_clean` — fake panelists return clean candidates, fake critic accepts; assert `outcome.accepted_at_iteration == 1`, `len(outcome.iterations) == 1`.
- `test_council_run_revises_then_accepts` — first iteration returns plans with missing forbidden_paths; critic flags it; second iteration returns clean plans; assert `outcome.accepted_at_iteration == 2`, the prior critic findings appear in iteration[0].critic.findings.
- `test_council_run_raises_planner_council_exhausted_when_max_iterations_exceeded` — fake critic always rejects; assert `PlannerCouncilExhausted` raised with the full iteration list.
- `test_planner_handler_dispatches_to_council_when_only_feature_goal_contract_present` — payload has `feature_goal_contract`, no `plan_contract`; assert handler instantiates council, persists final plan, enqueues children, returns `done`.
- `test_planner_handler_blocks_with_recovery_action_on_council_exhausted` — fake council raises `PlannerCouncilExhausted`; assert handler returns `HandlerResult(status="blocked", blocker_code="engineering.planner_council_exhausted")` with the iteration trace.

### 6.2 Integration test — `tests/integration/test_planner_r002.py`

This test must be Postgres‑gated (use the existing `database_url` fixture) and run the full path: project registration → handler invocation → council → plan persistence → role‑gated child task enqueue → handoff records.

**Setup (every test):** register `lvc-standard` via `register_project()` with `state="active"` and `metadata={"role_gates": {"planner": "enabled", "implementer": "disabled", "reviewer": "enabled", "qa": "enabled", "historian": "enabled"}}`. This is the canonical "exercise the workflow without running the stub Implementer" configuration.

- `test_council_produces_clean_plan_contract_for_lvc_r002(database_url)` — load `tests/fixtures/r002_feature_goal.json`, build a `ProjectContext` from in‑memory strings (do not actually read `/Volumes/devssd/repos/ull/lvc-standard` in the test — keep it hermetic), wire a `FakeCLIModelProvider` whose responses come from `tests/fixtures/r002_council_responses/` (one JSON file per profile per iteration), invoke the council via `PlannerHandler`, then assert:
  - The resulting `PlanContract` row in `engineering_plan_contracts` has `status="valid"` and `validation_errors == []`.
  - `acceptance_test_matrix` mentions all three lifecycle categories: at least one entry contains a term from `{stale, invalid, precondition}`, at least one from `{invariant, corrupt, crc}`, at least one from `{failure, timeout, partial}`.
  - `task_slices` includes (by `slice_id` substring or `objective` keyword): a design slice, a `qa.author` slice writing red tests scheduled before all implementer slices, a SINGLE‑store snapshot/restore implementer slice, a DOUBLE‑store snapshot/restore implementer slice, a journal‑cursor reconciliation slice, a reviewer slice, and a `qa.verify` slice running full‑suite + signoff. (Order: design → qa.author → impl(s) → reviewer → qa.verify.)
  - `council_reports` contains at least one entry per panelist plus the critic verdict, hash‑linkable back via the iteration trace.
  - **Role‑gating outcome:** child tasks enqueued in `tasks` table cover *only* the design + qa.author + reviewer + qa.verify + historian slices. Implementer slices are NOT in `tasks` (gated). For each implementer slice, an `engineering_recovery_actions` row exists with `blocker_code="engineering.role_gate_disabled"`, `action="block_execution"`, `status="deferred"`, and the slice id in the outcome JSON. Note: `qa.author` and `qa.verify` are gated by their distinct `task_type` strings — the `role_gates` lookup uses `role` ("qa"), so both QA phases share a single gate setting (treat `"qa"` as the role for both task types).
  - For each *enqueued* child, a `TaskContract` row exists in `engineering_task_contracts` with the same `plan_contract_hash` as the parent plan.
  - `engineering_handoffs` contains a `plan_to_task` row per *enqueued* child task — none for the deferred implementer slices.
  - `HandlerResult.result["deferred_slices"]` lists the implementer slices with their reason.

- `test_council_enqueues_implementer_when_role_gate_enabled(database_url)` — same fixture setup but flip the gate to `"implementer": "enabled"`; assert all slices including implementer are now enqueued and no `role_gate_disabled` recovery rows exist. This proves the gate is the *only* thing differing between the two flows.

- `test_council_writes_recovery_action_when_council_exhausted(database_url)` — wire the FakeCLIModelProvider so every iteration's critic returns `revise`; assert the handler returns `status="blocked"` with `blocker_code="engineering.planner_council_exhausted"`, an `engineering_recovery_actions` row exists with `action="replan"` and the iteration trace serialized into `outcome`, and no `engineering_plan_contracts` row was persisted (since the council never produced an accepted plan).

### 6.3 CLI smoke test — `tests/unit/test_plan_dry_run_cli.py`

- `test_plan_dry_run_exits_zero_with_clean_council` — invoke `plan dry-run` via `typer.testing.CliRunner` with a fake council that always accepts on iteration 1; assert exit code 0 and the printed JSON parses into a structure with `final` (a PlanContract) and `iterations` (a list of length 1).
- `test_plan_dry_run_exits_two_when_council_exhausted` — fake council always rejects; assert exit code 2 and the printed JSON contains an `error: "planner_council_exhausted"` field plus the full iteration trace.

### 6.4 Fixtures

Add under `tests/fixtures/`:

- `r002_feature_goal.json` — the JSON in § 5.
- `r002_council_responses/iter1/panelist-0.json` — a sample panelist response (raw model output: a JSON `PlanContract` with intentionally missing lifecycle test coverage so the critic flags it on iter1).
- `r002_council_responses/iter1/panelist-1.json`, `panelist-2.json` — variants with slightly different slice DAGs.
- `r002_council_responses/iter1/consolidator.json` — merged candidate (still missing lifecycle coverage).
- `r002_council_responses/iter1/critic.json` — verdict `revise` with blocking finding "missing lifecycle invariant tests".
- `r002_council_responses/iter2/panelist-0.json`, `panelist-1.json`, `panelist-2.json` — corrected candidates.
- `r002_council_responses/iter2/consolidator.json` — clean merged candidate.
- `r002_council_responses/iter2/critic.json` — verdict `accept`.

Plus a tiny helper `tests/fixtures/fake_cli_provider.py` that subclasses or wraps `pgloom.models.cli.CLIModelProvider` to return scripted responses by `(profile_name, iteration_count)`.

---

## 7. Acceptance gate (the implementor must clear all of these before declaring done)

1. **Static gates clean.** From `/Volumes/devssd/repos/oss/pgloom-engineering`:
   - `ruff check pgloom_engineering tests` → exit 0.
   - `mypy pgloom_engineering` → exit 0, no new errors.
2. **All new unit tests pass.** `pytest tests/unit/test_planner_council.py tests/unit/test_plan_dry_run_cli.py -v` → all green.
3. **All existing unit tests still pass.** `pytest tests/unit -v` → no regressions versus the baseline (10 passing today).
4. **Integration test passes against Postgres.** `pytest tests/integration/test_planner_r002.py -v` → green (when `PGLOOM_TEST_DATABASE_URL` is set; skips otherwise).
5. **All existing integration tests still pass.** `pytest tests/integration -v` → 12 passing (the existing worker_blocks suite) plus the new R‑002 case.
6. **R‑002 council outcome is structurally correct.** Running the integration test, the persisted `PlanContract` for R‑002 must satisfy:
   - `validation_errors == []` after `validate_plan_contract` — note this **requires** lifecycle coverage because the goal contract mentions snapshot/restore/persistence.
   - `task_slices` contains at minimum these slice types (by `role`):
     - 1 × `designer` (or implementer with `design` in objective)
     - ≥ 1 × `implementer` whose `allowed_paths` includes a path under `store/`
     - ≥ 1 × `implementer` whose `allowed_paths` includes paths under both SINGLE and DOUBLE store implementations (one slice or two — either is acceptable as long as both surfaces are covered)
     - ≥ 1 × `implementer` whose `objective` mentions journal cursor reconciliation
     - ≥ 1 × `qa.author` (`task_type == "engineering.qa.author"`) scheduled **before** every implementer slice; `allowed_paths` ⊆ `{"tests/**", "qa/fixtures/**"}`; `objective` references writing failing tests for the snapshot/restore acceptance criteria; implementer slices do not claim those write paths
     - ≥ 1 × `qa.verify` (`task_type == "engineering.qa.verify"`) scheduled **after** every reviewer slice; `allowed_paths` ⊆ `{"tests/**", "qa/fixtures/**"}`; `verification_commands` includes both `["./qa/smoke.sh"]` and a full‑suite invocation (e.g. `["./qa/regression.sh"]` or equivalent)
     - ≥ 1 × `reviewer`
   - `acceptance_test_matrix` covers all three lifecycle categories per § 6.2.
   - `council_reports` is non‑empty and includes the critic's final `accept` verdict.
   - `implementation_topology` is one of `SPLIT_SPECIALISTS` / `PARALLEL_CANDIDATES` / `COUNCIL_DECIDES` (not `SINGLE`, given the multi‑module scope).
7. **Worker pre/post gates remain green** for the persisted plan and the *enqueued* (non‑gated) child tasks. The integration test should claim the first non‑implementer child task via `pgloom_engineering.worker.run_once` with a fake handler and assert the pre‑gate accepts (project registered + contract present + hash matches).
8. **Role‑gating works end‑to‑end without an Implementer dispatch.** With `lvc-standard` registered as `metadata.role_gates.implementer = "disabled"`:
   - The planner produces a valid `PlanContract` containing implementer slices (the plan is unchanged by the gate — the gate is operational, not contractual).
   - **No** implementer task is enqueued in `tasks`. **No** `TaskContract` row is created for an implementer slice. **No** `plan_to_task` handoff is recorded for an implementer slice.
   - For every implementer slice, exactly one `engineering_recovery_actions` row exists with `blocker_code="engineering.role_gate_disabled"`, `action="block_execution"`, `status="deferred"`, the slice id in `outcome`, and a non‑empty `rationale`.
   - Reviewer + QA + Historian slices ARE enqueued normally with full contract + handoff machinery.
   - `HandlerResult.result["deferred_slices"]` enumerates the implementer deferrals.
   - Flipping the gate to `"enabled"` and re‑running produces full enqueue including implementer slices, with no `role_gate_disabled` rows. This is `test_council_enqueues_implementer_when_role_gate_enabled`.
9. **No edits outside the in‑scope surface** in § 2. Specifically: no edits to `contracts.py`, no edits to `contract_store.py`, no edits to `worker.py`, no schema migrations. The `role_enabled` helper goes in `projects.py`; the gating decision lives in `roles/planner.py`.
10. **Self‑review.** Before declaring done, the implementor agent runs a council‑style review on its own diff: read every new file once, list any blocking findings, and if any exist, fix them before reporting. The report at completion must include the diff stat (`git diff --stat`) and a one‑paragraph self‑review.

---

## 8. Implementation notes / pitfalls to avoid

- **Model output parsing.** Real `claude` CLI invocations rarely return raw JSON — they return Markdown with a JSON code block or chatter around it. The panelist/consolidator/critic each need a tolerant parser: extract the first ```json fenced block, fall back to the last `{...}` balanced span. Keep this in `panelist.py:_extract_json()` (one helper, reused).
- **`CLIModelProvider` token recording.** Every invocation should record to `pgloom.model_usage` via the provider's normal path. Capture the returned `model_usage_id` in the proposal/verdict so council_reports can link audit trails.
- **Idempotency.** A council run that gets killed mid‑iteration must not leave half‑written `engineering_plan_contracts` rows. Persist the final `PlanContract` only after the council returns `CouncilOutcome` — never inside the loop.
- **Iteration trace size.** `council_reports` JSON can grow. Cap each panelist's `raw_response` to 32 KB on persistence (truncate with a `...[truncated]` marker) so the JSONB column doesn't bloat.
- **No filesystem I/O inside the council.** All repo reads happen in the handler's `_build_project_context()` helper. The council itself takes pre‑built `ProjectContext` strings. This keeps tests hermetic.
- **Critic must be a different `CLIModelProfile` than panelist.** Even if both point to the same underlying CLI command, use a distinct `CLIModelProfile.name` so `model_usage` rows are attributable. The critic prompt is materially different (review rubric vs planning prompt).
- **`PlannerCouncilExhausted` carries the full iteration list.** The handler converts that into `HandlerResult.result["iterations"]` so the worker post‑gate writes a `RecoveryDecisionContract(action="replan", attempt=N, max_attempts=...)` with the trace inline. Self‑repair (later work) will read these rows.
- **Do not lift the council into `pgloom`.** It is engineering‑specific (uses the engineering `PlanContract`). Keep the import boundary clean — the council module imports from `pgloom_engineering.contracts` and `pgloom.models.cli`; nothing imports `pgloom_engineering.planner` from outside `pgloom_engineering`.

---

## 9. Reference paths

| What | Where |
|---|---|
| Master plan (autonomy contract, Track G + B, Phase 2 acceptance) | `/Volumes/devssd/repos/oss/pgloom/docs/plans/engineering-orchestrator-port.md` |
| Current planner stub | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/roles/planner.py` |
| Contract definitions + validators | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/contracts.py` |
| Contract store CRUD | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/contract_store.py` |
| Worker pre/post gates | `/Volumes/devssd/repos/oss/pgloom-engineering/pgloom_engineering/worker.py` |
| `CLIModelProvider` | `pgloom.models.cli.CLIModelProvider` (installed package; source at `/Volumes/devssd/repos/oss/pgloom/pgloom/models/cli.py`) |
| `run_bounded` (subprocess primitive) | `pgloom.harness.subprocess.run_bounded` |
| `lvc-standard` ROADMAP (R‑002 source of truth) | `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/ROADMAP.md:19-27` |
| `publishChecked` decision (must be respected by restore) | `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/DECISIONS.md` 2026‑04‑xx entry |
| QA scripts | `/Volumes/devssd/repos/ull/lvc-standard/qa/{smoke,regression}.sh` |
| Legacy planner (read‑only, for context not import) | `/Volumes/devssd/orchestrator/bin/orchestrator.py:9830` (`tick_planner`) |
| Legacy BRAID generators (read‑only, for context not import) | `/Volumes/devssd/orchestrator/braid/generators/lvc-implement-operator.prompt.md` |
| Legacy failure history | `/Volumes/devssd/orchestrator/repo-memory/{FAILURES,RECENT_WORK,CURRENT_STATE}.md` and `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/FAILURES.md` |

---

## 10. Reporting back

When done, the implementor agent should append a completion record at `/Volumes/devssd/repos/oss/pgloom-engineering/docs/reports/planner-impl-and-review-completion.md` containing:

1. Diff stat (`git diff --stat` against the branch base).
2. The exact pytest invocation outputs (last 30 lines of each), ruff exit, mypy exit.
3. The R‑002 `PlanContract` JSON the council produced (truncated `council_reports` is fine).
4. Any deviations from this brief, with rationale.
5. A one‑paragraph honest self‑review covering: what was hardest, what is brittle, what should be the next handler to wire up.

The council‑review pass that judges this work will run the same gates in § 7 against the live tree. Anything that ruff/mypy/pytest doesn't catch will be caught by reading the diff and re‑running the integration test against a freshly‑migrated DB.

---

## 11. Future: extract RubricRunner and reuse across Reviewer/QA panels

The critic in this brief is deliberately a **single-shot CLI invocation** that emits one JSON document with 11 `CriticCheckResult` rows. BRAID runtime is parked; the future swap target is a shared Python-native rubric layer.

**What stays stable across the extraction.**

- `CriticRunner.review(plan, context, validator_errors) -> CriticVerdict` — interface unchanged.
- `CriticVerdict` Pydantic shape — `verdict`, `rationale`, `findings`, `per_check_results`, `model_usage_id` — unchanged.
- `CriticCheckResult.check_id` values — frozen as the rubric IDs in §4.
- `prompts/critic.md` — kept as the rubric definition document and source of truth for what each check means.
- The handler's `_handle_council` path — receives a `CriticVerdict` and does not care whether the implementation is one-shot or a shared runner.
- All council audit data in `PlanContract.council_reports` — the structure persists; downstream tooling keys off `check_id`.

**What changes when RubricRunner lands.**

- `CheckDefinition`, `RubricDefinition`, `RubricRunner`, `RubricVerdict`, and `revise_until_clean(...)` move to a shared module such as `pgloom_engineering/rubrics.py`.
- `PLANNER_CRITIC_RUBRIC` becomes the first `RubricDefinition`.
- Reviewer panels define rubrics such as `CORRECTNESS_PANEL_RUBRIC`, `SECURITY_PANEL_RUBRIC`, and `CONTRACT_DRIFT_RUBRIC`.
- QA can use the same pattern for command evidence, coverage, and regression-risk checks.
- Per-check or per-panel parallelism can be added with Python control flow, not a Mermaid graph.

**What this brief must do now to keep that extraction easy.**

- Prompt template `prompts/critic.md` must list rubric items by `check_id` in their own subsections (one H3 per check).
- The verdict computation lives in `compute_verdict(per_check_results, validator_errors) -> Literal["accept", "revise", "reject"]`, **not in the prompt**.
- `CriticCheckResult.severity_if_failed` is part of the per-check rubric metadata, not a model output. Define it once in a `RUBRIC_CHECKS: list[CheckDefinition]` constant in `critic.py`; the prompt builder reads from this constant.
- Treat `RUBRIC_CHECKS` as the source of truth. Tests assert that every check in `RUBRIC_CHECKS` has a corresponding subsection in `prompts/critic.md` and that `CriticVerdict.per_check_results` contains exactly the check IDs in `RUBRIC_CHECKS`.
