Revise the prior PlanContract to address validator errors and blocking critic findings.

Emit exactly one JSON object matching PlanContract. Preserve accepted slices unless a
finding requires changing them.

Deterministic validator and critic checks are authoritative. In particular:
- QA author and QA verify allowed_paths must be limited to the project's
  registered QA/test roots.
- Implementers must not include project QA/test roots in allowed_paths.
- QA verify must run after every reviewer.
- Do not add finalization, merge, PR approval, or human-gate task slices; use
  only finalization_policy for the human merge gate.
- Small or single-surface items should stay at 4-6 slices unless there is a
  clear multi-surface risk.
- Remove wildcard paths and same-slice allowed/forbidden overlap.
