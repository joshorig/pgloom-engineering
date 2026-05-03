# Cross-Project Planning Registration

Registered locally for planning-only validation:

| Project | Root | Smoke | Regression |
| --- | --- | --- | --- |
| `trade-research-platform` | `/Volumes/devssd/repos/apps/trade-research-platform` | `./qa/smoke.sh` | `./qa/regression.sh` |
| `dag-framework` | `/Volumes/devssd/repos/ull/dag_framework` | `./qa/smoke.sh` | `./qa/regression.sh` |

Both registrations set `metadata.planning_only = true` and include project-specific
`relevant_paths` for planner context.

## Token Savior Smoke Results

### trade-research-platform

Query: R-003 crypto-domain config + diagnostics parity.

- Method: `token_savior_pack_context`
- Original tokens: 16,823
- Packed tokens: 2,333
- Saved: 14,490
- Reduction: 86.1%

### dag-framework

Query: R-001 WindowedAggregateNode with tumbling/sliding windows, watermark, late arrival, and
zero-allocation aggregation.

- Method: `token_savior_pack_context`
- Original tokens: 5,153
- Packed tokens: 2,329
- Saved: 2,824
- Reduction: 54.8%

## Notes

The Token Savior include patterns were generalized from the original LVC-specific Java/store
patterns to cover Java, Kotlin, TypeScript, Python, shell, Gradle, Markdown, YAML, and SQL files.
This is required for mixed backend/frontend repositories like `trade-research-platform`.
