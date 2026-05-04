from __future__ import annotations

from pgloom.harness.registry import HandlerRegistry

from pgloom_engineering.roles.historian import HistorianHandler
from pgloom_engineering.roles.implementer import ImplementerHandler
from pgloom_engineering.roles.planner import PlannerHandler
from pgloom_engineering.roles.qa import QAHandler
from pgloom_engineering.roles.reviewer import ReviewerHandler


def build_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register("engineering.plan", PlannerHandler())
    registry.register("engineering.implement", ImplementerHandler())
    registry.register("engineering.review", ReviewerHandler())
    registry.register("engineering.qa.author", QAHandler())
    registry.register("engineering.qa.verify", QAHandler())
    registry.register("engineering.historian", HistorianHandler())
    return registry
