# Roles

Planned task handlers:

- `engineering.plan`
- `engineering.implement`
- `engineering.review`
- `engineering.qa.author`
- `engineering.qa.verify`
- `engineering.historian`

Each handler will implement pgloom's handler contract and keep engineering
policy outside the core runtime.

`engineering.qa.author` is partially implemented. It writes failing tests in a
feature worktree, validates required project gates, runs deterministic semantic
checks over changed tests and benchmarks, and emits `QAAuthorContract`.

`engineering.qa.verify` is still pending. It must consume reviewer output,
preserve or strengthen QA coverage, run the project smoke/regression/full-app
gates, and write the final sign-off row that gates PR finalization.
