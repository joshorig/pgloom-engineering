# Implementor brief — Token Savior wiring + structural recording + prompt prefix caching

> **Audience.** A coding agent with full read/write on `/Volumes/devssd/repos/oss/pgloom-engineering` and read‑only access to `/Volumes/devssd/repos/oss/token-savior` and `/Volumes/devssd/repos/ull/lvc-standard`. Treat this brief as the complete spec; the only acceptable outputs are the surfaces in §3, the tests in §6, evaluated against §7's gate.
>
> **Goal.** Three things. (1) Confirm Token Savior is installed correctly and recording honestly across the full set of code paths. (2) **Stabilize the existing Anthropic prefix cache so it actually reuses across iterations** — the live planner reports show iter‑2 calls re‑creating ~18K cache tokens because the repair brief is currently in the prefix when it should be in the suffix. (3) Replace the wildly inaccurate char/4 estimator with `tiktoken` counts so the dashboard stops lying.
>
> **Scope size.** Small. Two days of work. The hardest part is the regression eval, not the wiring.

> **Update 2026‑05‑03 (post live‑run analysis).** The live planner suite at `docs/reports/live-planner-suite-2026-05-03-103809-...` shows:
> - Anthropic prefix caching is **already wired** by the planner implementor; this brief no longer needs to introduce it. It needs to **stabilize** it. Iter‑2 panelist calls write 17–18K cache tokens vs iter‑1's 8–9K — the prefix differs across iterations.
> - Token Savior is **already wired** and reporting 60–78% reduction ratios in the comparison reports. Verify this is the case in `pyproject.toml`; if so, the install step in § 3.1 is a verification rather than an addition.
> - The estimator at `model_provider.py` is reporting 9–15K estimated tokens against 27–80K actual — 2–4× under. Replace with `tiktoken` counting.
>
> See `docs/notes/token-economy.md` § 10 for the full per‑call cost breakdown.

---

## 1. Why this work exists

`pgloom_engineering/planner/token_savior_context.py:91-92` imports `token_savior.project_indexer` and `token_savior.query_api`. Those modules exist at `/Volumes/devssd/repos/oss/token-savior/src/token_savior/{project_indexer,query_api}.py` (distribution name `token-savior-recall`, version 2.6.0, MIT, by Mibay) but **the package is not installed in the pgloom-engineering venv**. Every call falls through to `_raw_context()` and the recording layer writes `tokens_saved=0` / `reduction_ratio=0`.

Today's structural savings — `context_lens` (per‑panelist focus filter), `plan_skeleton` (deterministic structure injection), `repair_brief` (delta‑only feedback), `plan_summary` (compact candidate digest), `production_grade` (pre‑LLM rejection) — are real and material, but **none** of them write to `engineering_token_savior_usage` because the recording entry point is keyed off the no‑op `token_savior_pack_context` method. The cost ledger is therefore unusable as a baseline for anything we add later.

Separately, the Planner's biggest single cost lever is missing: **Anthropic native prompt prefix caching**. With 3 panelists per iteration and up to 3 iterations, the static prefix (system prompt + rubric definitions + skeleton + lensed `ProjectContext`) gets sent fresh up to 9 times per feature. Marking the prefix cacheable gives a 90% discount on cached input tokens — the largest improvement we can make to today's flow without touching any of the existing tools.

The full strategy lives in `docs/notes/token-economy.md` § 3 and § 5. This brief is the executable piece.

---

## 2. Scope

**In scope.**
- Add `token-savior-recall` as a path dependency in `pgloom-engineering`'s `pyproject.toml` and verify the existing wiring activates (the import in `token_savior_context.py:91-92` succeeds, `_try_token_savior_pack` returns a real packed context).
- Extend `token_savior_context.py` so it always records to `engineering_token_savior_usage` regardless of which compressor path was taken — including the structural fallbacks (`lens_only`, `skeleton_injection`, `repair_brief_delta`). One row per invocation, with `metadata.method` naming the actual technique used.
- Add Anthropic prompt prefix caching support to `pgloom_engineering/model_provider.py:EngineeringCLIModelProvider`. Build the cacheable prefix once per iteration in `pgloom_engineering/planner/council.py`, pass it through the panelist invocation, mark it as a cache breakpoint.
- Fall‑back behavior: if the underlying CLI provider does not support cache breakpoints (e.g. running against a non‑Anthropic CLI), the breakpoint marker is a no‑op and behavior is identical to today.
- Per‑role `metadata.role` attribution on every `engineering_token_savior_usage` row.
- Replay regression eval: against a fixed corpus of recorded planner runs (under `docs/evals/` — already exists per the audit), confirm that turning on Token Savior + structural recording + prefix caching does not increase `validator_errors` count or `critic.blocking_findings` count by more than 10% on any feature in the corpus.

**Out of scope.**
- RTK adoption (separate brief: `docs/prompts/rtk-subprocess-filter.md`).
- Token Savior `mcp` or `memory-vector` extras (separate brief: `docs/prompts/token-savior-advanced.md`).
- Implementer / Reviewer / QA handlers (separate briefs).
- Master plan edits.
- Schema migrations beyond using the existing `004_token_savior.sql` table.
- Any change to `validate_plan_contract`, the worker pre/post gates, or the contract Pydantic schemas.

---

## 3. Required surfaces

### 3.1 Path dependency

In `pgloom-engineering/pyproject.toml`, add under `[tool.uv.sources]` (or equivalent for the project's resolver):

```toml
[tool.uv.sources]
token-savior-recall = { path = "../../oss/token-savior", editable = true }
```

and add `token-savior-recall` to `[project].dependencies`. The path is resolved relative to the `pyproject.toml` location; verify it works from `/Volumes/devssd/repos/oss/pgloom-engineering` resolving to `/Volumes/devssd/repos/oss/token-savior`. If the project is using a different resolver, use whichever path‑dependency syntax it supports — the goal is `import token_savior` succeeding in the dev and CI envs.

After the change, `/tmp/pge-venv/bin/pip install -e .[dev]` (or equivalent) must result in `python -c 'import token_savior; print(token_savior.__file__)'` printing a path inside `/Volumes/devssd/repos/oss/token-savior/src/token_savior/`. CI must also be updated so the Postgres‑backed test job picks up the path dep — if CI runs in a Linux container without `/Volumes/devssd/...` available, a different relative or git‑based source is needed for CI; document in the completion report.

### 3.2 Always‑record token usage (structural + compressor + fallback)

Today `token_savior_context.py:_try_token_savior_pack()` either returns a packed context (and the caller records usage) or returns `None` (and the caller takes the deterministic excerpt path). The deterministic path **does not currently record** — that is the lying‑zero bug.

Refactor `token_savior_context.py` so the public entry point `build_token_savior_project_context(...)` always returns a `TokenSaviorContextResult` with a `method` field set to one of:

- `"token_savior_pack_context"` — Token Savior compressor ran successfully. Reduction is real compression.
- `"deterministic_excerpt_with_lens"` — fallback path; reduction is from `context_lens` only versus the unlensed raw context. Method is honest about what was actually applied.
- `"deterministic_excerpt_only"` — fallback path with no lens applied (e.g. lens disabled by config). Reduction is whatever the deterministic excerpt achieves over the raw upper bound.

Each `TokenSaviorContextResult` written into `engineering_token_savior_usage` carries `metadata = {"method": ..., "role": ..., "lens": ..., "iteration": ...}` so the dashboard queries in `docs/notes/token-economy.md` § 7 work.

`TokenSaviorContextResult.input_tokens_original` is the token count of the **raw unlensed unfiltered project context** (the upper bound a naive implementation would have sent). `input_tokens_after_savior` is the token count of what the panelist actually receives. The recording is the diff. Token counts use `tiktoken` against `cl100k_base` (or the encoder Anthropic publishes, if available; document the choice in code comments).

### 3.3 Anthropic prompt prefix caching

`pgloom_engineering/model_provider.py:EngineeringCLIModelProvider.invoke` currently builds the prompt as one string and shells out via `run_bounded`. Anthropic's CLI (claude) supports `--cache-control` markers on system prompts and tool definitions; the integration is:

1. The caller (council) builds two strings: the **cacheable prefix** (system prompt + rubric definitions + skeleton + lensed `ProjectContext`) and the **per‑invocation suffix** (this panelist's lens identifier + this iteration's repair brief + the feature goal).
2. The provider's `invoke` signature gains an optional `cacheable_prefix: str | None = None` keyword. If provided, the provider passes it via the CLI's cache‑control mechanism (consult the CLI binary's `--help` for the exact flag — at the time this brief is written, `claude` supports cache control via JSON system messages with `cache_control: {type: "ephemeral"}` markers; verify against the installed binary).
3. If the underlying CLI does not support cache control (detected via `--help` parsing or feature flag), the prefix is concatenated onto the front of the prompt as today and the cache marker is silently dropped. Behavior identical to today; no regressions.
4. The provider records two new metadata fields on the resulting `engineering_token_savior_usage` row: `metadata.cache_prefix_tokens` (size of the cacheable prefix) and `metadata.cache_hit` (boolean — whether the provider's response indicated the prefix was served from cache; available on the second-and-subsequent requests within the cache TTL).

The council changes:

- `pgloom_engineering/planner/council.py` builds `cacheable_prefix` once per iteration (after lens application, before per‑panelist suffix construction). Same prefix is passed to all 3 panelists in that iteration.
- `pgloom_engineering/planner/critic.py` and `consolidator.py` do the same for their own static prefixes (rubric definitions + project context for critic; candidate aggregation prompt for consolidator).
- The deterministic Python‑only `production_grade.py` is unaffected (no LLM call).

### 3.4 Per‑role attribution

Every `engineering_token_savior_usage` row currently lacks the `role` field — it is implied by `task_id` lookup but not directly queryable. Extend the `metadata` JSONB to include `role` at the call sites: `panelist`, `critic`, `consolidator`. Future Implementer/Reviewer/QA calls will follow the same convention. This unblocks the per‑role dashboard query in `docs/notes/token-economy.md` § 7.

### 3.5 Replay regression eval

`scripts/run_live_planner_eval_suite.py` already exists per the repo audit. Extend or create a sibling `scripts/replay_token_economy_regression.py` that:

1. Reads the captured planner runs from `docs/evals/` (whichever directory holds the corpus).
2. Re‑plans each one against today's `main` (compressor disabled) and against the new path (compressor + structural recording + prefix caching enabled), via a feature flag in `Settings`.
3. For each replay, records: `tokens_saved` (sum), `validator_errors` count, `critic.blocking_findings` count, `iterations_to_accept`.
4. Asserts that across the corpus: total tokens_saved increased; per‑feature `validator_errors` count did not increase by more than 10%; per‑feature `critic.blocking_findings` count did not increase by more than 10%; `iterations_to_accept` increased by no more than 1 on any feature.
5. Emits a markdown report at `docs/reports/token-economy-baseline-YYYY-MM-DD.md` with the per‑feature deltas and a top‑level summary.

If the corpus is empty, the eval seeds itself by running the live planner against `lvc-standard` R‑002 once with the compressor disabled and once enabled; that is the minimum acceptable baseline for shipping.

### 3.6 Settings

Extend `pgloom_engineering/config.py:Settings` with:

```python
token_savior_enabled: bool = True                      # master kill switch
prompt_prefix_caching_enabled: bool = True             # Anthropic prefix caching
token_savior_record_structural_fallbacks: bool = True  # log lens-only / skeleton-only paths to the ledger
token_economy_regression_max_iteration_increase: int = 1
token_economy_regression_max_validator_error_increase_pct: float = 10.0
```

`PGLOOM_ENGINEERING_*` prefixed via pydantic-settings.

---

## 4. Component contracts

| Module | Public surface change | Notes |
|---|---|---|
| `pgloom_engineering/planner/token_savior_context.py` | `build_token_savior_project_context()` always returns `TokenSaviorContextResult` with non‑empty `method`. New `_record_savings()` private writes the `engineering_token_savior_usage` row regardless of path. | Existing fallback to `_raw_context()` kept; the difference is recording instead of swallowing. |
| `pgloom_engineering/model_provider.py` | `EngineeringCLIModelProvider.invoke(...)` gains `cacheable_prefix: str \| None = None` keyword. Returns `EngineeringModelInvocationResult.metadata` containing `cache_prefix_tokens` and `cache_hit`. | Provider sniffs CLI cache‑control support once per process and caches the answer. |
| `pgloom_engineering/planner/council.py` | Builds `cacheable_prefix` once per iteration; passes to each panelist invocation. | Cache prefix is iteration‑scoped, not feature‑scoped — repair brief changes per iteration so the prefix that includes it changes too. |
| `pgloom_engineering/planner/critic.py` | Builds its own `cacheable_prefix` (rubric definitions + project context) once per critic invocation; reuses across the deterministic + model passes. | Critic does not iterate per panelist, so cache benefit here is across‑feature reuse on the same project context. |
| `pgloom_engineering/planner/consolidator.py` | Builds its own `cacheable_prefix` (consolidation rubric); per‑invocation suffix is N candidate summaries. | |
| `pgloom_engineering/config.py` | New settings per § 3.6. | |
| `scripts/replay_token_economy_regression.py` | New script; emits markdown report. | If a similar script exists, extend it instead of duplicating. |

---

## 5. Test case — `lvc-standard` R‑002 dry run

Re‑use the planner test fixtures in `tests/integration/test_planner_*.py` and the live eval scripts. The bring‑up validation is:

1. With `Settings(token_savior_enabled=False, prompt_prefix_caching_enabled=False)` — produces a known‑good `PlanContract` for R‑002. Capture the `engineering_token_savior_usage` rows (likely zero or skipped today).
2. With `Settings(token_savior_enabled=True, prompt_prefix_caching_enabled=False)` — produces an equivalent `PlanContract` (same `validate_plan_contract` outcome and same critic verdict on iteration 1, ± allowed iteration drift). `engineering_token_savior_usage` rows now show real `tokens_saved` values, with `metadata.method` populated.
3. With `Settings(token_savior_enabled=True, prompt_prefix_caching_enabled=True)` — same as (2) but `metadata.cache_prefix_tokens > 0` on every panelist invocation and `metadata.cache_hit = true` on panelist invocations 2 and 3 of any given iteration (cold on the first, warm afterwards within the cache TTL).

The corpus in `docs/evals/` (or whatever the existing live eval suite uses) replays similarly.

---

## 6. Tests

### Unit tests

- `tests/unit/test_token_savior_context.py`
  - `test_pack_path_records_compressor_method` — install Token Savior, run `build_token_savior_project_context`, assert the row's `metadata.method == "token_savior_pack_context"` and `tokens_saved > 0`.
  - `test_fallback_path_records_lens_method` — monkeypatch the Token Savior import to raise, run with lens enabled, assert the row's `metadata.method == "deterministic_excerpt_with_lens"` and `tokens_saved >= 0` (lens may not save on small inputs but the row is recorded).
  - `test_fallback_path_records_no_lens_method` — disable lens via config, monkeypatch Token Savior to raise, assert `metadata.method == "deterministic_excerpt_only"`.
  - `test_per_role_metadata` — call from a stub planner, critic, consolidator; assert each row has `metadata.role` set correctly.

- `tests/unit/test_prompt_prefix_caching.py`
  - `test_invoke_passes_cacheable_prefix_to_cli_when_supported` — wire a fake CLI provider that records the constructed CLI argv; assert the cache‑control marker appears.
  - `test_invoke_falls_back_silently_when_cli_lacks_cache_support` — wire a CLI provider whose `--help` lacks the cache flag; assert the prefix is concatenated and no marker is emitted.
  - `test_metadata_records_cache_prefix_tokens_and_hit` — fake provider returns a response with a synthetic cache‑hit indicator; assert the row's `metadata.cache_hit` is `true`.

- `tests/unit/test_council_prefix_construction.py`
  - `test_cacheable_prefix_identical_across_panelists_in_same_iteration` — build the prefix for iteration 1 panelists 0/1/2; assert the bytes are byte‑equal.
  - `test_cacheable_prefix_changes_between_iterations_when_repair_brief_changes` — iteration 1 vs iteration 2 with a non‑empty repair brief; assert prefixes differ.

### Integration test (Postgres‑gated)

- `tests/integration/test_token_economy_e2e.py` — runs the planner against R‑002 with the three settings combinations described in § 5; asserts the `engineering_token_savior_usage` rows match expectations and the produced `PlanContract` is byte‑equal across the three runs (or differs only in `council_reports.iterations[*].proposals[*].raw_response` because of model nondeterminism — exclude those fields from the equality check).

### Replay regression

- `tests/integration/test_token_economy_regression.py` — invokes `scripts/replay_token_economy_regression.py` against the eval corpus, asserts the report's summary indicates within‑threshold deltas. Marked `@pytest.mark.slow` because it runs the full planner N times.

---

## 7. Acceptance gate

1. **Static gates clean.** `ruff check pgloom_engineering tests scripts` → exit 0. `mypy pgloom_engineering` → exit 0, no new errors.
2. **All new unit tests pass.** `pytest tests/unit -v -k "token_savior_context or prompt_prefix_caching or council_prefix"` → green.
3. **All existing unit tests still pass.** No regression to the existing 22+ passing.
4. **Integration test passes.** `pytest tests/integration/test_token_economy_e2e.py` → green when `PGLOOM_TEST_DATABASE_URL` is set.
5. **`token-savior-recall` is importable.** `python -c 'import token_savior; print(token_savior.__version__)'` from the dev venv prints `2.6.0` (or whatever the local checkout's version is).
6. **Reduction is real.** R‑002 dry run with `token_savior_enabled=True` records at least one row in `engineering_token_savior_usage` with `tokens_saved > 0` and `metadata.method == "token_savior_pack_context"`.
7. **Cache hits register.** R‑002 dry run with `prompt_prefix_caching_enabled=True` records at least one row with `metadata.cache_hit = true`.
7a. **Prefix is iteration‑stable.** New unit test: build the panelist prompt for iteration 1 and iteration 2 of the same feature with a non‑empty repair brief; assert the cacheable prefix is byte‑equal across iterations and the per‑invocation suffix carries the iteration‑specific repair brief. Integration test against `lvc-standard` R‑002: replay a 2‑iteration run and assert `cache_creation_input_tokens` for iter‑2 panelists is within 10% of iter‑1 panelists (today it's 2× larger because the prefix is being re‑created).
7b. **Estimator accuracy.** Replace `estimated_input_tokens` / `estimated_output_tokens` with `tiktoken`‑backed counts (or surface Anthropic's `actual_input_tokens` + cache‑creation + cache‑read as the canonical number and drop the estimate column). Unit test: feed a known prompt, assert estimated and actual differ by < 15% in either direction. The current 2–4× under‑estimate documented in `docs/notes/token-economy.md` § 10.3 item 8 must be eliminated.
7c. **Per‑call cost rendering.** Extend `pgloom-engineering feature show FEATURE_ID` (or wherever the dashboard surface lives) to print: per‑profile (panelist / consolidator / critic) call count, per‑profile total cost, per‑profile total cache‑creation tokens, output token total, and a one‑line "iteration cache stability score" defined as `1 - mean(iter_2_cache_creation / iter_1_cache_creation)` (1.0 = perfectly stable; 0.0 = no reuse).
8. **No quality regression.** `scripts/replay_token_economy_regression.py` against the eval corpus emits a report whose summary line says `regression_within_thresholds=true`. The thresholds are the `Settings` defaults from § 3.6 (≤ 1 extra iteration per feature; ≤ 10% extra validator errors or critic blocking findings).
9. **No edits outside the in‑scope surface** in § 2. Specifically: no edits to `contracts.py`, `contract_store.py`, `worker.py`, the existing rubric checks in `critic.py`, or any DB schema migration.
10. **Self‑review.** Implementor agent runs a council‑style review on its own diff before declaring done; report includes `git diff --stat` and a paragraph self‑review noting which CLI cache‑control flag the integration uses, whether CI required a different path‑dep syntax than dev, and any `metadata` field added beyond what this brief specifies.

---

## 8. Implementation notes / pitfalls

- **CLI cache‑control flag is binary‑version‑dependent.** Confirm via `claude --help` against the installed CLI; the flag may be `--cache` or `--system-cache` or only available via JSON system messages. Pin the integration to whatever the installed binary supports and document in code comments.
- **Cache TTL.** Anthropic's ephemeral cache TTL at the time of writing is 5 minutes. If the council runs multiple iterations spaced > 5 min apart (rare but possible during debugging), the cache hit metadata will be `false` on the post‑TTL invocation. Don't treat that as a regression; the metric's purpose is observability, not correctness.
- **`_raw_context` token‑count baseline.** When recording `input_tokens_original`, use the unlensed unfiltered raw context, not the lensed version. Otherwise lens‑only fallback rows show `tokens_saved=0` even though the lens did save tokens. The honest baseline is "what naive implementation would have sent".
- **Path dep across machines.** A path dep makes the repo non‑portable to anyone without the sibling checkout. CI specifically may need a git URL or a pinned commit. Acceptable v1 trade‑off; document the path in `docs/development.md` (or wherever onboarding lives) as a setup step.
- **Token Savior may want its own config.** The `token-savior-recall` package likely accepts a config block (project paths to index, language map, etc.). Look at its `README.md` and `pyproject.toml` for a config schema; pass through whatever it needs from `Settings`.
- **Recording overhead.** One DB INSERT per panelist invocation × 3 panelists × N iterations × per‑feature scale. At today's scale (single feature in flight) this is negligible. If batch dispatch lands later, batch the inserts.
- **Don't break the cache by accident.** Anthropic's prefix cache keys on byte‑exact prefix. If the prefix string contains a non‑deterministic field (timestamp, UUID, randomized panelist ID), every invocation misses cache. Build the prefix from deterministic inputs only; per‑panelist nondeterminism lives in the suffix.

---

## 9. Reference paths

| What | Where |
|---|---|
| Strategy doc (this brief implements pieces 1, 5, 6 of § 6 there) | `docs/notes/token-economy.md` |
| Token Savior local checkout | `/Volumes/devssd/repos/oss/token-savior` |
| Token Savior expected import surface | `/Volumes/devssd/repos/oss/token-savior/src/token_savior/{project_indexer,query_api}.py` |
| Existing wiring (already references the import) | `pgloom_engineering/planner/token_savior_context.py:91-92` |
| Recording layer (do not modify schema; extend metadata only) | `pgloom_engineering/token_savior.py` + `pgloom_engineering/db/schema/004_token_savior.sql` |
| Existing planner package | `pgloom_engineering/planner/` |
| Existing model provider | `pgloom_engineering/model_provider.py` |
| Existing eval scripts | `scripts/run_live_planner_eval_suite.py`, `scripts/verify_lvc_r002_planner.py` |
| Eval corpus | `docs/evals/` |
| Anthropic CLI `claude` binary | `which claude` on the host |
| RTK brief (parallel work, ships independently) | `docs/prompts/rtk-subprocess-filter.md` |
| Token Savior advanced extras (later, deferred) | `docs/prompts/token-savior-advanced.md` |

---

## 10. Reporting back

Append a completion record at `docs/reports/token-savings-fix-and-prefix-cache-completion.md` containing:

1. `git diff --stat` + last 30 lines of each: ruff, mypy, pytest unit, pytest integration.
2. The exact CLI cache‑control flag the integration uses (and the `claude --help` snippet that confirms it).
3. The path‑dep syntax used in `pyproject.toml` for dev, plus whatever variant CI required (if different).
4. A sample `engineering_token_savior_usage` row from each of the three Settings combinations described in § 5.
5. The replay regression report (`docs/reports/token-economy-baseline-YYYY-MM-DD.md`) summary.
6. Any deviations from this brief, with rationale.
7. One‑paragraph self‑review covering: where the cache hit / miss boundary actually fell on the R‑002 corpus, what the realized reduction ratio was, and what is most fragile.

The council‑review pass that judges this work runs § 7 against the live tree.
