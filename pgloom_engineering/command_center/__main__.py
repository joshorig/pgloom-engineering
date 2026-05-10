from __future__ import annotations

import os

import uvicorn

from pgloom_engineering.command_center.app import CommandCenterSettings


def main() -> None:
    settings = CommandCenterSettings()
    port = int(os.environ.get("PGLOOM_COMMAND_CENTER_PORT", settings.port))
    uvicorn.run(
        "pgloom_engineering.command_center.app:create_app",
        factory=True,
        host=settings.host,
        port=port,
    )


if __name__ == "__main__":
    main()
