from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_loopback_host(host: str | None) -> bool:
    return host in LOOPBACK_HOSTS


def assert_loopback_bind(host: str) -> None:
    if not is_loopback_host(host):
        raise ValueError("Command Center v1 must bind to 127.0.0.1, ::1, or localhost")


def assert_loopback_peer(host: str | None) -> None:
    if not is_loopback_host(host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Command Center v1 accepts loopback peers only",
        )


async def assert_loopback_websocket(websocket: WebSocket) -> None:
    host = websocket.client.host if websocket.client else None
    if is_loopback_host(host):
        return
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Command Center v1 accepts loopback peers only",
    )


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        host = request.client.host if request.client else None
        assert_loopback_peer(host)
        return await call_next(request)
