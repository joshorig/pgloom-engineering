from __future__ import annotations

from pgloom_engineering.handlers.registry import build_registry


def test_registry_builds() -> None:
    registry = build_registry()
    assert registry.get("engineering.plan") is not None
    assert registry.get("engineering.implement") is not None
    assert registry.get("engineering.qa.author") is not None
    assert registry.get("engineering.qa.verify") is not None
