# Planner Roadmap Test Candidates

Source: `/Volumes/devssd/repos/ull/lvc-standard/repo-memory/ROADMAP.md`

## Recommended Regression Matrix

### Small / Single-Candidate Baseline: R-003 Range-query API

Use R-003 to verify the planner can keep a straightforward API feature compact. The expected
plan should include design, implementation, reviewer, and QA slices, but should not explode into
many implementation panels.

Expected stress points:
- zero-allocation visitor API
- SINGLE and DOUBLE store coverage
- range semantics: empty, single-key, full keyspace, reverse scan
- alloc gate preservation

### Medium / Cross-Module Feature: R-005 SBE Schema Evolution Adapters

Use R-005 to verify the planner handles a moderate cross-module feature without lifecycle
durability complexity. It should produce compile-time validation, compatibility test matrix, and
QA/review gates.

Expected stress points:
- additive-only schema rule
- N-1/N/N+1 compatibility matrix
- `sbe-adapters` ownership boundaries
- no allocation regression

### High-Risk / Dependency-Gated Feature: R-006 Distributed Replication

Use R-006 to verify the planner respects roadmap dependencies and blocks or narrows scope when
prerequisites are incomplete. R-006 depends on R-001 and R-002; because R-002 is still TODO, the
planner should either produce a dependency blocker or explicitly scope work to design/spike tasks
that do not require final implementation.

Expected stress points:
- dependency gate on R-002 snapshot initial sync
- two-process integration tests
- replication lag metric from R-001
- manual failover exclusion
- active/standby behavioral parity

### Ambiguity / Replan Trigger: R-004 Journal Compression

Use R-004 as an ambiguous/dependency-sensitive candidate. It depends on R-002 and has hot-path
constraints that are easy to violate. A good planner should avoid starting full implementation
before snapshot format decisions are settled.

Expected stress points:
- dependency gate on R-002
- compression only on segment seal
- zero allocation on publish path
- poller decompression scratch-buffer reuse
- synthetic workload size-reduction benchmark

## Acceptance For Planner Tests

- R-003 accepted in one iteration with a compact plan.
- R-005 accepted with explicit compatibility matrix and reviewer/QA gates.
- R-006 blocked, scoped to dependency-safe design/spike work, or explicitly marks R-002 as a
  prerequisite blocker.
- R-004 triggers dependency-aware planning and does not schedule hot-path compression work that
  violates the roadmap constraints.
- Every run records `model_usage` rows for panelist, consolidator, and critic calls.
- Runs using packed project context record `engineering_token_savior_usage` rows linked to
  relevant `model_usage.id` values.

## Cross-Project Additions

### trade-research-platform

Registered as planning-only at `/Volumes/devssd/repos/apps/trade-research-platform`.

Recommended candidates:
- R-003 Crypto-domain config + diagnostics parity: compact config/controller/UI contract pass.
- R-016 SignalSpec validator/compiler/API: larger cross-module feature for planner breadth.
- R-019 Ingress backpressure: high-risk runtime resilience feature with clear hot-path constraints.

### dag-framework

Registered as planning-only at `/Volumes/devssd/repos/ull/dag_framework`.

Recommended candidates:
- R-001 Windowed aggregation node: medium runtime feature with watermark/late-arrival semantics.
- R-003 YAML graph topology loader: compact startup-only feature and good compactness test.
- R-006 Backpressure policy selector: high-risk runtime feature with spill-file and alloc gates.

## Compactness Pressure

Small or single-surface roadmap items should generally produce 4-6 slices:
design, 1-2 implementation slices, one reviewer slice, and one QA/finalization slice. Separate
historian, duplicate reviewer, and duplicate QA slices are treated as planner sprawl unless the
roadmap item explicitly requires repo-memory or release-note updates.
