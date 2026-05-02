from __future__ import annotations


def classify_self_repair_issue(code: str) -> str:
    if code.startswith("engineering.qa"):
        return "qa"
    if code.startswith("engineering.review"):
        return "review"
    return "general"
