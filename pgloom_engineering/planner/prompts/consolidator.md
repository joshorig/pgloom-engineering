Merge the candidate PlanContract summaries into one complete PlanContract.

Prefer slices that multiple panelists agree on. Preserve concrete paths,
verification commands, lifecycle acceptance coverage, and human finalization.
Emit exactly one JSON object matching PlanContract.

Rules:
- Treat DETERMINISTIC_PLAN_SKELETON as the canonical local expansion baseline.
  Consolidate candidates back toward its order, role/task_type mapping, and
  target slice count unless the feature evidence justifies extra implementer
  slices.
- Valid roles are only `designer`, `implementer`, `reviewer`, `qa`, and
  `historian`. Convert candidate terms like `worker` or `developer` to
  `implementer`.
- Use only canonical role/task_type pairs: designer/engineering.design,
  implementer/engineering.implement, reviewer/engineering.review,
  qa/engineering.qa.author, qa/engineering.qa.verify, and
  historian/engineering.history.
- `affected_surfaces` must be non-empty and should include every repo area the
  final plan will touch.
- Use concrete path prefixes only; remove wildcard paths from candidates.
- Prefer paths present in candidate context/summaries. Remove invented path
  prefixes when an equivalent existing path is available.
- Ensure each slice's own allowed_paths and forbidden_paths do not overlap.
- Preserve reviewer, `engineering.qa.author`, and `engineering.qa.verify`
  slices. If candidates use generic QA slices, split them into test-first
  authoring before implementers and verification after reviewers.
- QA author/verify write paths must be restricted to the QA/test roots shown in
  candidate project contexts. Implementer write paths must not include those
  paths.
- Apply compactness pressure for small/single-surface roadmap items: prefer 4-6
  slices total: design, qa.author, 1-2 implementers, reviewer, qa.verify. Merge
  redundant QA/review work.
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
- Dependency IDs must refer only to earlier slices.
- Use `open_final_feature_pr_for_human_merge` for finalization_policy.
- The input is summarized to save tokens; do not treat omitted raw JSON as omitted
  requirements. Reconstruct a complete valid PlanContract from the summaries.
- Deterministic validator and critic checks are authoritative. If candidates
  disagree with the deterministic rules, follow the deterministic rules.
