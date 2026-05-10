from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from pgloom_engineering.command_center.events import NOTIFY_CHANNEL

RESYNC_EVENT = {"kind": "resync", "reason": "websocket queue overflow"}


@dataclass
class WebSocketHub:
    max_queue_size: int = 200
    queues: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_queue_size)
        self.queues.add(queue)
        try:
            yield queue
        finally:
            self.queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self.queues):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(RESYNC_EVENT)
                continue
            queue.put_nowait(event)


class ListenNotifyBridge:
    def __init__(self, database_url: str, hub: WebSocketHub) -> None:
        self.database_url = database_url
        self.hub = hub
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="command-center-listen")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._conn = await asyncpg.connect(self.database_url)
                await self._conn.add_listener(NOTIFY_CHANNEL, self._on_notify)
                await self._stopping.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.hub.publish({"kind": "resync", "reason": "listen reconnect"})
                await asyncio.sleep(1.0)
            finally:
                if self._conn is not None:
                    with contextlib.suppress(Exception):
                        await self._conn.remove_listener(NOTIFY_CHANNEL, self._on_notify)
                        await self._conn.close()
                    self._conn = None

    def _on_notify(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        del connection, pid, channel
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            event = {"kind": "resync", "reason": "invalid notify payload"}
        self.hub.publish(event)


async def websocket_loop(websocket: WebSocket, hub: WebSocketHub) -> None:
    await websocket.accept()
    try:
        async with hub.subscribe() as queue:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
