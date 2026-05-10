Review the PlanContract against every rubric check.

Emit exactly one JSON object with:
- rationale: string
- per_check_results: list of CriticCheckResult objects

Do not decide the final verdict. The runtime computes accept/revise/reject.
Return exactly one result for every check_id below. Do not omit any check.

### check_design_contract_completeness
Lifecycle work must define persistence and concurrency protocols.

### check_slice_path_coverage
Every affected surface must be covered by slice allowed paths and tests.

### check_forbidden_path_overlap
No task slice may include overlapping entries in its own allowed_paths and forbidden_paths.
Cross-role boundaries are allowed: for example, a QA slice can forbid source paths
that an implementer slice allows.

### check_verification_commands
Implementer and QA slices must define qa or Gradle verification commands.

### check_lifecycle_coverage
Lifecycle work must cover stale/invalid, invariant, and failure/partial cases.

### check_topology_consistency
SINGLE topology is incompatible with multiple implementer slices.

### check_reviewer_present
Plans must include at least one reviewer slice.

### check_qa_author_present
Plans must include a test-first engineering.qa.author slice before implementers.

### check_qa_verify_present
Plans must include engineering.qa.verify.scrutiny after reviewers and
engineering.qa.verify.usertest after scrutiny unless metadata authorizes
user-test skip.

### check_qa_paths_disjoint
QA author/scrutiny/user-test slices must write only tests or fixtures and stay disjoint from implementers.

### check_acceptance_assertion_coverage
Every acceptance assertion must be claimed by at least one task slice, and every
task slice must claim at least one acceptance assertion.

### check_milestones_present
Plans must include milestone contracts with validation contracts and signoff policy.
Milestone signoff is executable, not descriptive: downstream milestone slices are
locked until prerequisite milestones are signed off. A milestone using
`scrutiny_and_usertest` must include both split validator slices in that same
milestone; otherwise the gate is impossible to satisfy.

### check_orphan_slices
Non-terminal slices should feed a later reviewer, QA, or historian slice.

### check_finalization_policy
Final feature PR merge must remain human-gated.

### check_objective_specificity
Slice objectives should name concrete artifacts, files, tests, or metrics.

### check_risk_register_present
Lifecycle work should carry an explicit risk register.

### check_roadmap_dependency_handling
Dependency-gated roadmap items must block, narrow, or explicitly sequence prerequisites.

### check_hot_path_invariants
Plans must not schedule work that violates stated zero-allocation or hot-path constraints.
For benchmark-backed allocation requirements, the benchmark must be runnable by
the project's feature smoke gate and must avoid allocating benchmark fixtures,
reflection proxies, boxed callbacks, or target reset work inside the measured
operation. Full regression/JMH sweeps are periodic project gates, not
per-feature QA scrutiny blockers.

### check_behavioral_coverage_not_inventory_only
Endpoint, route, prefix, filter, query, and benchmark acceptance must be proven
through behavior cases. A plan that only checks method/route/build-file presence
without exercising matching and non-matching behavior is insufficient.

### check_small_feature_compactness
Small or single-surface roadmap items should use a compact handoff with limited slices.
