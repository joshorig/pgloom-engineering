from __future__ import annotations

import re
from typing import Any, Literal

ContextLens = Literal["architecture", "qa", "risk"]


def lens_for_panelist(panelist_index: int) -> ContextLens:
    return ("architecture", "qa", "risk")[panelist_index % 3]


def apply_context_lens(context: Any, lens: ContextLens) -> Any:
    focus = _focus_terms(lens)
    roadmap = _lens_excerpt(context.roadmap_excerpt, focus)
    decisions = _lens_excerpt(context.decisions_excerpt, focus)
    relevant_paths = _lens_paths(context.relevant_paths, lens)
    return context.model_copy(
        update={
            "context_lens": lens,
            "lens_focus": list(focus),
            "roadmap_excerpt": roadmap or context.roadmap_excerpt,
            "decisions_excerpt": decisions or context.decisions_excerpt,
            "relevant_paths": relevant_paths or context.relevant_paths,
        }
    )


def _focus_terms(lens: ContextLens) -> tuple[str, ...]:
    if lens == "qa":
        return (
            "test",
            "qa",
            "smoke",
            "regression",
            "acceptance",
            "failure",
            "fixture",
            "verify",
        )
    if lens == "risk":
        return (
            "risk",
            "constraint",
            "decision",
            "memory",
            "failure",
            "stale",
            "security",
            "concurrency",
            "migration",
            "persistence",
        )
    return (
        "api",
        "architecture",
        "symbol",
        "class",
        "module",
        "surface",
        "implementation",
        "file",
    )


def _lens_excerpt(text: str, terms: tuple[str, ...], *, max_lines: int = 90) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    lowered_terms = tuple(term.lower() for term in terms)
    kept: list[str] = []
    last_heading = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            last_heading = stripped
            continue
        if any(term in stripped.lower() for term in lowered_terms):
            if last_heading and (not kept or kept[-1] != last_heading):
                kept.append(last_heading)
            kept.append(line)
        if len(kept) >= max_lines:
            break
    return "\n".join(_dedupe_adjacent(kept))


def _lens_paths(paths: list[str], lens: ContextLens) -> list[str]:
    if lens == "qa":
        preferred = [
            path
            for path in paths
            if _matches(path, r"(^tests/|^qa/|src/test|ui/tests|test|fixture)")
        ]
    elif lens == "risk":
        preferred = [
            path
            for path in paths
            if _matches(path, r"(migration|schema|security|store|persistence|journal|state)")
        ]
    else:
        preferred = [
            path
            for path in paths
            if not _matches(path, r"(^tests/|^qa/fixtures/|fixture)")
        ]
    remainder = [path for path in paths if path not in preferred]
    return [*preferred, *remainder[:6]][:12]


def _matches(value: str, pattern: str) -> bool:
    return re.search(pattern, value, flags=re.IGNORECASE) is not None


def _dedupe_adjacent(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if result and result[-1] == line:
            continue
        result.append(line)
    return result
