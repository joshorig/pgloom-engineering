# Command Center

Command Center is a local operator UI served by the FastAPI backend and the
bundled React build. The service defaults to a non-loopback bind so it can be
opened from the Codex in-app browser, but browser-facing requests are still
guarded at the HTTP boundary.

## Browser Boundary

The backend rejects requests whose `Host` header is not `localhost`,
`127.0.0.1`, or an explicit value from `CC_ALLOWED_HOSTS`.

For LAN testing, set the concrete host you use in the browser:

```sh
CC_ALLOWED_HOSTS=192.168.50.8 pgloom-engineering command-center
```

WebSocket upgrades also require an allowed `Origin`. This prevents an arbitrary
site opened in the operator's browser from using the local Command Center as a
loopback peer.

## Dev Mode

The production/default app is same-origin and does not enable permissive CORS.
When running the Vite development server, opt in explicitly:

```sh
CC_DEV_MODE=1 pgloom-engineering command-center
```

Dev mode allows `http://localhost:5173` and `http://127.0.0.1:5173` for local
frontend iteration only.
