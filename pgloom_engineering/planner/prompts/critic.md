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
Plans must include an engineering.qa.verify slice after reviewers.

### check_qa_paths_disjoint
QA author/verify slices must write only tests or fixtures and stay disjoint from implementers.

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

### check_small_feature_compactness
Small or single-surface roadmap items should use a compact handoff with limited slices.
