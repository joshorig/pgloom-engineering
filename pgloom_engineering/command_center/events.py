from __future__ import annotations

from typing import Literal, TypedDict

NOTIFY_CHANNEL = "cc_events"
MAX_NOTIFY_PAYLOAD_BYTES = 7_500

EventKind = Literal[
    "feature.update",
    "worker_run.update",
    "handoff.update",
    "qa.signoff",
    "intervention.added",
    "recovery.update",
    "plan.update",
    "task.update",
    "resync",
]


class CommandCenterEvent(TypedDict, total=False):
    v: int
    kind: EventKind | str
    feature_id: str | None
    row_id: int | str | None
    fields: list[str]
    ts: str
    reason: str
