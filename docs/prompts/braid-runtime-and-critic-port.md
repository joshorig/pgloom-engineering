# Implementor brief — BRAID runtime is parked

> **Status: DEFERRED INDEFINITELY.** Do not implement the Mermaid DSL, graph parser, graph runner, template registry, or R1-R7 lint engine unless a concrete future need appears. The master plan now treats Track C as bounded rubrics, not BRAID runtime.

## Decision

BRAID was useful in the legacy orchestrator because it was the only multi-step orchestration primitive available. In `pgloom-engineering`, the load-bearing primitives are now different:

- Pydantic contracts define every handoff.
- Worker pre/post gates reject missing, stale, or invalid contracts.
- `engineering_handoffs` and `engineering_recovery_actions` provide durable audit and self-heal inputs.
- The pgloom task DAG provides dependencies, retries, attempts, and blocker codes.
- Planner council loops provide propose -> consolidate -> critique -> validate -> revise behavior directly in Python.

The retained idea from BRAID is bounded structured critique: explicit checks with stable IDs, short prompts, per-check evidence, mechanically computed verdicts, and optional parallel execution. That becomes a Python-native rubric pattern.

## Replacement

The future implementation target is a small shared rubric layer, extracted only when multiple roles need it:

```text
CheckDefinition
RubricDefinition
RubricRunner
RubricVerdict
revise_until_clean(...)
```

The first concrete rubric is the planner critic in `pgloom_engineering/planner/critic.py`. Reviewer and QA panels should reuse that pattern later.

## What Would Resurrect BRAID

Reconsider BRAID only if one of these becomes real:

- humans need to author and review workflow templates as data
- lint-time topology guarantees become more valuable than Python test coverage
- a measured cost/accuracy regression shows Mermaid-style structured prompts outperform typed rubrics for our production workflows
- external systems need portable workflow diagrams as runtime artifacts

Until then, Mermaid diagrams may exist in docs, but not as runtime source of truth.
