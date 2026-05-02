# Migration From Reference

The legacy `/Volumes/devssd/orchestrator` checkout is reference-only.

Filesystem queues, SQLite state, JSONL event logs, and ad hoc lock files are not
ported. Postgres-backed pgloom primitives replace those layers.
