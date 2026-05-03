from __future__ import annotations

from pathlib import Path

DEFAULT_QA_WRITE_PATHS = ["tests/", "qa/fixtures/"]


def normalize_path_prefix(path: str) -> str:
    stripped = path.strip()
    if stripped in {"", "."}:
        return ""
    return stripped.rstrip("/") + "/"


def is_qa_write_path(path: str, qa_write_paths: list[str] | None = None) -> bool:
    normalized = normalize_path_prefix(path)
    prefixes = [normalize_path_prefix(item) for item in (qa_write_paths or DEFAULT_QA_WRITE_PATHS)]
    if any(normalized.startswith(prefix) for prefix in prefixes if prefix):
        return True
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 3 and parts[-2:] == ["src", "test"]:
        return True
    if len(parts) >= 3 and parts[-3:] == ["src", "test", "java"]:
        return True
    if normalized.endswith("/src/test/") or "/src/test/" in normalized:
        return True
    if normalized.startswith("ui/tests/") or "/ui/tests/" in normalized:
        return True
    return False


def discover_qa_write_paths(project_root: Path) -> list[str]:
    discovered = list(DEFAULT_QA_WRITE_PATHS)
    candidates = [
        path
        for path in project_root.rglob("*")
        if path.is_dir()
        and (
            path.match("*/src/test")
            or path.match("*/src/test/java")
            or path.match("*/ui/tests")
            or path.match("ui/tests")
        )
    ]
    for path in sorted(candidates):
        rel = path.relative_to(project_root).as_posix().rstrip("/") + "/"
        if rel not in discovered:
            discovered.append(rel)
    return discovered[:80]
