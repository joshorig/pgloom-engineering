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
- BRAID templates and graph runner
- Git and GitHub integrations
- Telegram command handling
- engineering dashboard collectors
- PPD reports
