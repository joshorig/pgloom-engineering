Merge the candidate PlanContract summaries into one complete PlanContract.

Prefer slices that multiple panelists agree on. Preserve concrete paths,
verification commands, lifecycle acceptance coverage, and human finalization.
Emit exactly one JSON object matching PlanContract.

Rules:
- Treat DETERMINISTIC_PLAN_SKELETON as the canonical local expansion baseline.
  Consolidate candidates back toward its order, role/task_type mapping, and
  target slice count unless the feature evidence justifies extra implementer
  slices.
- If INHERIT_BASELINE_MODE.enabled is true, this is an operator
  replan-from-milestone request. Preserve every baseline frozen-prefix task
  slice exactly, with unchanged JSON values for those slice objects. Consolidate
  only the requested milestone and downstream replacement work.
- Valid roles are only `designer`, `implementer`, `reviewer`, `qa`, and
  `historian`. Convert candidate terms like `worker` or `developer` to
  `implementer`.
- Use only canonical role/task_type pairs: designer/engineering.design,
  implementer/engineering.implement, reviewer/engineering.review,
  qa/engineering.qa.author, qa/engineering.qa.verify.scrutiny,
  qa/engineering.qa.verify.usertest, and
  historian/engineering.history.
- `affected_surfaces` must be non-empty and should include every repo area the
  final plan will touch.
- Use concrete path prefixes only; remove wildcard paths from candidates.
- Prefer paths present in candidate context/summaries. Remove invented path
  prefixes when an equivalent existing path is available.
- Ensure each slice's own allowed_paths and forbidden_paths do not overlap.
- Preserve reviewer, `engineering.qa.author`, `engineering.qa.verify.scrutiny`,
  and `engineering.qa.verify.usertest` slices. If candidates use generic QA
  slices, split them into test-first authoring before implementers, scrutiny
  after reviewers, and user-test after scrutiny unless metadata authorizes skip.
- Keep deterministic lint/build/test/smoke/benchmark commands in
  `engineering.qa.verify.scrutiny`. Do not put Gradle/test/check/JMH/smoke
  commands in `engineering.qa.verify.usertest.verification_commands`; user-test
  may name only a launch/setup harness or interaction entrypoint and must rely on
  the validator model to exercise the public feature surface and record replay
  evidence.
- Preserve QA policy guidance from candidate contexts and summaries: endpoint
  harness requirements, structured payload assertions, benchmark variants,
  required gates, behavior coverage rules, and avoid patterns must survive in QA
  author objectives/outputs when relevant.
- When benchmark acceptance is tied to a smoke/benchmark-smoke gate, preserve
  metadata-declared benchmark roots and test_support_paths needed to wire the new
  benchmark into that gate. Do not accept a plan where benchmark evidence is added
  but the required gate cannot execute it.
- Preserve behavior coverage for filters, prefixes, routes, and queries as
  matching/non-matching test cases; method or route inventory checks alone are not
  valid acceptance coverage.
- QA author/scrutiny/user-test write paths must be restricted to the QA/test
  roots shown in candidate project contexts. Implementer write paths must not
  include those paths.
- Design slice `allowed_paths` must be documentation/design paths only. Keep
  source/test/benchmark paths in the design objective or expected outputs if
  needed for context, but do not preserve them as design write permissions.
- Implementer `allowed_paths` must not include `docs/` or `repo-memory/`.
  Documentation/status updates are design or final human-gated follow-up work,
  not implementation work.
- Apply compactness pressure for small/single-surface roadmap items: prefer 6
  slices total: design, qa.author, 1-2 implementers, reviewer, qa.scrutiny, and
  qa.usertest. For code-heavy or hot-path features that span multiple source
  backends/variants, prefer 2-4 smaller implementer slices over one broad
  implementer slice; this preserves quality and avoids excessive model context
  replay. Merge redundant QA/review work.
- For hot-path interface or shared API additions, preserve coverage for
  wrappers, decorators, metrics adapters, and other delegating implementations
  that implement the same contract when they are discoverable by symbol search.
  A plan that optimizes only concrete base implementations can still violate the
  feature if common wrappers inherit an allocating default path.
- If implementer slices are split by variant/backend such as SINGLE vs DOUBLE
  or direct vs mmap, each variant-scoped slice must use slice-specific
  verification commands. Prefer concrete Gradle `--tests Class.method` filters
  matching the QA-author objective. Do not preserve a plan where multiple
  variant slices each run the same broad all-variant conformance class command;
  either make the command method/class-specific or merge those implementer
  slices.
- Preserve separate implementer slices for wide/system features that span
  independent ownership surfaces such as DSL/compiler, API/workflow, UI, and
  lifecycle/overflow/invariants.
- Do not create finalization, merge, PR approval, or human-gate task slices.
  Human merge is represented only by finalization_policy.
- Preserve stale/invalid, invariant/CRC or equivalent invariant-failure, and
  failure/partial lifecycle coverage only when the feature is lifecycle,
  durability, recovery, or state-machine work. Do not add snapshot/CRC/journal
  acceptance entries for unrelated features.
- Every task slice needs non-empty `allowed_paths`, `forbidden_paths`,
  `expected_outputs`, and `verification_commands`.
- Implementer `allowed_paths` must be package/file-level roots, not broad module
  source roots such as `core/src/main/java/` or `store/src/main/java/`. Keep the
  write surface narrow enough that unrelated production packages remain outside
  the path policy.
- Preserve or synthesize `acceptance_assertions`, per-slice
  `acceptance_assertion_ids`, `required_procedures`, grading criteria,
  validation strategy, and milestone contracts. Every assertion must be claimed
  by at least one slice; every slice must claim at least one assertion.
- Milestones must group slice ids, carry validation contracts, and use
  `scrutiny_and_usertest` unless metadata authorizes scrutiny-only signoff.
  Treat milestone signoff as an executable dependency gate: if a downstream
  milestone depends on an earlier milestone, its slices cannot run until the
  earlier milestone's validators have completed. A `scrutiny_and_usertest`
  milestone must contain both validator slices in its own `slice_ids`; do not
  make implementation depend on a design/QA-author-only milestone that requires
  validator signoff.
- Prefer module-local commands for QA author and implementer slices. Feature
  QA scrutiny should use lint/build, feature-specific tests, and direct
  benchmark smoke commands such as `:benchmarks:jmhSmokeCheck` with the
  project-required smoke properties. Do not schedule `qa/smoke.sh`,
  `qa/regression.sh`, bare `./gradlew test/check`, or full `:benchmarks:jmh`
  sweeps as per-feature blockers; those are project-scheduled periodic or
  broad project gates unless project metadata explicitly supplies a
  feature-scoped replacement command.
- Gradle `--tests` filters are case-sensitive. Keep concrete class or
  class.method identifiers that the QA-author slice is instructed to create.
  Do not preserve variant wildcards such as `*Mmap*RangeScan*` unless the final
  QA-author objective names a matching `Mmap...RangeScan...` class or method.
- QA user-test is not another broad gate runner. It must specify a
  user-facing CLI/API/browser/app flow, or for a pure library a focused
  consumer-style command/harness using the public API. Do not put `qa/smoke.sh`,
  `qa/regression.sh`, deterministic test/check commands such as Gradle
  `:module:test --tests ...`, `:benchmarks:jmhSmokeCheck`, or full benchmark
  sweeps in user-test verification.
- Remove exploratory commands (`grep`, `cat`, `echo`), list-only commands, and
  dry-run-only commands when they are used as verification proof.
- In corrective-slice recovery, preserve existing QA-authored test class/method
  names from failure evidence and prior handoffs unless the plan also includes a
  QA-author repair slice that creates the replacement test. Do not synthesize
  generic conformance class names for implementation-only recovery.
- Dependency IDs must refer only to earlier slices.
- Use `open_final_feature_pr_for_human_merge` for finalization_policy.
- The input is summarized to save tokens; do not treat omitted raw JSON as omitted
  requirements. Reconstruct a complete valid PlanContract from the summaries.
- Deterministic validator and critic checks are authoritative. If candidates
  disagree with the deterministic rules, follow the deterministic rules.
