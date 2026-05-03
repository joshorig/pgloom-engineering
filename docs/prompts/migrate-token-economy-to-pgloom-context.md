# Migrate Token Economy to `pgloom.context`

## Objective

Adopt the shared token-economy primitives from `pgloom.context` while preserving
`pgloom-engineering` planner behavior, existing database rows, and live planner eval outputs.

Do not remove engineering-specific adapters. Move only generic accounting/counting/packing
responsibility to pgloom.

## Background

`pgloom` now provides:

- `pgloom.context.count_tokens`
- `pgloom.context.TokenBudget`
- `pgloom.context.ContextContributor`
- `pgloom.context.ContextBuilder`
- `pgloom.context.ContextPack`
- `pgloom.context.PromptCacheKey`
- `pgloom.context.TokenSavingsRecord`
- `pgloom.context.record_token_savings`
- `pgloom.context.list_token_savings`
- `pgloom.context.summarize_token_savings`

`pgloom-engineering` should keep:

- Token Savior code-repo packing
- planner context lenses
- planner context capsules
- deterministic skeletons
- repair briefs
- production-grade checks
- RTK command-specific filters
- any engineering-specific reporting fields

## Migration Steps

1. Bump `pgloom` dependency to a version that contains `pgloom.context`.

2. Replace token counting imports:

   ```python
   from pgloom_engineering.token_count import count_tokens
   ```

   with:

   ```python
   from pgloom.context import count_tokens
   ```

   Then delete `pgloom_engineering/token_count.py` only after all imports are moved.

3. Adapt planner context packing result types.

   Keep `TokenSaviorContextResult` if it carries planner-specific `ProjectContext`, but align
   field names and semantics with `pgloom.context.ContextPack`:

   - `input_tokens_original`
   - `input_tokens_packed` or existing `input_tokens_after_savior`
   - `tokens_saved`
   - `reduction_ratio`
   - `method`
   - `packed_context`
   - contributor metadata where available

4. Keep `engineering_project_context_capsules`.

   The capsule table is planner-specific and should stay in `pgloom-engineering`. It can store
   `ContextPack`-compatible fields, but pgloom should not know about projects, ROADMAP,
   DECISIONS, context lenses, or git heads.

5. Migrate savings writes.

   For new writes, call `pgloom.context.record_token_savings` in addition to the existing
   `engineering_token_savior_usage` writer during a transition period.

   Map fields:

   ```text
   feature_id -> scope_id
   workflow_id -> workflow_id
   task_id -> task_id
   model_usage_id -> model_usage_id
   profile_name -> profile_name
   input_tokens_original -> input_tokens_original
   input_tokens_after_savior -> input_tokens_after
   tokens_saved -> tokens_saved
   reduction_ratio -> reduction_ratio
   estimated_cost_saved_usd -> estimated_cost_saved_usd
   metadata -> metadata
   ```

   Include `metadata.source_table = "engineering_token_savior_usage"` while dual-writing.

6. Keep existing reports stable.

   `pgloom-engineering feature show` should continue reading `engineering_token_savior_usage`
   until there is a deliberate report migration. Add optional display of pgloom-level
   `token_savings` totals only after parity tests pass.

7. Adapt RTK filter accounting.

   `pgloom_engineering.rtk.filter` should keep all command-specific filtering behavior, but it
   can use:

   - `pgloom.context.count_tokens`
   - `pgloom.context.TokenSavingsRecord`
   - `pgloom.context.record_token_savings`

   Continue registering unfiltered stdout/stderr artifacts exactly as today.

8. Add tests.

   Required tests:

   - Existing planner council tests still pass.
   - Existing RTK tests still pass.
   - Existing `engineering_token_savior_usage` integration tests still pass.
   - New test proves dual-write creates matching totals in `token_savings`.
   - New test proves context capsule cache behavior is unchanged.

9. Remove duplicated code only after dual-write parity.

   Safe removals after migration:

   - `pgloom_engineering/token_count.py`

   Do not remove:

   - `pgloom_engineering/token_savior.py` until reports are migrated.
   - `engineering_token_savior_usage` table until historical reporting is migrated.
   - planner context capsule code.

## Non-goals

- Do not move Token Savior imports into pgloom.
- Do not move RTK command filters into pgloom.
- Do not make pgloom aware of ROADMAP, DECISIONS, QA paths, feature goals, or planner roles.
- Do not change live planner prompt shape unless a separate eval proves cost/quality parity.

## Acceptance

- `ruff`, `mypy`, and `pytest` pass.
- DB-backed migration tests pass.
- Planner live eval smoke still produces the same accepted/revised verdicts on the fixed eval corpus.
- `engineering_token_savior_usage` totals and `token_savings` totals match for dual-written rows.
- No domain-specific code is added to `pgloom.context`.
