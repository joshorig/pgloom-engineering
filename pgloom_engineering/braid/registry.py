from __future__ import annotations


class BraidTemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, str] = {}

    def register(self, name: str, template: str) -> None:
        self._templates[name] = template

    def get(self, name: str) -> str | None:
        return self._templates.get(name)
