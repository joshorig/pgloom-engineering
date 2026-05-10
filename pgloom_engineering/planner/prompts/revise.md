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
- Small or single-surface items should stay at 5-7 slices unless there is a
  clear multi-surface risk. If a code-heavy hot-path feature was collapsed into
  one broad implementer slice, split it into 2-4 smaller implementer slices by
  API/core and backend/variant surfaces instead of preserving the broad slice.
- If a prior validator error says `variant_slice_uses_broad_conformance_gate`,
  fix it directly: either merge the variant-scoped implementer slices into one
  broader implementer slice, or keep the split only when each variant slice has
  a concrete slice-specific Gradle `--tests Class.method` or class filter that
  the QA-author slice is instructed to create. Do not keep the same broad
  conformance class command on multiple variant slices.
- Remove wildcard paths and same-slice allowed/forbidden overlap.
