from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request, WebSocket, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


def is_loopback_host(host: str | None) -> bool:
    return host in LOOPBACK_HOSTS


def assert_loopback_bind(host: str) -> None:
    if not is_loopback_host(host):
        raise ValueError("Command Center local-only mode must bind to 127.0.0.1, ::1, or localhost")


def assert_loopback_peer(host: str | None) -> None:
    if not is_loopback_host(host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Command Center local-only mode accepts loopback peers only",
        )


def host_name(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    return value.split(":", 1)[0]


def is_allowed_host(host_header: str | None, allowed_hosts: set[str] | None = None) -> bool:
    host = host_name(host_header)
    return bool(host and (host in LOOPBACK_HOSTS or host in (allowed_hosts or set())))


def is_allowed_origin(
    origin: str | None,
    *,
    allowed_hosts: set[str] | None = None,
    dev_mode: bool = False,
) -> bool:
    if not origin:
        return False
    if dev_mode and origin in DEV_ORIGINS:
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    return is_allowed_host(parsed.netloc, allowed_hosts)


def assert_allowed_host(host_header: str | None, allowed_hosts: set[str] | None = None) -> None:
    if is_allowed_host(host_header, allowed_hosts):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Command Center rejected the Host header",
    )


async def assert_loopback_websocket(websocket: WebSocket) -> None:
    host = websocket.client.host if websocket.client else None
    if is_loopback_host(host):
        return
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Command Center local-only mode accepts loopback peers only",
    )


async def assert_allowed_websocket(
    websocket: WebSocket,
    *,
    allowed_hosts: set[str] | None = None,
    dev_mode: bool = False,
) -> None:
    if not is_allowed_host(websocket.headers.get("host"), allowed_hosts):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Command Center rejected the Host header",
        )
    if not is_allowed_origin(
        websocket.headers.get("origin"),
        allowed_hosts=allowed_hosts,
        dev_mode=dev_mode,
    ):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Command Center rejected the WebSocket Origin",
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


class HostAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        allowed_hosts: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.allowed_hosts = allowed_hosts or set()

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not is_allowed_host(request.headers.get("host"), self.allowed_hosts):
            return JSONResponse(
                {"detail": "Command Center rejected the Host header"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return await call_next(request)
