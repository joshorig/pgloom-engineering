# pgloom-engineering — token economy

> Living architectural reference for how the autonomous workflow controls token cost. Captures what we already do, where the bills land, what's broken, and what's next. Not a brief — implementor briefs in `docs/prompts/` carry the executable instructions; this doc is the why.

---

## 1. Where the tokens go (today and projected)

| Role | Per‑invocation token sinks | Per‑feature shape | Today | Projected at full scale |
|---|---|---|---|---|
| Planner panelist | system prompt + rubric + `ProjectContext` (lensed) + skeleton + repair brief + feature goal | 3 panelists × N iterations | dominant cost (planner is the only role really firing) | small fraction |
| Critic | candidate plan **summary** + lensed context + validator errors | 1 × N iterations | small | small |
| Consolidator | N candidate summaries | 1 × N iterations | small | small |
| Implementer | full file dumps for context, gradle/JMH/test stdout, prior task_result, repair feedback | 1+ per slice; many slices per feature | not yet wired | **dominant cost** |
| Reviewer | diff to review, neighbouring code, prior similar reviews, full panel rubrics × 2+ panels | 2+ panels × N rounds per slice | not yet wired | high |
| QA author | acceptance matrix, existing tests to model after, project test conventions | 1 per slice | not yet wired | medium |
| QA verify | full‑suite output, full‑app run logs, coverage reports, residual gap analysis | 1+ per feature, per signoff round | not yet wired | **dominant cost** alongside Implementer |

**Operating principle.** The compression / caching machinery has to land *before* the heavy roles ship, not after. Otherwise we burn cost we never had to pay during the bring‑up phase.

---

## 2. What's already wired in `pgloom_engineering/planner/`

| Module | Mechanism | Active today? |
|---|---|---|
| `token_savior.py` | Recording layer — writes `engineering_token_savior_usage` rows with `tokens_saved` / `reduction_ratio` / `estimated_cost_saved_usd` | yes (recording works) |
| `token_savior_context.py` | `_try_token_savior_pack()` imports `token_savior.project_indexer` + `token_savior.query_api`, falls back to deterministic excerpt on failure | **no — see § 3** |
| `context_capsule.py` + `007_planning_context_capsules.sql` | DB‑backed cache of packed `ProjectContext` keyed by `(project, git_head, query_hash, capsule_version)`. Identical queries reuse the capsule across runs and across features. | yes |
| `context_lens.py` | Per‑panelist focus filter — panelist 0 gets `architecture` lens, 1 gets `qa`, 2 gets `risk`. Lensed `ProjectContext` excerpts only the parts of ROADMAP / DECISIONS / paths matching the lens's focus terms. 3× panelists ≠ 3× context cost. | yes |
| `plan_skeleton.py` | Deterministic `DeterministicPlanSkeleton` injected into the panelist prompt. Model fills slots rather than generating PlanContract structure from scratch. | yes |
| `plan_summary.py` | Candidate plan summary (truncated fields, length‑capped strings). Consolidator sees summaries, not raw 30 KB JSON candidates. | yes |
| `repair_brief.py` | On revise iterations, panelists receive only must‑fix codes + top‑12 critic findings, not full prior iteration trace. | yes |
| `production_grade.py` | Deterministic Python‑only critic pass. Many would‑be‑rejections never spend critic LLM tokens. | yes |
| `model_provider.py` (`EngineeringCLIModelProvider`) | Wraps `pgloom.models.cli.CLIModelProvider`, records durable usage rows, returns `model_usage_id` for audit linkage. | yes |

**Categories already covered:** context caching, context filtering (lens), context compression *(intent, see § 3)*, deterministic priming (skeleton), inter‑iteration delta‑only (repair brief), summary‑not‑full (plan_summary), pre‑LLM rejection (production_grade), per‑invocation cost recording.

---

## 3. The wired-but-uninstalled finding

`pgloom_engineering/planner/token_savior_context.py:91-92` does:

```python
project_indexer = import_module("token_savior.project_indexer")
query_api = import_module("token_savior.query_api")
```

The `token_savior` Python package is **not installed in the pgloom-engineering venv** (`/tmp/pge-venv/bin/python -c 'import token_savior'` → `ModuleNotFoundError`). Every call therefore falls through to the deterministic excerpt fallback. Today the recording layer ships `tokens_saved=0` / `reduction_ratio=0` because the actual compression step is a no‑op.

**Resolved finding (2026‑05).** The package **does exist** at `/Volumes/devssd/repos/oss/token-savior` — distribution name `token-savior-recall`, version 2.6.0, MIT, by Mibay (= the same Token Savior MCP cited as item #4 in the linked tweet). It bundles `project_indexer.py` and `query_api.py` matching the expected import paths exactly, plus `tree-sitter-java` for `lvc-standard` symbol navigation out of the box, plus an optional `memory-vector` extra (sqlite-vec + sentence-transformers) for semantic retrieval, plus an MCP server mode (`mcp` extra). It exposes 105 MCP tools, claims 97% token savings, and ships with 1318 tests.

**Impact today.** Reported `engineering_token_savior_usage` numbers understate savings — the structural pieces (lens, skeleton, repair brief, production_grade) deliver real reductions but they are **not** captured in the recording layer because the recording layer is keyed off the no‑op `token_savior_pack_context` method that always falls through. The dashboard would show zero savings even though we are saving real tokens.

**Fix.** Install the local `token-savior-recall` package as a path dependency in `pyproject.toml`, then re‑run `_try_token_savior_pack` end‑to‑end against R‑002 to confirm the import succeeds and a real `tokens_saved` value lands in `engineering_token_savior_usage`. Add a fallback path that records the structural savings (lens + skeleton + repair brief) under their own `method` keys so the cost ledger is honest even when Token Savior is unavailable. Captured in `docs/prompts/token-savings-fix-and-prefix-cache.md`.

---

## 4. The 10‑tool review (https://x.com/rodmanai/status/2050604420870852654)

Each of the tools listed in the linked tweet, mapped to our setup:

| # | Tool | Verdict | Where |
|---|---|---|---|
| 1 | **RTK** (Rust Token Killer) — filters terminal output, 60–90% reduction | **Tier 1 — adopt** | Wrap `pgloom.harness.subprocess.SubprocessResult.stdout/stderr` for the eventual Implementer + QA verify flows. Brief: `docs/prompts/rtk-subprocess-filter.md`. |
| 2 | **Context Mode** — Playwright/GH offload to SQLite, 98% reduction | **Skip** | We don't use Playwright; gh CLI outputs are small. Pattern is essentially what `engineering_handoffs` already does. |
| 3 | **code-review-graph** — Tree‑sitter knowledge graph, 49× reduction | **Skip — superseded by #4** | Same problem space as Token Savior, which we already have wired and which is more mature (105 MCP tools, 1318 tests, vector extra, MCP server mode). Adopting code-review-graph would be parallel infrastructure. |
| 4 | **Token Savior** (Mibay, v2.6.0) — code‑by‑symbols, 97% reduction, in‑process import + MCP server modes | **Tier 1 — install** | **Already partially wired** at `pgloom_engineering/planner/token_savior_context.py:91-92` but not installed. Local checkout at `/Volumes/devssd/repos/oss/token-savior`. Install as path dep → wiring activates. Covers what #3 and #10 do via two extras (`mcp`, `memory-vector`). Brief: `docs/prompts/token-savings-fix-and-prefix-cache.md`. |
| 5 | **Caveman Claude** — terse output, 65–75% reduction | **Skip** | Our outputs are JSON‑schema‑constrained. Saves only `rationale` / `findings.message` strings — small percentage, at the cost of audit readability. |
| 6 | **claude-token-efficient** — CLAUDE.md instruction | **Skip** | Marginal; same reasoning as #5. We can drop a one‑line "be terse in rationales" instruction in our existing prompts if needed. |
| 7 | **token-optimizer-mcp** — caching tool outputs, 95%+ reduction on repeats | **Tier 2 — defer** | `context_capsule` already caches the dominant repeated payload (project context). Revisit when Implementer ships and we see what's actually repeated. |
| 8 | **claude-token-optimizer** (Nadim) — setup prompt collection | **Skip** | We have rubric‑driven prompts; their template set isn't compatible. |
| 9 | **token-optimizer** (Greensh) — "ghost token" finder | **Tier 2 — one‑shot audit** | Not a runtime dependency. Run once against current planner prompts to surface waste. |
| 10 | **claude-context** (Zilliz) — BM25 + vector retrieval, ~40% reduction | **Defer behind Token Savior** | Token Savior's optional `memory-vector` extra (sqlite-vec + sentence-transformers) covers the same use case at lighter weight. Adopt Zilliz only if Token Savior's vector capability proves insufficient at scale. |

---

## 5. Gaps not in the tweet but high ROI

| Gap | What | ROI | Status |
|---|---|---|---|
| **Anthropic prompt prefix caching** | Static system prompt + rubric + skeleton + lensed context shares a cached prefix across the 3 panelists per iteration and across iterations within a feature. 90% discount on cached input tokens. | **Highest single lever** for current Planner cost, especially as iterations grow. 3 panelists × 3 iterations = 9 invocations sharing the same ~5 KB prefix today. | Captured in `docs/prompts/token-savings-fix-and-prefix-cache.md`. |
| **Model routing per role** | Run consolidator + skeleton selection on Haiku‑class; keep panelists / critic on Sonnet/Opus. `Settings` already exposes per‑role profiles; the switch is a config change with one new profile. | Medium — consolidation is a small fraction of total tokens but the model‑class swap is essentially free quality‑wise for that pass. | Defer; mention here so it's not lost. |
| **Diff‑only candidate consolidation** | Consolidator currently sees N full summaries. Pass diffs (candidate N vs candidate N‑1, after sorting by some canonical key) instead. | Low‑medium — only material if `panelist_count > 3`. | Defer. |
| **Output schema constraints** | `CLIModelProfile.parse_response="json"` + `response_schema` already exists. Tightening the schema for panelist/critic outputs would shave output tokens. | Low‑medium — output is already structured. | Quick win; can land alongside the prefix‑caching brief. |

---

## 6. Adoption order

1. **`token-savings-fix-and-prefix-cache.md`** — install `token-savior-recall` as a path dep so the existing wiring activates; add structural‑savings recording for lens / skeleton / repair_brief; add Anthropic native prompt prefix caching for the static rubric+skeleton+context prefix shared across panelists. **Smallest scope, highest immediate ROI.** Land first.
2. **`rtk-subprocess-filter.md`** — RTK wrapper around `pgloom.harness.subprocess`. Lands now (no role dependency); pays off the moment the Implementer or QA verify ship.
3. **One‑shot audit pass** with token-optimizer (#9) against current planner prompts. ~30 minutes. Captures findings in `docs/reports/`.
4. **`token-savior-advanced.md`** — turn on Token Savior's optional extras for the heavy code‑reading roles: `mcp` extra so Implementer/Reviewer/QA can use it as an MCP tool, `memory-vector` extra for similarity retrieval (alternative to Zilliz #10). **Deferred until Implementer brief is in flight** — adds real value only when the heavy code‑reading roles exist.
5. Defer #2, #3, #5, #6, #7, #8, #10 indefinitely; revisit if the workload shape changes (Token Savior covers the #3 + #4 + #10 problem space; the rest are not relevant to our setup).

---

## 7. Measurement

The `engineering_token_savior_usage` schema already records `input_tokens_original`, `input_tokens_after_savior`, `tokens_saved`, `reduction_ratio`, `estimated_cost_saved_usd`. With the broken import fixed, every compressor we add must land alongside a row attribution: which compressor (`method` field), which invocation (`model_usage_id`), which feature (`feature_id`). Three queries to add to `pgloom-engineering feature show`:

```sql
-- Total saved per feature
select sum(tokens_saved), sum(estimated_cost_saved_usd)
  from engineering_token_savior_usage where feature_id = $1;

-- Saved per method (so we can compare RTK vs prefix-cache vs code-graph)
select metadata->>'method' as method,
       sum(tokens_saved), avg(reduction_ratio)
  from engineering_token_savior_usage
  where feature_id = $1 group by 1 order by 2 desc;

-- Saved per role (which role benefits most)
select metadata->>'role' as role,
       sum(tokens_saved), avg(reduction_ratio)
  from engineering_token_savior_usage
  where feature_id = $1 group by 1 order by 2 desc;
```

This makes every compression integration self‑justifying — the dashboard tells us whether RTK is actually saving 60–90% on our workload, or whether the tweet's claim was for a workload shape we don't have.

**Quality regression guard.** Every compressor added must be evaluated against a fixed corpus of completed features (replay). If `validator_errors` count or critic blocking‑finding count increases by > 10% on a representative replay set after enabling a compressor, that compressor is reverted by default and gated behind an opt‑in flag. The eval scripts under `scripts/run_live_planner_eval_suite.py` are the seed for this — the brief at `docs/prompts/token-savings-fix-and-prefix-cache.md` formalizes the regression check.

---

## 8. Decisions captured

1. **2026‑05: keep the recording layer (`token_savior.py`) as the authoritative cost ledger.** All compressors plug into it; nothing duplicates the schema.
2. **2026‑05: do not adopt #2, #5, #6, #8 from the linked tweet.** Marginal or redundant for a JSON‑schema‑constrained, contract‑driven workflow.
3. **2026‑05: prompt prefix caching is the highest‑ROI lever and is not in the linked tweet.** Treat it as Tier 1 in adoption order.
4. **2026‑05: defer code‑aware retrieval (#3, #10) until Implementer brief is in flight.** Adopting earlier means writing the integration against a phantom consumer.
5. **2026‑05: every compressor reports `method` + `role` in `engineering_token_savior_usage.metadata`.** No silent compression. Per‑method per‑role attribution is the basis of the regression guard.

---

## 9. Pointers

| What | Where |
|---|---|
| Token Savior install + structural recording + prefix caching brief | `docs/prompts/token-savings-fix-and-prefix-cache.md` |
| RTK brief | `docs/prompts/rtk-subprocess-filter.md` |
| Token Savior advanced (MCP + vector) brief | `docs/prompts/token-savior-advanced.md` |
| Token Savior local checkout | `/Volumes/devssd/repos/oss/token-savior` (`token-savior-recall` v2.6.0) |
| Recording layer | `pgloom_engineering/token_savior.py` |
| Schema | `pgloom_engineering/db/schema/004_token_savior.sql`, `007_planning_context_capsules.sql` |
| Existing context infra | `pgloom_engineering/planner/{token_savior_context,context_capsule,context_lens,plan_skeleton,plan_summary,repair_brief,production_grade}.py` |
| Master plan Track G + autonomy contract | `/Volumes/devssd/repos/oss/pgloom/docs/plans/engineering-orchestrator-port.md` |
| Linked tweet | https://x.com/rodmanai/status/2050604420870852654 (auth‑gated; tool list captured in § 4) |
| Live planner suite reports | `docs/reports/live-planner-suite-2026-05-03-103809-live-planner-autonomy-baseline/` |
| Per‑call usage data (R006‑wide failed cost gate) | `docs/reports/live-planner-suite-2026-05-03-103809-live-planner-autonomy-baseline/dag-r006-wide__claude-sonnet/model_usage.jsonl` |

---

## 10. Findings from live planner runs (2026‑05‑03)

The live‑planner autonomy suite at `docs/reports/live-planner-suite-2026-05-03-103809-...` ran three feature shapes against Claude Sonnet and revealed both pleasant surprises and concrete cost waste. Hard data, not speculation.

### 10.1 Pleasant surprises

- **Anthropic prompt caching is already wired.** Every `model_usage.jsonl` row shows `cache_creation_input_tokens` and `cache_read_input_tokens` populated. The first panelist invocation of R006 wrote 9,469 tokens of cacheable prefix and read 12,876 from cache; subsequent panelists in the same iteration read the same 12,876 from cache without re‑creating it. Brief #1 (`token-savings-fix-and-prefix-cache.md`) was wrong to assume caching was missing — it was already added by the planner implementor. The brief should be amended to focus on **stabilizing** the prefix rather than introducing the feature.
- **Token Savior is also wired and reporting real reductions.** Comparison reports show 60.7%–77.7% reduction ratios on packed context. The wiring problem flagged in § 3 must have been resolved between the audit and this run; verify against current `pyproject.toml` to confirm.
- **`production_grade` rubric runs and gates correctly.** Every accepted run shows `accept (100) / prod accept (100)` — the deterministic Python check is doing real work.

### 10.2 Cost waste — the R006‑wide $2.77 / 10‑call breakdown

R006 wide failed the `api_equivalent_cost ≤ $2.25` gate at $2.37 (and $2.77 actual on Anthropic's metering). Per‑call from `model_usage.jsonl`:

| Call | Profile | Iter | Prompt chars | Response chars | Actual tokens | Cache create | Cache read | Output tokens | Cost USD | Elapsed |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | panelist | 1 | 17,695 | 20,645 | 29,233 | 9,469 | 12,876 | 6,878 | 0.149 | 99 s |
| 2 | panelist | 1 | 13,863 | 19,578 | 26,700 | 8,142 | 12,876 | 5,672 | 0.124 | 89 s |
| 3 | panelist | 1 | 15,467 | 19,445 | 27,917 | 8,738 | 12,876 | 6,293 | 0.136 | 99 s |
| 4 | **consolidator** | 1 | **61,624** | **32,047** | **57,652** | 21,315 | 12,876 | **23,451** | **0.453** | **344 s** |
| 5 | critic | 1 | 46,691 | 13,300 | 37,221 | 16,494 | 12,876 | 7,841 | 0.198 | 126 s |
| 6 | panelist | 2 | 51,021 | 32,686 | 41,227 | **18,658** | 12,876 | 9,683 | 0.234 | 161 s |
| 7 | panelist | 2 | 47,189 | 32,033 | 40,075 | **17,331** | 12,876 | 9,858 | 0.231 | 160 s |
| 8 | panelist | 2 | 48,793 | 31,427 | 40,440 | **17,927** | 12,876 | 9,627 | 0.230 | 147 s |
| 9 | **consolidator** | 2 | **97,325** | 32,264 | 64,399 | **31,065** | 12,876 | **20,448** | **0.455** | 282 s |
| 10 | critic | 2 | 48,147 | 13,739 | 79,621 | **24,074** | **44,072** | 11,455 | **0.555** | 241 s |

Totals: 444,485 actual tokens, **$2.77 USD**, ~31 minutes wall clock.

**Cost by component (Anthropic Sonnet pricing $3 input / $3.75 cache write / $0.30 cache read / $15 output per million):**

- Output: ~111K × $15/M = **$1.66 (60% of total)**
- Cache creation: ~188K × $3.75/M = **$0.71 (25%)**
- Cache read: ~142K × $0.30/M = **$0.04 (1.5%)**
- Direct input + overhead: rest

### 10.3 Concrete waste discovered (ranked by ROI)

1. **Prefix is unstable across iterations** *(big, easy fix)*. `cache_creation_input_tokens` per panelist call: iter 1 averages **8.7K**, iter 2 averages **18.0K**. The prefix is being **re‑created** between iterations because the repair brief is in it. Moving repair brief out of the cacheable prefix and into the per‑invocation suffix would save ≈ 9K cache‑creation tokens × 3 panelists = **27K cache‑creation tokens per revise iteration ≈ $0.10 per feature** that revises. On the failing R006 run that alone is 4% of the bill.
2. **Critic call 10 wrote 24K cache tokens with 44K read** *(structural)*. Critic is reading a much larger cache (44K) than panelists (12.8K) but also writing 24K of new cache. Suggests the critic's prefix differs from the panelist prefix on every iteration. If critic's prefix were stabilized similarly, ≈ $0.07 saved per revise iteration.
3. **Output dominates cost (60%)** *(material)*. PlanContract regenerated by every panelist + every consolidator. Consolidator‑02 alone produced 32K chars of JSON ≈ 20K output tokens ≈ $0.30. Iteration 2 panelists collectively produced 96K chars of output. **The model is rewriting the same plan structure 6+ times per feature.** Reducing output via:
   - **Diff‑mode panelists on revise iterations** — emit `{"add": [...], "modify": [...], "remove": [...]}` against the prior consolidated plan instead of regenerating the full PlanContract. Reconstruct in code. Plausible savings: 60% of iter‑2 panelist output × 3 panelists = **~$0.40 per revise iteration**.
   - **Tighter JSON schema** that disallows free‑text wrappers around the JSON object. Sonnet often emits a sentence of preamble before the JSON; that preamble is pure waste.
4. **Consolidator‑02 prompt is 97K chars** *(scoping bug)*. Consolidator was passed all 6 candidates (3 from iter 1 + 3 from iter 2) when iter 2's consolidator should only see the new iter 2 candidates plus the prior consolidated plan as a baseline. Cutting the scope saves ~50K prompt chars ≈ ~$0.06 from cache write alone, plus the model has less to read.
5. **Iter‑2 panelists run 3‑wide on revise** *(policy)*. Iteration 1 needs 3 panelists for council voice. Iteration 2 fixing identified must‑fix items doesn't necessarily need 3 voices — it needs targeted repair. Reducing iter‑2 panelists from 3 to 1 saves 2× panelist calls = ~**$0.46 per revise iteration**.
6. **Use Haiku for consolidator + critic** *(model routing)*. Both are mechanical — consolidator merges typed candidates per a rubric; critic emits per‑check structured results. Neither is a reasoning sink. Haiku at $0.80 input / $4 output is ~4× cheaper. Two consolidator + two critic calls cost $1.66 today; on Haiku ~$0.42. Saves **~$1.24 per revise feature, ~$0.30 per accepted‑iter‑1 feature**.
7. **Skip the model critic when production_grade scores 100/100** *(optimization gate)*. R006's critic‑01 must have returned `revise` because iter 2 ran. If `production_grade.score >= 100 and validator_errors == []` we should accept without invoking the model critic at all. Saves $0.20 per clean‑first‑pass feature.
8. **Estimator is 2–4× under‑actual** *(observability fix)*. R006 panelist‑01: estimated 9,586 / actual 29,233. The dashboard is lying (in the optimistic direction). Either fix the char/4 heuristic with real `tiktoken` counting, or trust Anthropic's response metadata as the source of truth and stop emitting estimates.

### 10.4 What stays good

- **Cache READ is cheap and working well.** $0.04 of $2.77 is cache read — basically free. Don't touch it.
- **Token Savior in `token_savior_context.txt`** (9,898 chars) shows the packed context is small. The compression piece is doing its job for project context; the cost waste is downstream.
- **Quality gates pass.** Every accepted run shows accept(100) / prod accept(100). We are not paying for low quality.

### 10.5 Cost projection at scale

Assumed feature mix from these runs (rough):

| Feature shape | Cost per feature (today) | After § 10.3 fixes (estimate) |
|---|---:|---:|
| Small (R003) | $0.95 – $1.03 | $0.55 – $0.60 |
| Medium (R001) | $0.95 – $1.03 | $0.55 – $0.60 |
| Wide accept‑iter‑1 | $1.0 – $1.5 | $0.55 – $0.85 |
| Wide accept‑iter‑2 (R006) | $2.4 – $2.8 | $1.2 – $1.5 |

For 100 features/month at 70/30 small‑to‑wide mix: today **~$120/month**, after fixes **~$60–70/month**. The 50% reduction comes mostly from items 3 (output diff mode), 5 (single‑panelist revise), and 6 (Haiku routing).

### 10.6 What to fold back into briefs

- **Brief #1 (`token-savings-fix-and-prefix-cache.md`)** — amend: cacheable prefix must be **iteration‑stable** (repair brief in suffix); add a unit test that asserts `cache_creation_input_tokens` is approximately equal across iterations 1 and 2 of a feature. Replace the "add prefix caching" framing with "stabilize and verify the existing prefix caching."
- **Brief #1** — also amend: replace the char/4 estimator with `tiktoken` counts (or accept Anthropic's metadata as canonical and drop the estimate column).
- **New brief: `iter-2-and-output-economy.md`** — covers items 3, 4, 5, 6, 7. Captures the diff‑mode panelist, scoped consolidator, single‑panelist revise, Haiku routing, and production_grade preemption together because they share design surface (the council loop in `pgloom_engineering/planner/council.py`).

### 10.7 Codex GPT‑5.5 high — different cost shape, cross‑backend routing

The codex runs at `docs/reports/live-planner-2026-05-03-r00{2,3}-codex-gpt55-high*` and `live-planner-2026-05-03-codex-reconciled-comparison.json` reveal that codex behaves very differently from Claude on the same workload — and not in the direction the headline pricing implies.

**Headline pricing comparison:**

| Backend | Input | Cache write | Cache read | Output |
|---|---:|---:|---:|---:|
| Claude Sonnet 4.5 | $3.00/M | $3.75/M | $0.30/M | $15.00/M |
| Codex GPT‑5.5 (high reasoning) | $5.00/M | n/a (no explicit cache write) | $0.50/M | $30.00/M |

Codex looks 2× more expensive at the per‑token level. **In practice it is cheaper per accepted feature.** Reconciled R003 numbers:

| Backend | Iter | Calls | Actual tokens | API‑equiv cost |
|---|---:|---:|---:|---:|
| Claude Sonnet | 1 | 4–5 | 173K–267K | $1.07–$1.47 |
| Codex GPT‑5.5/high | 1 | 5 | 124K | **$0.73** |
| Codex GPT‑5.5/high | 2 | 8 | 188K | **$1.13** |

**Why codex wins despite higher unit pricing:**

1. **Codex outputs are ~3–4× shorter than Claude's.** Codex panelist response: ~11K chars / ~2,700 actual_output_tokens. Claude Sonnet panelist: ~19–32K chars / ~6,300–9,800 actual_output_tokens. Same valid PlanContract, much less padding/preamble.
2. **Codex uses `actual_input_tokens` directly with implicit cache reads.** Every codex row shows `cache_creation_input_tokens: null` and a stable `cache_read_input_tokens: 7552`. OpenAI auto‑caches the prefix server‑side; there is no client‑driven cache write step, so the iter‑1‑vs‑iter‑2 instability that costs Claude $0.10+ per revise feature **does not apply to codex.** Item 1 in § 10.3 is Claude‑specific.
3. **Reasoning tokens tracked separately** (`reasoning_output_tokens: 516` on every call). High reasoning effort accounts for some of the cost but produces a shorter, cleaner final output. The CoT budget is roughly fixed regardless of prompt complexity.

**Same waste sources still apply:**

| § 10.3 item | Claude impact | Codex impact |
|---|---|---|
| 1. Prefix unstable across iterations | ~$0.10/revise feature | **N/A** (codex has no explicit cache write) |
| 2. Critic prefix differs from panelist | ~$0.07/revise feature | **N/A** (same reason) |
| 3. Output dominates cost | $1.66 of $2.77 (60%) on R006 | $0.40 of $0.73 (55%) on R002 — still the largest share |
| 4. Consolidator scope bloat | ~$0.06/revise feature | Smaller because codex consolidator prompts are ~40K chars vs Claude's 97K — but still applies |
| 5. 3 panelists on revise | ~$0.46/revise feature | ~$0.20/revise feature (codex panelist ~$0.10 each) |
| 6. Mechanical roles on flagship model | $0.30–$1.24/feature | $0.10–$0.50/feature |
| 7. Skip critic when production_grade=100 | ~$0.20/feature | ~$0.10/feature |
| 8. Estimator inaccurate | Same | Same — codex estimator is also ~3× under (e.g. estimated 6,030 / actual 20,584 input) |

**Cross‑backend model routing matrix:**

| Role | Backend | Today | Recommended |
|---|---|---|---|
| Panelist (reasoning) | Claude | Sonnet | Sonnet — keep |
| Panelist (reasoning) | Codex | gpt‑5.5/high | gpt‑5.5/high — keep |
| Consolidator (mechanical) | Claude | Sonnet | **Haiku** |
| Consolidator (mechanical) | Codex | gpt‑5.5/high | **gpt‑5.3** (Josh's suggestion) **or** gpt‑5.5/medium reasoning **or** gpt‑5.5/low reasoning |
| Critic (rubric) | Claude | Sonnet | **Haiku** |
| Critic (rubric) | Codex | gpt‑5.5/high | **gpt‑5.3** **or** lowered reasoning effort |
| Production_grade (deterministic) | n/a | Python only | Python only — no model |

Two equivalent codex levers: **swap to a cheaper model tier** (gpt‑5.3) **or** **lower reasoning_effort** (`-c model_reasoning_effort="medium"` or `"low"`). The current invocation pins all three roles at high reasoning. For consolidator and critic — both rubric‑driven mechanical work — high reasoning is overkill. Either lever moves the cost shape without changing the model family; lowered reasoning is the smaller blast radius (no new profile required, just a config change).

**Open question (verify against the codex CLI before adoption):** which exact model name should the codex consolidator/critic use? Josh suggested `gpt-5.3`; the alternatives are `gpt-5.5` with reduced `model_reasoning_effort`. Run `codex models` (or whichever subcommand exposes the available list) to confirm what's available; the brief will pick the first viable option.

**Backend‑routing brief impact.** The iter‑2 economy brief at `docs/prompts/iter-2-and-output-economy.md` § 3.4 is updated to specify per‑backend model routing and per‑backend reasoning effort settings rather than the original Claude‑only "Haiku" recommendation. See that brief for the concrete settings surface.
