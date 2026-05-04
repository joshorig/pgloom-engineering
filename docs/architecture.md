# Architecture

`pgloom-engineering` is a domain layer over `pgloom`.

`pgloom` owns durable workflow primitives:

- workflows
- tasks
- slots
- leases
- approvals
- model usage
- artifacts
- notifications
- memory
- dashboard collector registration

`pgloom-engineering` owns engineering behavior:

- feature aggregation
- role handlers
- typed plan, task, QA, review, and recovery contracts
- planner council and rubric-style critics
- QA author semantic checks and project-gate validation
- Git and GitHub integrations
- Telegram command handling
- engineering dashboard collectors
- PPD reports

BRAID is parked. The runtime primitive for autonomous review is typed contracts
plus Python rubric runners, deterministic gates, and recorded recovery actions.
BRAID can be revisited only if human-authored graph templates or lint-time
workflow-shape guarantees become a concrete need.
