# Implementor brief — iteration‑2 + output economy

> **Audience.** A coding agent with full read/write on `/Volumes/devssd/repos/oss/pgloom-engineering`. Treat this brief as the complete spec.
>
> **Goal.** Cut planner cost by ~50% on revise‑iteration features and ~30% on accept‑iter‑1 features by addressing five concrete waste sources identified in the live planner runs of 2026‑05‑03. Quality must not regress (`validate_plan_contract` errors and critic blocking findings within 10% of baseline; `iterations_to_accept` no worse on any replayed feature).
>
> **Scope size.** Medium. Three to five days of work. Several independent levers; can be split into separate PRs if the implementor prefers, but the eval framework needs to be in place from PR 1 to validate each.

> **Reads first.** `docs/notes/token-economy.md` § 10 has the per‑call cost breakdown that motivates this brief. Do not start without reading it. The R006 wide failed‑cost trace at `docs/reports/live-planner-suite-2026-05-03-103809-live-planner-autonomy-baseline/dag-r006-wide__claude-sonnet/model_usage.jsonl` is the canonical regression target.

---

## 1. Why this work exists

R006 wide failed the $2.25 cost gate at $2.77 actual / $2.37 API‑equiv. Of that $2.77:

- Output: $1.66 (60%)
- Cache creation: $0.71 (25%)
- Cache read: $0.04 (~1.5%)
- Direct input + overhead: rest

Five specific waste sources, ranked by ROI per `docs/notes/token-economy.md` § 10.3 items 3, 4, 5, 6, 7:

| # | Waste | Today's cost | Estimated savings |
|---|---|---|---|
| 3 | Panelists regenerate full PlanContract on every iteration; consolidator does too | iter‑2 panelist output ~$0.46 + consolidator‑02 output ~$0.30 = ~$0.76 per revise feature | ~$0.40 per revise feature (60% of iter‑2 panelist output) |
| 4 | Consolidator‑02 sees all 6 candidates instead of latest 3 + prior baseline | ~$0.06 cache write waste | ~$0.06 per revise feature |
| 5 | 3 panelists run on revise iterations | ~$0.69 per revise iteration | ~$0.46 per revise feature |
| 6 | Consolidator + critic use the flagship model when a cheaper tier suffices | Claude: $1.66 across 2 cons + 2 critic on R006. Codex: ~$0.50 of $1.13 on R003‑reconciled. | Claude: ~$1.24 per revise feature, ~$0.30 per accept‑iter‑1. Codex: ~$0.40 per revise feature, ~$0.10 per accept‑iter‑1. (Per‑backend lever differs — see § 3.4.) |
| 7 | Model critic runs even when production_grade returns clean 100/100 | ~$0.20 per accept‑iter‑1 feature | ~$0.20 per accept‑iter‑1 feature |

Combined: **~50% cost reduction on revise features, ~30% on accept‑iter‑1**. Projected monthly cost (100 features) drops from ~$120 to ~$60–70.

---

## 2. Scope

**In scope.**

1. **Diff‑mode panelist on revise iterations** (item 3). New panelist prompt and output schema for iter ≥ 2 that emits a structured plan diff against the prior consolidated plan. New code path in `pgloom_engineering/planner/council.py` that reconstructs the full `PlanContract` from the prior plan + the diff before passing to consolidator.
2. **Scoped consolidator inputs** (item 4). Iter‑2 consolidator sees only iter‑2 candidates plus the iter‑1 consolidated plan as a baseline, not all 6 candidates.
3. **Single‑panelist revise mode** (item 5). New `Settings` flag `planner_iter_2_panelist_count: int = 1`. When enabled, iter ≥ 2 spawns 1 panelist focused on the must‑fix repair brief instead of the iter‑1 panelist count. Default flag value to 1 with a kill‑switch back to 3 if regressions appear.
4. **Model routing per profile** (item 6). New `Settings` fields `planner_consolidator_model: str = "haiku"` and `planner_critic_model: str = "haiku"`; `planner_panelist_model` stays Sonnet. Wired through `EngineeringCLIModelProvider.invoke` so each profile's `CLIModelProfile.command` carries the correct `--model` argument.
5. **Production_grade preempts model critic** (item 7). When `production_grade.score >= 100 and validator_errors == []`, skip the model critic and treat the iteration as accepted. New `metadata.method = "production_grade_preempted_critic"` row in `engineering_token_savior_usage` recording the saved cost.
6. **Replay regression eval** that proves each item independently does not increase `validate_plan_contract` errors, critic blocking findings, or `iterations_to_accept` beyond thresholds. Lives at `scripts/replay_iter2_economy_eval.py`.

**Out of scope.**

- Token Savior wiring (separate brief: `docs/prompts/token-savings-fix-and-prefix-cache.md`).
- RTK adoption (separate brief: `docs/prompts/rtk-subprocess-filter.md`).
- Any change to the rubric checks themselves in `critic.py` (the critic's *what* is unchanged; only its *when* and its *which model* change).
- Any change to the contract Pydantic schemas in `contracts.py`. Diff mode adds an internal Pydantic helper, not a new contract surface visible to handlers downstream.
- DB schema migrations.
- Implementer / Reviewer / QA Engineer handlers.

---

## 3. Required surfaces

### 3.1 Diff‑mode panelist (item 3)

New module `pgloom_engineering/planner/plan_diff.py`:

```python
class SliceDiff(BaseModel):
    op: Literal["add", "modify", "remove"]
    slice_id: str
    after: TaskSliceContract | None = None        # required for add/modify, null for remove
    rationale: str

class PlanDiff(BaseModel):
    """Iter-2+ panelists emit this instead of regenerating the full PlanContract."""
    base_plan_contract_hash: str                  # hash of the prior consolidated plan
    slice_diffs: list[SliceDiff]
    design_contract_changes: dict[str, Any] = {}  # field-level overrides; empty if unchanged
    acceptance_test_matrix_additions: list[str] = []
    acceptance_test_matrix_removals: list[str] = []  # bounded; refused if would empty the matrix
    risk_register_additions: list[str] = []

def apply_plan_diff(base: PlanContract, diff: PlanDiff) -> PlanContract:
    """Reconstruct a full PlanContract from a base + diff. Validates that
    base_plan_contract_hash matches contract_hash(base) — refuses if not."""
```

`pgloom_engineering/planner/panelist.py:_build_prompt`:

- For iteration 1: emit the existing prompt, expecting full `PlanContract` JSON output.
- For iteration ≥ 2: emit a different prompt (`prompts/panelist_revise.md`, new file) that includes the prior consolidated `PlanContract`, the critic's blocking findings, and the validator errors. Output expected is `PlanDiff` JSON (not `PlanContract`).
- The model prompt explicitly enumerates the schema and includes a one‑line example for each `op`.

`pgloom_engineering/planner/council.py`:

- Detect iteration index in the panelist call site.
- For iter ≥ 2, accept the panelist's `PlanDiff`, apply it to the prior consolidated `PlanContract` via `apply_plan_diff`, then continue with the existing flow (consolidator sees a fully‑reconstructed candidate `PlanContract` per panelist, just like iter 1).

### 3.2 Scoped consolidator inputs (item 4)

`pgloom_engineering/planner/consolidator.py`:

- `Consolidator.merge(proposals: list[CouncilProposal], *, prior_consolidated: PlanContract | None = None)` — new keyword.
- For iter 1: `prior_consolidated=None`, behavior unchanged.
- For iter ≥ 2: caller passes the iter‑1 consolidated `PlanContract` as `prior_consolidated` and **only** the iter‑2 proposals in `proposals`. The consolidator prompt is updated to "merge these new candidates into the prior baseline plan" instead of "merge these N candidates into one." `prompts/consolidator_revise.md` is the new prompt template.

### 3.3 Single‑panelist revise (item 5)

`pgloom_engineering/config.py`:

```python
planner_iter_1_panelist_count: int = 3              # unchanged behavior
planner_iter_2_panelist_count: int = 1              # NEW; 1 by default, configurable up to planner_iter_1_panelist_count
```

`pgloom_engineering/planner/council.py:run`:

- For iteration 1: spawn `planner_iter_1_panelist_count` panelists.
- For iteration ≥ 2: spawn `planner_iter_2_panelist_count` panelists, all with the same lens (the lens that owned the most blocking findings on iter 1 — fall back to lens 0 if tied).
- The single iter‑2 panelist (when count=1) sees the full repair brief and is responsible for addressing all must‑fix items. With multi‑panelist iter‑2, they split the must‑fix items proportionally if a clean partition is possible; otherwise each gets the full set (as today).

### 3.4 Model routing per profile (item 6) — backend‑aware

The current setup runs both Claude and Codex backends. Each has a different "cheaper for mechanical work" lever (see `docs/notes/token-economy.md` § 10.7 for the analysis):

- **Claude lever**: swap the model. Sonnet → Haiku for mechanical roles. ~4× cheaper input + output.
- **Codex lever**: either (a) swap the model (gpt‑5.5 → gpt‑5.3 if available) or (b) lower the reasoning effort (`model_reasoning_effort="medium"` or `"low"` instead of `"high"`). Lowered reasoning is smaller blast radius — same model family, just cheaper CoT budget.

`pgloom_engineering/config.py` — backend‑aware settings:

```python
# Panelist (reasoning role) — keep flagship on each backend
planner_claude_panelist_model: str = "sonnet"
planner_codex_panelist_model: str = "gpt-5.5"
planner_codex_panelist_reasoning: str = "high"

# Consolidator (mechanical merge)
planner_claude_consolidator_model: str = "haiku"
planner_codex_consolidator_model: str = "gpt-5.5"        # or "gpt-5.3" if available — verify via `codex models`
planner_codex_consolidator_reasoning: str = "medium"     # lowered from high

# Critic (rubric scoring)
planner_claude_critic_model: str = "haiku"
planner_codex_critic_model: str = "gpt-5.5"              # same model swap caveat as consolidator
planner_codex_critic_reasoning: str = "medium"

# Fallback policy when the cheaper tier emits malformed JSON
planner_claude_haiku_fallback_to_sonnet: bool = True     # one-retry escalation
planner_codex_low_reasoning_fallback_to_high: bool = True
```

`pgloom_engineering/model_provider.py:EngineeringCLIModelProvider.invoke`:

- Honor the profile's declared model **and reasoning effort**. The existing `CLIModelProfile.command` for codex carries `-m gpt-5.5 -c model_reasoning_effort="high"`; replace with the role‑specific values resolved from settings at profile construction time.
- The existing Claude profile carries `--model sonnet`; replace with the role‑specific Claude model from settings.
- Backend detection via `CLIModelProfile.command[0]` (`claude` vs `codex`) drives which settings keys to pull.

`pgloom_engineering/planner/council.py` (and `critic.py`, `consolidator.py` if they construct profiles):

- Load the per‑backend per‑role model + reasoning from settings when constructing the `CLIModelProfile`.

**Pre‑integration verification step (mandatory).** Before wiring `gpt-5.3` as a fallback option, run `codex --help` (or `codex models`) against the installed binary to confirm what model names are available. If `gpt-5.3` is not present, default the codex consolidator/critic settings to `gpt-5.5` with `model_reasoning_effort="medium"` instead — same cost reduction goal, different lever, no risk of an invalid model name. Document the verified available model list in the completion report.

**Quality gate for this item is mandatory.** Replay R001/R003/R006 against both backends with the cheaper mechanical settings; assert validator errors + critic blocking findings counts are within 10% of the Sonnet/gpt‑5.5‑high baseline per feature per backend. If quality regresses:

- For Claude with Haiku: enable `planner_claude_haiku_fallback_to_sonnet`. Implementable as a one‑retry escalation when Haiku returns invalid JSON or fails the rubric structure.
- For Codex with reduced reasoning: enable `planner_codex_low_reasoning_fallback_to_high`. Same one‑retry escalation pattern.

If both regress materially even with the fallback, the implementor reverts model routing for the affected role and records a `known_quality_regression: true` flag in the completion report along with example prompts that exposed the regression.

### 3.5 Production_grade preempts critic (item 7)

`pgloom_engineering/planner/council.py`:

- After `production_grade.evaluate_production_grade(consolidated, ...)` returns:
  - If `report.verdict == "accept"` AND `validator_errors == []`, skip the model critic call entirely. Construct a synthetic `CriticVerdict` from the production_grade report (verdict=accept, per_check_results derived from the deterministic checks, model_usage_id=None) and proceed as if the critic had accepted.
  - Otherwise invoke the model critic as today.
- Record `engineering_token_savior_usage` row with `metadata.method="production_grade_preempted_critic"`, `tokens_saved` = the typical critic call's token budget (estimate), `metadata.role="critic"`, `metadata.preempted=true`.

### 3.6 Settings summary

```python
# Iteration economy
planner_iter_1_panelist_count: int = 3
planner_iter_2_panelist_count: int = 1
planner_revise_diff_mode_enabled: bool = True
planner_consolidator_scoped_inputs_enabled: bool = True
planner_production_grade_preempts_critic: bool = True

# Model routing
planner_panelist_model: str = "sonnet"
planner_consolidator_model: str = "haiku"
planner_critic_model: str = "haiku"
planner_consolidator_haiku_fallback_to_sonnet: bool = True   # one-retry escalation if Haiku output is malformed
planner_critic_haiku_fallback_to_sonnet: bool = True
```

All `PGLOOM_ENGINEERING_*` prefixed.

### 3.7 Replay regression eval

`scripts/replay_iter2_economy_eval.py` — variant of the existing live planner suite that runs against the captured corpus at `docs/reports/live-planner-suite-*` and emits a comparison report.

For each captured run: replay it 6 times — baseline (all flags off), each item enabled in isolation (5 runs), all enabled together (1 run). Record per‑run `cost_usd`, `validator_errors`, `critic_blocking_findings`, `iterations_to_accept`, `output_tokens`, `cache_creation_tokens`.

Emit `docs/reports/iter2-economy-baseline-YYYY-MM-DD.md` with a per‑feature table and aggregates. The eval is the gate for this brief — every item must pass independently and the combined run must not regress quality versus baseline.

---

## 4. Tests

### Unit tests

- `tests/unit/test_plan_diff.py`
  - `test_apply_diff_add_slice` — add a new slice; assert it appears in the right position per `depends_on`.
  - `test_apply_diff_modify_slice` — modify an existing slice's `objective` and `verification_commands`; assert other slices unchanged.
  - `test_apply_diff_remove_slice` — remove a slice; assert it's gone and downstream `depends_on` references are flagged.
  - `test_apply_diff_refuses_when_base_hash_mismatch` — pass a diff whose `base_plan_contract_hash` doesn't match `contract_hash(base)`; assert the function refuses.
  - `test_diff_cannot_empty_acceptance_matrix` — diff with `acceptance_test_matrix_removals` that would leave the matrix empty; assert refused.

- `tests/unit/test_council_iter_2_panelist_count.py`
  - `test_iter_1_spawns_3_panelists` — fake council, count assertion.
  - `test_iter_2_spawns_1_panelist_by_default` — count assertion.
  - `test_iter_2_lens_selection_picks_most_blocking` — feed iter‑1 critic findings with 3 blocking on lens "qa", 1 each on others; assert the iter‑2 panelist gets the qa lens.

- `tests/unit/test_consolidator_scoped_inputs.py`
  - `test_iter_1_consolidator_sees_all_proposals` — count assertion.
  - `test_iter_2_consolidator_sees_only_iter_2_plus_baseline` — assert the proposals list passed to the consolidator excludes iter‑1 proposals; assert `prior_consolidated` is the iter‑1 consolidated plan.

- `tests/unit/test_model_routing.py`
  - `test_claude_consolidator_uses_haiku_when_configured` — fake Claude provider records the `--model` argv; assert "haiku".
  - `test_claude_critic_uses_haiku_when_configured` — same.
  - `test_claude_panelist_uses_sonnet_when_configured` — assert "sonnet".
  - `test_claude_haiku_fallback_to_sonnet_on_malformed_json` — fake provider returns garbage on first Haiku call; assert the council retries with Sonnet for that one invocation.
  - `test_codex_consolidator_uses_lower_reasoning_when_configured` — fake codex provider records the `-c model_reasoning_effort=...` argv; assert "medium" (or whatever the configured value is).
  - `test_codex_critic_uses_lower_reasoning_when_configured` — same.
  - `test_codex_panelist_keeps_high_reasoning` — assert "high".
  - `test_codex_low_reasoning_fallback_to_high_on_malformed_json` — fake provider returns garbage on first low‑reasoning call; assert the council retries with high reasoning.
  - `test_codex_uses_gpt_5_3_when_available` — guarded by a feature flag indicating the model is available in the installed CLI; if the flag is off, asserts the fallback to gpt‑5.5 + medium reasoning.

- `tests/unit/test_production_grade_preempts_critic.py`
  - `test_skips_critic_when_production_grade_accepts_clean` — feed a plan where `production_grade` returns `verdict=accept, score=100` and `validator_errors=[]`; assert no critic call is made; assert the synthetic `CriticVerdict` reflects accept.
  - `test_invokes_critic_when_production_grade_returns_revise` — assert critic is called normally.
  - `test_records_preemption_in_token_savior_usage` — assert a row with `metadata.method="production_grade_preempted_critic"` is written.

### Integration tests (Postgres‑gated)

- `tests/integration/test_iter2_economy_e2e.py` — runs against R001/R003/R006 captured fixtures (or live if cheap):
  - With all flags off: baseline cost matches the recorded R006 ~$2.77.
  - With all flags on: cost reduction ≥ 35% on R006, quality unchanged.
  - With diff mode only: assert iter‑2 panelist output is < 50% of iter‑2 panelist baseline output.
  - With scoped consolidator only: assert iter‑2 consolidator prompt is < 70% of baseline iter‑2 consolidator prompt size.
  - With single‑panelist revise only: assert iter‑2 calls drop from 5 (3 panelists + cons + critic) to 3 (1 panelist + cons + critic).
  - With Haiku routing only: assert per‑call cost on consolidator + critic drops by ≥ 60% versus Sonnet baseline; quality within thresholds.
  - With production_grade preemption only: replay a clean‑first‑pass feature; assert no model critic call.

### Replay regression

- `tests/integration/test_iter2_economy_regression.py` — invokes `scripts/replay_iter2_economy_eval.py` against the captured corpus, asserts the report's summary indicates within‑threshold deltas. Marked `@pytest.mark.slow`.

---

## 5. Acceptance gate

1. **Static gates clean.** `ruff check`, `mypy` exit 0.
2. **All new unit tests pass.** Listed in § 4.
3. **All existing unit tests still pass.** No regression.
4. **Integration tests pass.** Each item independently demonstrates the cost reduction from § 1 within ±25% (item‑specific savings vary; the eval should report the exact realized number).
5. **Combined run on R006 reduces cost by ≥ 35%** versus the recorded $2.77 baseline. Quality unchanged: `validate_plan_contract` errors and critic blocking findings within ±10% of baseline; `iterations_to_accept` no worse on any replayed feature; final accepted plan structurally equivalent (same slice count, same role distribution, same acceptance matrix categories).
6. **Replay regression eval green.** `scripts/replay_iter2_economy_eval.py` against the corpus emits a report whose summary line says `regression_within_thresholds=true`.
7. **Per‑item kill switches work.** Each Settings flag from § 3.6 can independently revert that item to today's behavior. Verified by a dedicated unit test per flag.
8. **Haiku quality acceptable.** If the Haiku replay shows > 10% increase in critic blocking findings or validator errors on any feature, the implementor either (a) reverts model routing for the affected role or (b) adds a stronger fallback policy (e.g. always retry with Sonnet on first Haiku critic blocking finding) and re‑validates.
9. **No edits outside the in‑scope surface** in § 2. No changes to `contracts.py` (except the new `PlanDiff` Pydantic if placed here, which is acceptable since it doesn't alter persisted contracts), no changes to worker pre/post gates, no DB schema migrations.
10. **Self‑review.** Implementor agent runs a council‑style review on its own diff before declaring done; report includes `git diff --stat`, the realized R006 cost reduction, per‑item realized savings, and any quality regressions identified along with their mitigation.

---

## 6. Implementation notes / pitfalls

- **Diff mode parsing fragility.** Sonnet may emit either a full PlanContract (regenerating despite the prompt asking for a diff) or a diff with surface mistakes (e.g. modifying a slice without setting `op="modify"`). Build a tolerant parser that detects the case and either accepts whichever it gets, or escalates to a Sonnet retry with a stricter prompt. Log every escalation to `engineering_token_savior_usage.metadata.diff_mode_fallback`.
- **Hash mismatch in diff mode.** If the base PlanContract hash in the diff doesn't match the actual prior consolidated plan, the panelist's diff is invalid. Fall back to asking that panelist to regenerate the full PlanContract (one Sonnet retry); record the cost as `metadata.method="diff_mode_hash_mismatch_fallback"`.
- **Single‑panelist revise risks an echo chamber.** With only 1 panelist on iter 2, there's no council voice, just one model fixing the must‑fix items. If quality degrades, the kill switch sets the count back to 3 — but document the trade‑off so a future operator understands why this default exists.
- **Lens selection for single‑panelist revise.** The criterion in § 3.3 is "lens that owned the most blocking findings on iter 1." Tie‑break by lens index. This is heuristic; if it underperforms on the corpus, evaluate alternatives (e.g. always use lens 0; rotate per iteration; or have the consolidator suggest which lens).
- **Haiku output quality.** Haiku can sometimes emit malformed JSON or skip rubric checks. The `haiku_fallback_to_sonnet` flag is mandatory; the eval must verify it triggers cleanly when needed without infinite loops.
- **production_grade preemption is risky if production_grade is too lenient.** Today it returns `accept (100)` on every passing run in the corpus. If we make it the only critic, any blind spot in production_grade becomes a permanent blind spot. The conservative posture: invoke the model critic on every Nth accepted iteration (configurable, default 1 in 10) as a sanity check; record the disagreement rate over time. Add this as a separate flag `planner_production_grade_critic_sample_rate: float = 0.1`.
- **Cost recording attribution.** Every change in this brief should produce `engineering_token_savior_usage` rows with a distinct `metadata.method` so the dashboard can attribute savings cleanly. Methods to introduce: `iter_2_diff_mode`, `iter_2_consolidator_scoped`, `iter_2_single_panelist`, `model_routing_haiku`, `production_grade_preempted_critic`.
- **Wall clock improves too.** R006's 10 calls took ~31 minutes wall clock. Cutting iter‑2 calls and routing consolidator/critic to Haiku (which is faster) likely halves that. Record the elapsed time alongside cost in the eval report.

---

## 7. Reference paths

| What | Where |
|---|---|
| Strategy doc + per‑call cost analysis | `docs/notes/token-economy.md` (especially § 10) |
| R006 wide failure trace | `docs/reports/live-planner-suite-2026-05-03-103809-live-planner-autonomy-baseline/dag-r006-wide__claude-sonnet/model_usage.jsonl` |
| Live planner suite reports | `docs/reports/live-planner-suite-2026-05-03-103809-...` |
| Pricing reference | `docs/reports/planner-pricing-2026-05-03.json` |
| Council loop | `pgloom_engineering/planner/council.py` |
| Panelist | `pgloom_engineering/planner/panelist.py` |
| Critic | `pgloom_engineering/planner/critic.py` |
| Consolidator | `pgloom_engineering/planner/consolidator.py` |
| Production grade (deterministic) | `pgloom_engineering/planner/production_grade.py` |
| Model provider | `pgloom_engineering/model_provider.py` |
| Settings | `pgloom_engineering/config.py` |
| Existing live eval scripts | `scripts/run_live_planner_eval_suite.py`, `scripts/verify_lvc_r002_planner.py` |
| Token Savior recording | `pgloom_engineering/token_savior.py` |
| Wiring + estimator brief (parallel work) | `docs/prompts/token-savings-fix-and-prefix-cache.md` |

---

## 8. Reporting back

Append a completion record at `docs/reports/iter-2-and-output-economy-completion.md` containing:

1. `git diff --stat` + last 30 lines of: ruff, mypy, unit tests, integration test.
2. Replay regression report (`docs/reports/iter2-economy-baseline-YYYY-MM-DD.md`) summary table — per‑item savings + combined.
3. Realized R006 cost after combined: target ≤ $1.80 (≥ 35% from $2.77).
4. Any item that did not meet its expected savings, with rationale.
5. Any quality regression identified during eval and how it was mitigated.
6. One‑paragraph self‑review covering: what was hardest (probably diff mode parsing), where the realized savings differed from the brief's estimates, what should be tuned next.
