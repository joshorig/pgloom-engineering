from __future__ import annotations

import re
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from pgloom.context import count_tokens
from pydantic import BaseModel

from pgloom_engineering.path_policy import discover_qa_write_paths
from pgloom_engineering.planner import ProjectContext


class TokenSaviorContextResult(BaseModel):
    context: ProjectContext
    input_tokens_original: int
    input_tokens_after_savior: int
    tokens_saved: int
    reduction_ratio: float
    method: str
    packed_context: str


def build_token_savior_project_context(
    *,
    project_root: Path,
    query: str,
    budget_tokens: int = 3500,
    memory_digest: str = "",
) -> TokenSaviorContextResult:
    original = _raw_context(project_root, query=query, memory_digest=memory_digest)
    packed = _try_token_savior_pack(
        project_root,
        query=query,
        budget_tokens=budget_tokens,
        memory_digest=memory_digest,
    )
    if not packed:
        packed = _try_token_savior_uv_pack(
            project_root,
            query=query,
            budget_tokens=budget_tokens,
            memory_digest=memory_digest,
        )
    method = "token_savior_pack_context" if packed else "deterministic_excerpt"
    if not packed:
        packed = _deterministic_pack(project_root, query=query, memory_digest=memory_digest)
    after = _approx_tokens(packed)
    before = _approx_tokens(original)
    tokens_saved = max(0, before - after)
    relevant_paths = _candidate_relevant_paths(project_root, query)
    qa_write_paths = _filter_relevant_qa_write_paths(
        discover_qa_write_paths(project_root),
        query=query,
        relevant_paths=relevant_paths,
    )
    return TokenSaviorContextResult(
        context=ProjectContext(
            project_root=project_root,
            roadmap_excerpt=packed,
            decisions_excerpt=(
                "See roadmap_excerpt: token-savior packed context includes decisions."
            ),
            qa_smoke_path=project_root / "qa/smoke.sh",
            qa_regression_path=project_root / "qa/regression.sh",
            relevant_paths=list(dict.fromkeys([*relevant_paths, *qa_write_paths])),
            qa_write_paths=qa_write_paths,
        ),
        input_tokens_original=before,
        input_tokens_after_savior=after,
        tokens_saved=tokens_saved,
        reduction_ratio=tokens_saved / before if before else 0.0,
        method=method,
        packed_context=packed,
    )


def _try_token_savior_pack(
    project_root: Path,
    *,
    query: str,
    budget_tokens: int,
    memory_digest: str,
) -> str | None:
    token_savior_src = Path("/Volumes/devssd/repos/oss/token-savior/src")
    if token_savior_src.exists():
        sys.path.insert(0, str(token_savior_src))
    try:
        project_indexer = import_module("token_savior.project_indexer")
        query_api = import_module("token_savior.query_api")
    except Exception:
        return None
    try:
        indexer_cls = project_indexer.ProjectIndexer
        create_project_query_functions = query_api.create_project_query_functions
        index = indexer_cls(
            str(project_root),
            include_patterns=_include_patterns(),
            max_files=400,
        ).index()
        q = create_project_query_functions(index)
        search_terms = _query_terms(query)
        sections = [
            "# Token Savior Project Summary",
            str(q["get_project_summary"]()),
            "# Packed Context",
            str(q["pack_context"](query, budget_tokens=budget_tokens, max_symbols=24)),
            "# Relevant Search Hits",
            "\n\n".join(
                f"## {term}\n{_stringify(q['search_codebase'](term, max_results=12))}"
                for term in search_terms[:6]
            ),
            "# ROADMAP relevant entry",
            _extract_relevant_roadmap(project_root / "repo-memory/ROADMAP.md", query),
            "# DECISIONS relevant excerpt",
            _extract_relevant_decisions(project_root / "repo-memory/DECISIONS.md", query),
            "# Memory digest",
            memory_digest,
        ]
        return "\n\n".join(sections)
    except Exception:
        return None


def _try_token_savior_uv_pack(
    project_root: Path,
    *,
    query: str,
    budget_tokens: int,
    memory_digest: str,
) -> str | None:
    token_savior_project = Path("/Volumes/devssd/repos/oss/token-savior")
    if not token_savior_project.exists():
        return None
    search_terms = _query_terms(query)[:6]
    code = f"""
from token_savior.project_indexer import ProjectIndexer
from token_savior.query_api import create_project_query_functions
idx = ProjectIndexer(
    {str(project_root)!r},
    include_patterns={_include_patterns()!r},
    max_files=500,
).index()
q = create_project_query_functions(idx)
print('# Token Savior Project Summary')
print(q['get_project_summary']())
print('\\n# Token Savior Packed Context')
print(q['pack_context']({query!r}, budget_tokens={budget_tokens}, max_symbols=24))
for term in {search_terms!r}:
    print(f'\\n# Search Hits: {{term}}')
    print(q['search_codebase'](term, max_results=12))
"""
    try:
        completed = subprocess.run(
            ["uv", "run", "--project", str(token_savior_project), "python", "-c", code],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    sections = [
        completed.stdout,
        "# ROADMAP relevant entry",
        _extract_relevant_roadmap(project_root / "repo-memory/ROADMAP.md", query),
        "# DECISIONS relevant excerpt",
        _extract_relevant_decisions(project_root / "repo-memory/DECISIONS.md", query),
        "# Memory digest",
        memory_digest,
    ]
    return "\n\n".join(sections)


def _deterministic_pack(project_root: Path, *, query: str, memory_digest: str) -> str:
    terms = _query_terms(query)
    sections = [
        "# ROADMAP relevant entries",
        _grep_lines(
            project_root / "repo-memory/ROADMAP.md",
            terms,
        ),
        "# DECISIONS relevant excerpt",
        _extract_relevant_decisions(project_root / "repo-memory/DECISIONS.md", query),
        "# CURRENT_STATE guaranteed messaging",
        _grep_lines(
            project_root / "repo-memory/CURRENT_STATE.md",
            terms,
        ),
        "# Relevant files",
        "\n".join(_candidate_relevant_paths(project_root, query)),
        "# Memory digest",
        memory_digest,
    ]
    return "\n\n".join(sections)


def _raw_context(project_root: Path, *, query: str, memory_digest: str) -> str:
    files = [
        "repo-memory/ROADMAP.md",
        "repo-memory/DECISIONS.md",
        "repo-memory/CURRENT_STATE.md",
        "qa/smoke.sh",
        "qa/regression.sh",
        *list(_candidate_relevant_paths(project_root, query)[:8]),
    ]
    parts = []
    for rel in files:
        path = project_root / rel
        if path.is_file():
            parts.append(f"# {rel}\n{path.read_text(encoding='utf-8', errors='replace')}")
    if memory_digest:
        parts.append(f"# Memory digest\n{memory_digest}")
    return "\n\n".join(parts)


def _extract_relevant_roadmap(path: Path, query: str) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    roadmap_id = _roadmap_id_for_query(query)
    if roadmap_id is None:
        return _grep_lines(path, query.split())
    marker = f"### [{roadmap_id}]"
    start = text.find(marker)
    next_start = text.find("### [R-", start + len(marker))
    if start < 0:
        return _grep_lines(path, query.split())
    if next_start <= start:
        next_start = start + 2500
    return text[start:next_start].strip()


def _roadmap_id_for_query(query: str) -> str | None:
    lowered = query.lower()
    if "r-002" in lowered or "snapshot" in lowered or "restore" in lowered:
        return "R-002"
    if "r-003" in lowered or "range" in lowered or "visitor" in lowered:
        return "R-003"
    if "r-004" in lowered or "compression" in lowered or "lz4" in lowered:
        return "R-004"
    if "r-005" in lowered or "sbe" in lowered or "schema" in lowered:
        return "R-005"
    if "r-006" in lowered or "replication" in lowered or "standby" in lowered:
        return "R-006"
    return None


def _extract_relevant_decisions(path: Path, query: str) -> str:
    lowered = query.lower()
    terms = _query_terms(query)
    if any(item in lowered for item in ["snapshot", "restore", "journal"]):
        terms = list(dict.fromkeys([*terms, "publishChecked", "staged", "journal", "aborted"]))
    return _grep_lines(path, terms)


def _grep_lines(path: Path, terms: list[str]) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    lowered_terms = [term.lower() for term in terms]
    kept = [line for line in lines if any(term in line.lower() for term in lowered_terms)]
    return "\n".join(kept[:80])


def _include_patterns() -> list[str]:
    return [
        "**/*.java",
        "**/*.kt",
        "**/*.ts",
        "**/*.tsx",
        "**/*.py",
        "**/*.sh",
        "**/*.gradle",
        "**/*.md",
        "**/*.yaml",
        "**/*.yml",
        "**/*.sql",
        "build.gradle",
        "settings.gradle",
        "gradle.properties",
    ]


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", query):
        lowered = token.lower()
        if lowered in {
            "the",
            "and",
            "for",
            "with",
            "must",
            "should",
            "feature",
            "implement",
            "project",
            "acceptance",
        }:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))[:12] or ["roadmap", "decision", "test"]


def _candidate_relevant_paths(project_root: Path, query: str) -> list[str]:
    terms = [term.lower() for term in _query_terms(query)]
    candidates: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project_root).as_posix()
        if any(part in rel for part in ["/.git/", "node_modules/", ".venv/", "build/"]):
            continue
        lowered = rel.lower()
        if any(term in lowered for term in terms) or lowered in {
            "qa/smoke.sh",
            "qa/regression.sh",
            "repo-memory/roadmap.md",
            "repo-memory/decisions.md",
        }:
            candidates.append(rel)
        if len(candidates) >= 24:
            break
    return list(dict.fromkeys([*candidates, *_known_existing_paths(project_root, query)]))[:36]


def _known_existing_paths(project_root: Path, query: str) -> list[str]:
    lowered = query.lower()
    candidates: list[str] = []
    for rel in [
        "benchmarks/src/test/",
        "benchmarks/src/test/java/",
        "runtime-core/src/main/",
        "runtime-core/src/test/",
        "dag-framework-api/src/main/",
        "dag-framework-api/src/test/",
        "platform-dsl/src/main/",
        "platform-dsl/src/test/",
        "app-api/src/main/",
        "app-api/src/test/",
        "ui/src/",
        "ui/tests/",
    ]:
        if (project_root / rel).exists() and (
            rel.split("/", 1)[0].replace("-", " ") in lowered
            or any(term in lowered for term in ["benchmark", "jmh", "api", "ui", "dsl"])
        ):
            candidates.append(rel)
    return candidates


def _filter_relevant_qa_write_paths(
    paths: list[str],
    *,
    query: str,
    relevant_paths: list[str],
) -> list[str]:
    always = [path for path in paths if path in {"tests/", "qa/fixtures/"}]
    modules = {
        path.split("/", 1)[0]
        for path in relevant_paths
        if "/" in path and not path.startswith(("tests/", "qa/"))
    }
    lowered_query = query.lower()
    if "ui" in lowered_query or "playwright" in lowered_query:
        modules.add("ui")
    dag_query = any(
        term in lowered_query
        for term in [
            "backpressure",
            "overflow",
            "spill",
            "graphpartitionrunner",
            "jmh",
        ]
    )
    if not dag_query and any(
        term in lowered_query for term in ["api", "config", "diagnostic", "controller"]
    ):
        modules.add("app-api")
    if any(term in lowered_query for term in ["signalspec", "signal spec", "backtest"]):
        modules.update({"platform-dsl", "app-api", "app-core", "ui"})
    if any(term in lowered_query for term in ["backpressure", "overflow", "spill"]):
        modules.update({"dag-framework-api", "runtime-core", "dag-framework-lvc", "benchmarks"})
    if any(term in lowered_query for term in ["window", "watermark", "aggregate", "jmh"]):
        modules.update({"runtime-core", "dag-framework-api", "benchmarks"})
    dag_modules = {"dag-framework-api", "dag-framework-lvc", "runtime-core", "benchmarks"}
    if "app-api" in modules and not modules.intersection(dag_modules) and any(
        term in lowered_query for term in ["config", "diagnostic", "controller"]
    ):
        modules = {"app-api", *(["ui"] if "ui" in modules else [])}
    filtered = [
        path
        for path in paths
        if path in always or any(path.startswith(f"{module}/") for module in modules)
    ]
    if "ui/tests/" in filtered:
        filtered = [path for path in filtered if path != "ui/src/test/"]
    return list(dict.fromkeys(filtered or paths[:8]))[:10]


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


def _approx_tokens(text: str) -> int:
    return count_tokens(text)
