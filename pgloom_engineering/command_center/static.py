from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code != 404:
            return response
        return await super().get_response("index.html", scope)
