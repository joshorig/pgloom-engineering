# Implementor brief — RTK subprocess output filter

> **Audience.** A coding agent with full read/write on `/Volumes/devssd/repos/oss/pgloom-engineering`. Treat this brief as the complete spec.
>
> **Goal.** Cut subprocess output going into LLM context by 60–90% via Rust Token Killer, before the Implementer and split QA verification roles ship and start producing the heavy gradle / JMH / test logs.
>
> **Scope size.** Small. One day of work. The integration is one wrapper around the existing `SubprocessResult`; the regression test makes sure we're not stripping signal alongside the noise.
>
> **Why now (before the consumers ship).** Once Implementer, QA scrutiny, and QA user-test exist, every iteration burns subprocess‑output tokens. Landing the filter pre‑consumer means the cost never appears in the first place. After‑the‑fact integration always leaves a "we burned $X before we noticed" tail.

---

## 1. Why this work exists

`pgloom.harness.subprocess.SubprocessResult.stdout` and `.stderr` flow into LLM context in two places (today projected, not yet in code):

1. **Implementer post‑run summary**: after `verification_commands` run, the result becomes part of `TaskResultContract.checks[].output` for downstream consumption by Reviewer and QA.
2. **QA scrutiny smoke output**: gradle `:benchmarks:jmhSmokeCheck` alone can produce > 50 KB of log. Full regression sweeps are periodic project gates, not per-feature QA scrutiny blockers. QA reads smoke/feature-test logs to decide whether to add gap-closing tests; Reviewer reads them when investigating failures.

A 100 KB log, naively included, is roughly 25K tokens. Three role contexts touching it = 75K tokens of mostly Gradle progress noise. RTK (Rust Token Killer, https://github.com/rtk-ai/rtk) is purpose‑built for filtering build/test output: drops timestamps, reformats stack traces, collapses progress noise, preserves error and assertion lines. The tweet's 60–90% claim matches what other tools in this space (e.g. `prettier-eslint`, error‑only modes) achieve in practice.

The full strategy lives in `docs/notes/token-economy.md` § 4 row 1. This brief is the executable piece.

---

## 2. Scope

**In scope.**
- Add `rtk` (or whatever Python package / binary the Rust tool ships as) as a dependency. If RTK is binary‑only, install via project's bootstrap script and shell out to it; if it ships a Python wrapper, prefer that.
- New module `pgloom_engineering/rtk/filter.py` that takes a `SubprocessResult` and returns a `FilteredSubprocessResult` carrying both the filtered stdout/stderr and the unfiltered originals (for audit). Originals are persisted to artifact storage; filtered versions go into LLM context.
- A small policy layer `pgloom_engineering/rtk/policy.py` that decides which subprocess outputs to filter and which to pass through unchanged. Filter by default; allow opt‑out per slice or per command via `TaskContract.metadata.rtk_passthrough_commands`.
- Recording extension: a row in `engineering_token_savior_usage` per filtered invocation with `metadata.method = "rtk"`, `metadata.command = argv[0]`, `tokens_saved = before - after`.
- Quality regression test: filtered output preserves all assertion failures, all stack traces, all `BUILD FAILED` markers, and all lines matching `error:` / `Error:` / `FAIL` (case‑sensitive matters per language). Test fixtures from `lvc-standard`'s actual gradle/JMH outputs.

**Out of scope.**
- Token Savior wiring (separate brief: `docs/prompts/token-savings-fix-and-prefix-cache.md`).
- Token Savior advanced extras (separate brief: `docs/prompts/token-savior-advanced.md`).
- Any Implementer / Reviewer / QA handler implementation (separate briefs).
- Master plan edits.
- Changes to `pgloom.harness.subprocess` itself — RTK lives in the engineering repo and wraps the result, it does not replace `run_bounded`.

---

## 3. Required surfaces

### 3.1 Module layout

```
pgloom_engineering/rtk/
├── __init__.py                  # exports filter_subprocess_result
├── filter.py                    # FilteredSubprocessResult + filter_subprocess_result()
├── policy.py                    # FilterPolicy + should_filter()
└── fixtures/                    # known-good / known-noisy reference outputs for tests
    ├── gradle_unit_test_pass.log
    ├── gradle_unit_test_fail.log
    ├── jmh_smoke_pass.log
    ├── jmh_smoke_alloc_regression.log
    └── full_app_run_segment.log
```

### 3.2 Public Pydantic surface

`pgloom_engineering/rtk/filter.py`:

```python
class FilteredSubprocessResult(BaseModel):
    """Wraps SubprocessResult with the filtered + original streams."""
    original: SubprocessResult                        # full stdout/stderr from pgloom.harness.subprocess
    filtered_stdout: str
    filtered_stderr: str
    filter_method: Literal["rtk", "passthrough", "rtk_unavailable"]
    tokens_before: int                                # encoder-counted on original.stdout + original.stderr
    tokens_after: int                                 # encoder-counted on filtered streams
    tokens_saved: int
    reduction_ratio: float                            # (tokens_saved / tokens_before) bounded [0, 1]
    artifact_id_unfiltered: int | None = None         # pgloom.artifacts row id if the originals were registered

def filter_subprocess_result(
    result: SubprocessResult,
    *,
    policy: FilterPolicy | None = None,
    encoder_name: str = "cl100k_base",
    record_in: str | None = None,                     # database_url for engineering_token_savior_usage
    feature_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
) -> FilteredSubprocessResult: ...
```

`pgloom_engineering/rtk/policy.py`:

```python
class FilterPolicy(BaseModel):
    enabled: bool = True
    passthrough_commands: list[str] = Field(default_factory=list)  # argv[0] basenames that skip filtering
    passthrough_exit_codes: list[int] = Field(default_factory=list) # e.g. [0] to skip filtering on success
    max_tokens_after: int | None = None                # truncate post-filter to this hard ceiling

def should_filter(result: SubprocessResult, policy: FilterPolicy) -> bool: ...
```

### 3.3 Filtering implementation

RTK ships as a Rust binary at `https://github.com/rtk-ai/rtk`. The integration approach:

1. **Binary on PATH**: `rtk --help` succeeds. `filter_subprocess_result` shells out via `pgloom.harness.subprocess.run_bounded(["rtk", "--lang", "auto", "--input-format", "stdout"], stdin=result.stdout.encode())`. Bounded with a generous timeout (5s); on timeout or non‑zero exit, fall back to `filter_method="rtk_unavailable"` and pass through unchanged.
2. **Python package**: if RTK exposes a `pyo3` wheel or similar, prefer in‑process invocation to avoid the subprocess overhead per filter call. Check the repo at integration time to see what's available.
3. **No RTK installed**: fallback path returns `FilteredSubprocessResult` with `filter_method="rtk_unavailable"`, `filtered_stdout = original.stdout`, `tokens_saved = 0`. Recording happens anyway so the dashboard shows "RTK was attempted but unavailable" rather than silent zero.

### 3.4 Artifact preservation

Filtering is lossy by design. The unfiltered originals must remain available for debugging, audit, and regression triage. Two options:

- **Option A (preferred)**: register the unfiltered streams as `pgloom.artifacts` rows (one for stdout, one for stderr) and store the artifact ids on `FilteredSubprocessResult.artifact_id_unfiltered_stdout` and `.artifact_id_unfiltered_stderr`. Anyone investigating a failure can fetch the originals.
- **Option B**: serialize the unfiltered streams into the `metadata` JSONB of the `engineering_token_savior_usage` row. Simpler but bloats the JSONB column; reject in favor of A.

`pgloom.artifacts.register_artifact()` already exists; use the local filesystem backend.

### 3.5 Token counting

Use `tiktoken` with `cl100k_base` encoder by default. The encoder choice matters because Anthropic's tokenizer is not strictly cl100k but the deltas correlate well; the dashboard cares about relative reduction not absolute absolute. Make the encoder name configurable via `Settings.token_count_encoder` so a future swap is mechanical.

If `tiktoken` is not already a dependency, add it as one — it's small and stable.

### 3.6 Settings

Extend `pgloom_engineering/config.py:Settings` with:

```python
rtk_filter_enabled: bool = True
rtk_passthrough_commands: list[str] = Field(default_factory=list)
rtk_passthrough_on_success: bool = False               # if true, skip filtering when exit_code=0
rtk_max_tokens_after: int | None = None                # hard ceiling post-filter
token_count_encoder: str = "cl100k_base"
```

### 3.7 Integration call sites

This brief **does not** modify Implementer / Reviewer / QA handlers (they don't exist yet). What it does is provide the wrapper so when those briefs land, the integration is one line:

```python
filtered = filter_subprocess_result(
    result,
    policy=FilterPolicy(...),
    record_in=database_url,
    feature_id=feature_id,
    task_id=task_id,
    role="implementer",
)
context_for_next_role = filtered.filtered_stdout + filtered.filtered_stderr
```

To prove the wrapper is real before consumers exist, the brief includes one **end‑to‑end test that runs `lvc-standard`'s actual `qa/smoke.sh` against the wrapper** and asserts the filtered output preserves the alloc gate's pass/fail signal while collapsing Gradle progress noise. This stress‑tests the integration against real workload shape, not a synthetic.

---

## 4. Tests

### Unit tests

- `tests/unit/test_rtk_filter.py`
  - `test_passthrough_when_disabled` — `policy.enabled=False` returns the original streams verbatim with `tokens_saved=0`.
  - `test_passthrough_for_listed_command` — `policy.passthrough_commands=["git"]`, run with `argv=["git", "status"]`, returns originals.
  - `test_passthrough_on_success_when_enabled` — `policy.passthrough_on_success=True`, exit_code=0, returns originals.
  - `test_filters_when_policy_says_to` — `policy.enabled=True`, exit_code != 0 (or success path enabled), shells out to RTK; assert filtered streams differ from originals and `tokens_saved > 0`.
  - `test_truncates_to_max_tokens_after` — set `policy.max_tokens_after=100`, run on a long output; assert filtered length is ≤ 100 tokens.
  - `test_records_engineering_token_savior_usage_row` — pass `record_in=database_url`; assert the row exists with `metadata.method="rtk"` and `metadata.command="<argv[0] basename>"`.
  - `test_falls_back_when_rtk_binary_missing` — monkeypatch `which rtk` → None; assert `filter_method="rtk_unavailable"` and originals returned.
  - `test_artifact_registration_for_unfiltered_originals` — assert `artifact_id_unfiltered_stdout` and `artifact_id_unfiltered_stderr` are populated and the artifact rows exist.

- `tests/unit/test_rtk_policy.py`
  - `test_should_filter_truth_table` — exercises every `(enabled, passthrough_commands, passthrough_on_success, exit_code, argv)` combination.

### Quality regression tests

- `tests/unit/test_rtk_quality_preservation.py` — for each fixture in `pgloom_engineering/rtk/fixtures/`:
  - Filter the fixture.
  - Assert every line matching `^.*FAIL.*$` (case‑sensitive Java/Gradle convention) is preserved.
  - Assert every line matching `^.*Exception.*$` (Java stack‑trace top frame) is preserved.
  - Assert every line containing `BUILD FAILED` is preserved.
  - Assert every line matching `^.*assertion.*failed.*$|^.*expected:.*but was:.*$` (JUnit/AssertJ failure marker) is preserved.
  - Assert reduction ratio ≥ 0.5 on the noisy fixtures (`gradle_unit_test_pass.log`, `jmh_smoke_pass.log`) and any reduction at all on already‑terse fixtures (`gradle_unit_test_fail.log` is mostly signal — small reduction expected).

### End‑to‑end (live, slow, optional)

- `tests/integration/test_rtk_against_lvc_smoke.py` — marked `@pytest.mark.slow`, gated on `LVC_STANDARD_PATH` env var. Runs `lvc-standard`'s `qa/smoke.sh` via `pgloom.harness.subprocess.run_bounded`, filters the result, asserts the filtered output preserves the alloc‑gate verdict line (`:benchmarks:jmhSmokeCheck` PASSED/FAILED) while collapsing per‑task progress noise. Captures the realized reduction ratio in the test output for reporting.

---

## 5. Acceptance gate

1. **Static gates clean.** `ruff check pgloom_engineering/rtk tests` → exit 0. `mypy pgloom_engineering/rtk` → exit 0.
2. **Unit tests pass.** `pytest tests/unit/test_rtk_*.py -v` → green.
3. **Quality regression tests pass.** Every fixture preserves the marker patterns enumerated in § 4. Reduction ratios meet the floors (≥ 0.5 on noisy fixtures).
4. **Existing tests still pass.** No regression to the 22+ currently passing.
5. **`engineering_token_savior_usage` rows are written** with `metadata.method="rtk"` and `metadata.command` populated. Confirmed via DB query in the integration test.
6. **Artifact preservation works.** Unfiltered originals are queryable via `pgloom.artifacts` after a filter call; the artifact ids round‑trip through `FilteredSubprocessResult.artifact_id_unfiltered_*`.
7. **Fallback path works.** With RTK binary uninstalled (or path mocked), `filter_method="rtk_unavailable"` and behavior is identical to today (pass through with zero recorded savings).
8. **Live `lvc-standard` smoke run succeeds** (when `LVC_STANDARD_PATH` is set). Realized reduction ratio recorded in the completion report.
9. **No edits outside the in‑scope surface** in § 2. No changes to `pgloom.harness.subprocess`, `worker.py`, contract Pydantic, or DB schema.
10. **Self‑review.** Same convention as other briefs.

---

## 6. Implementation notes / pitfalls

- **RTK install path.** If RTK is Rust‑binary‑only, the bootstrap script (`scripts/bootstrap_dev_env.sh` or equivalent) should install it via `cargo install rtk-ai/rtk` or fetch a release binary. Document the install step; CI will need it pre‑installed in the container image.
- **Encoder mismatch.** `tiktoken` `cl100k_base` is OpenAI's; Anthropic uses a slightly different tokenizer. The reduction ratio is a relative metric so the encoder choice doesn't affect comparability across runs of the same encoder. Don't change encoders mid‑corpus; document the choice in code.
- **Don't filter `rtk` itself.** If the policy ever passes RTK output through RTK, infinite loop / nonsense. Add `argv[0] == "rtk"` to the always‑passthrough list in `should_filter`.
- **Color codes.** Gradle and JMH emit ANSI escape codes. RTK probably handles them; verify against the fixtures. If not, strip ANSI before passing to RTK.
- **Whitespace‑only output.** Empty subprocess output → `tokens_before=0`, `reduction_ratio = 0/0`. Guard against div‑by‑zero; return `reduction_ratio=0` with `tokens_saved=0`.
- **Stderr vs stdout independence.** Filter them separately; some tools (gradle, npm) put errors on stdout and progress on stderr or vice versa. RTK likely handles both as a unit but the policy may want to filter only one.
- **Artifact retention.** Unfiltered originals can be large. Use the local filesystem backend (already in pgloom 0.2.0); a future StorageBackend Protocol (deferred to pgloom 0.3.x per the master plan) will let us push to S3 if needed.

---

## 7. Reference paths

| What | Where |
|---|---|
| Strategy doc | `docs/notes/token-economy.md` § 4 row 1 |
| RTK upstream | https://github.com/rtk-ai/rtk |
| Subprocess primitive | `pgloom.harness.subprocess.run_bounded` |
| Artifact registration | `pgloom.artifacts.register_artifact` |
| Recording layer | `pgloom_engineering/token_savior.py` + `004_token_savior.sql` |
| `lvc-standard` smoke script (live test target) | `/Volumes/devssd/repos/ull/lvc-standard/qa/smoke.sh` |
| Token Savior wiring brief (parallel work) | `docs/prompts/token-savings-fix-and-prefix-cache.md` |

---

## 8. Reporting back

Append a completion record at `docs/reports/rtk-subprocess-filter-completion.md` containing:

1. `git diff --stat` + last 30 lines of: ruff, mypy, unit tests, integration test (if run live).
2. Realized reduction ratio against the live `lvc-standard` smoke run (if executed).
3. RTK install path used (binary URL, cargo install command, or python wrapper version).
4. Any deviations from this brief, with rationale.
5. One‑paragraph self‑review covering: which fixtures were hardest to preserve marker patterns on, whether any over‑filtering was caught and how, and whether the encoder choice surfaced any issues.
