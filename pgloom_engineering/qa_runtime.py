from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pgloom.harness.subprocess import SubprocessResult, run_bounded

from pgloom_engineering.rtk.filter import filter_subprocess_result


@dataclass(frozen=True)
class QAVerificationResult:
    original: SubprocessResult
    stdout_excerpt: str
    stderr_excerpt: str
    infra_error: str | None


def project_qa_metadata(project_metadata: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    qa = project_metadata.get("qa")
    if isinstance(qa, dict):
        metadata.update(qa)
    qa_author = project_metadata.get("qa_author")
    if isinstance(qa_author, dict):
        metadata = _deep_merge_dicts(metadata, qa_author)
    for key in [
        "test_roots",
        "source_roots",
        "endpoint_roots",
        "browser_test_roots",
        "example_tests",
        "helper_files",
        "quality_gates",
        "avoid_patterns",
        "verification_commands",
        "env",
        "path_prepend",
        "dependency_hydration",
        "route_inventory",
        "route_coverage_requirements",
        "preferred_test_skeletons",
        "preferred_helpers",
        "behavior_coverage_rules",
        "required_gates",
        "semantic_conventions",
    ]:
        flat_key = f"qa_{key}"
        if flat_key in project_metadata and key not in metadata:
            metadata[key] = project_metadata[flat_key]
    return metadata


def prompt_safe_qa_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in [
        "test_roots",
        "source_roots",
        "endpoint_roots",
        "browser_test_roots",
        "example_tests",
        "helper_files",
        "quality_gates",
        "avoid_patterns",
        "verification_commands",
        "env",
        "path_prepend",
        "dependency_hydration",
        "route_inventory",
        "route_coverage_requirements",
        "preferred_test_skeletons",
        "preferred_helpers",
        "behavior_coverage_rules",
        "required_gates",
        "semantic_conventions",
    ]:
        value = metadata.get(key)
        if isinstance(value, dict):
            safe[key] = _prompt_safe_value(value)
        elif isinstance(value, list):
            safe[key] = [
                safe_item
                for item in value[:40]
                if (safe_item := _prompt_safe_value(item)) is not None
            ]
        elif isinstance(value, str):
            safe[key] = value
    return safe


def _prompt_safe_value(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        return [
            safe_item
            for item in value[:40]
            if (safe_item := _prompt_safe_value(item)) is not None
        ]
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for item_key, item_value in list(value.items())[:40]:
            if isinstance(item_key, str):
                safe_value = _prompt_safe_value(item_value)
                if safe_value is not None:
                    safe[item_key] = safe_value
        return safe
    return None


def validate_required_qa_gates(
    worktree: Path,
    project_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata = project_qa_metadata(project_metadata)
    raw_gates = metadata.get("required_gates")
    gates = raw_gates if isinstance(raw_gates, list) else []
    validation: list[dict[str, Any]] = []
    for raw_gate in gates:
        if not isinstance(raw_gate, dict):
            continue
        command = raw_gate.get("command")
        command_list = [str(item) for item in command] if isinstance(command, list) else []
        gate_id = str(raw_gate.get("id") or "unnamed")
        command_path = _gate_command_path(command_list)
        missing: list[str] = []
        evidence: list[str] = []
        text = ""
        if command_path is None:
            missing.append("command")
        else:
            full_path = worktree / command_path
            if not full_path.exists():
                missing.append(str(command_path))
            elif full_path.is_file():
                text = full_path.read_text(encoding="utf-8", errors="replace")
                evidence.append(f"command_file:{command_path}")
        for coverage in raw_gate.get("must_cover", []):
            coverage_id = str(coverage)
            tokens = _gate_coverage_tokens(coverage_id)
            if text and any(token in text for token in tokens):
                evidence.append(f"covers:{coverage_id}")
            else:
                missing.append(f"coverage:{coverage_id}")
        validation.append(
            {
                "gate_id": gate_id,
                "command": command_list,
                "status": "configured" if not missing else "missing",
                "evidence": evidence,
                "missing": missing,
            }
        )
    return validation


def _gate_command_path(command: list[str]) -> Path | None:
    if not command:
        return None
    first = command[0]
    if first.startswith("./"):
        return Path(first)
    if "/" in first:
        return Path(first)
    return None


def _gate_coverage_tokens(coverage_id: str) -> list[str]:
    tokens = {
        "allocation": ["gc.alloc.rate.norm", "alloc.rate", "allocation"],
        "benchmark_smoke": ["jmhSmokeCheck", "jmhSmoke"],
        "benchmark_full": [":benchmarks:jmh", "benchmark_full"],
        "unit_regression": [
            "./gradlew test",
            "gradle test",
            "mvn test",
            "pytest",
            "unit_regression",
            "regression",
        ],
    }
    return tokens.get(coverage_id, [coverage_id])


def discover_route_inventory(
    project_root: Path,
    project_metadata: dict[str, Any],
    *,
    api_prefixes: list[str] | None = None,
    limit: int = 160,
) -> list[dict[str, str]]:
    metadata = project_qa_metadata(project_metadata)
    explicit = metadata.get("route_inventory")
    if isinstance(explicit, list):
        routes = [_route_inventory_item(item) for item in explicit]
        return _filter_route_inventory(routes, api_prefixes, limit)
    roots = _metadata_roots(project_root, metadata, "endpoint_roots")
    if not roots:
        roots = _metadata_roots(project_root, metadata, "source_roots")
    if not roots:
        roots = [project_root]
    discovered: dict[tuple[str, str], dict[str, str]] = {}
    for root in roots[:20]:
        if not root.exists():
            continue
        for path in _iter_route_source_files(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            for route in _spring_annotation_routes(text):
                route["source"] = path.relative_to(project_root).as_posix()
                discovered[(route["method"], route["path"])] = route
            for route_path in _quoted_api_paths(text):
                route = {
                    "method": "ANY",
                    "path": route_path,
                    "source": path.relative_to(project_root).as_posix(),
                }
                discovered[(route["method"], route["path"])] = route
    return _filter_route_inventory(list(discovered.values()), api_prefixes, limit)


def route_inventory_for_prompt(routes: list[dict[str, str]], *, limit: int = 80) -> list[str]:
    lines: list[str] = []
    for route in routes[:limit]:
        method = route.get("method") or "ANY"
        path = route.get("path") or ""
        source = route.get("source") or ""
        if path:
            lines.append(f"{method} {path} ({source})" if source else f"{method} {path}")
    return lines


def qa_env(project_metadata: dict[str, Any], *, project_root: Path | None = None) -> dict[str, str]:
    metadata = project_qa_metadata(project_metadata)
    env: dict[str, str] = {}
    raw_env = metadata.get("env")
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if isinstance(key, str) and isinstance(value, str):
                env[key] = _expand_env_value(value, {**os.environ, **env}, project_root)
    if "JAVA_HOME" not in env:
        java_home = _discover_java_home()
        if java_home is not None:
            env["JAVA_HOME"] = str(java_home)
    path_prepend = _path_prepend(metadata, env, project_root)
    if env.get("JAVA_HOME"):
        java_bin = str(Path(env["JAVA_HOME"]) / "bin")
        if java_bin not in path_prepend:
            path_prepend.insert(0, java_bin)
    if path_prepend:
        env["PATH"] = os.pathsep.join([*path_prepend, os.environ.get("PATH", "")])
    return env


def command_with_env(argv: list[str], env: dict[str, str]) -> list[str]:
    if not env:
        return argv
    return ["env", *[f"{key}={value}" for key, value in sorted(env.items())], *argv]


def hydrate_dependencies(
    project_root: Path,
    worktree: Path,
    project_metadata: dict[str, Any],
) -> None:
    metadata = project_qa_metadata(project_metadata)
    raw = metadata.get("dependency_hydration")
    relatives = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
    if not relatives:
        relatives = ["node_modules", "ui/node_modules"]
    for relative_text in relatives:
        relative = Path(relative_text)
        source = project_root / relative
        target = worktree / relative
        if not source.exists() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=source.is_dir())


def relevant_changed_files(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if "__pycache__/" not in path
        and not path.endswith((".pyc", ".pyo"))
        and path not in {"node_modules", "ui/node_modules"}
        and not is_generated_tool_artifact(path)
    ]


def is_generated_tool_artifact(path: str) -> bool:
    normalized = path.strip("/")
    if normalized in {
        ".pytest_cache",
        "test-results/.last-run.json",
        "playwright-report/index.html",
    }:
        return True
    ignored_prefixes = (
        ".pytest_cache/",
        ".gradle/",
        "build/",
        "test-results/",
        "playwright-report/",
        "ui/test-results/",
        "ui/playwright-report/",
    )
    ignored_suffixes = (
        ".class",
        ".log",
        ".tmp",
    )
    return normalized.startswith(ignored_prefixes) or normalized.endswith(ignored_suffixes)


def run_qa_verification(
    command: list[str],
    *,
    worktree: Path,
    project_metadata: dict[str, Any],
    timeout_seconds: float = 300,
    database_url: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    feature_id: str | None = None,
) -> QAVerificationResult:
    try:
        result = run_bounded(
            command,
            cwd=worktree,
            timeout_seconds=timeout_seconds,
            env=qa_env(project_metadata, project_root=worktree),
        )
    except OSError as exc:
        result = SubprocessResult(
            argv=command,
            exit_code=127,
            stdout="",
            stderr=str(exc),
            duration_seconds=0,
            timed_out=False,
            killed=False,
        )
    filtered = filter_subprocess_result(
        result,
        record_in=database_url,
        workflow_id=workflow_id,
        task_id=task_id,
        feature_id=feature_id,
        role="qa",
    )
    stdout_excerpt = filtered.filtered_stdout[-2000:]
    stderr_excerpt = filtered.filtered_stderr[-2000:]
    return QAVerificationResult(
        original=result,
        stdout_excerpt=stdout_excerpt,
        stderr_excerpt=stderr_excerpt,
        infra_error=verification_infra_error(result.stdout, result.stderr),
    )


def canonical_red_proof(result: QAVerificationResult) -> list[dict[str, Any]]:
    return [
        {
            "source": "orchestrator",
            "command": result.original.argv,
            "exit_code": result.original.exit_code,
            "failure_excerpt": _proof_excerpt(result),
            "stdout_excerpt": result.stdout_excerpt,
            "stderr_excerpt": result.stderr_excerpt,
            "timed_out": result.original.timed_out,
            "killed": result.original.killed,
        }
    ]


def verification_infra_error(stdout: str, stderr: str) -> str | None:
    combined = f"{stdout}\n{stderr}".lower()
    stderr_lower = stderr.lower()
    patterns = [
        "unable to locate a java runtime",
        "java_home is not set",
        "command not found",
        "could not find or load main class org.gradle.wrapper.gradlewrappermain",
        "node: command not found",
        "npm: command not found",
        "cannot find package",
        "getaddrinfo enotfound",
        "npm error network",
    ]
    for pattern in patterns:
        if pattern in combined:
            return pattern
    if _missing_executable_error(stderr_lower):
        return "no such file or directory"
    return None


def _missing_executable_error(stderr: str) -> bool:
    if "no such file or directory" not in stderr:
        return False
    if "filenotfounderror" in stderr and "subprocess" not in stderr:
        return False
    executable_markers = [
        "env:",
        "exec:",
        "spawn",
        "subprocess",
        "failed to run",
        "cannot execute",
        "[errno 2]",
    ]
    return any(marker in stderr for marker in executable_markers)


def red_proof_infra_error(red_proof: list[dict[str, Any]]) -> str | None:
    for proof in red_proof:
        excerpt = proof.get("failure_excerpt")
        if isinstance(excerpt, str):
            issue = verification_infra_error(excerpt, "")
            if issue is not None:
                return issue
    return None


def red_proof_matches_verification(
    red_proof: list[dict[str, Any]],
    verification_command: list[str],
) -> bool:
    expected = " ".join(verification_command)
    expected_tail = " ".join(verification_command[-2:])
    for proof in red_proof:
        command = proof.get("command")
        normalized = " ".join(command) if isinstance(command, list) else command
        if not isinstance(normalized, str):
            continue
        normalized = " ".join(normalized.split())
        if expected in normalized or normalized in expected:
            return True
        if expected_tail and expected_tail in normalized:
            return True
    return False


def _path_prepend(
    metadata: dict[str, Any],
    env: dict[str, str],
    project_root: Path | None,
) -> list[str]:
    raw = metadata.get("path_prepend")
    if not isinstance(raw, list):
        return []
    expanded: list[str] = []
    for item in raw:
        if isinstance(item, str):
            expanded.append(_expand_env_value(item, {**os.environ, **env}, project_root))
    return expanded


def _expand_env_value(value: str, env: dict[str, str], project_root: Path | None) -> str:
    expanded = value
    if project_root is not None:
        expanded = expanded.replace("${PROJECT_ROOT}", str(project_root))
        expanded = expanded.replace("$PROJECT_ROOT", str(project_root))
    for key, replacement in env.items():
        expanded = expanded.replace(f"${{{key}}}", replacement)
        expanded = expanded.replace(f"${key}", replacement)
    return expanded


def _discover_java_home() -> Path | None:
    current = os.environ.get("JAVA_HOME")
    candidates = [
        Path(current) if current else None,
        Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"),
        Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
        Path("/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home"),
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "bin/java").exists():
            return candidate
    return None


def _route_inventory_item(item: object) -> dict[str, str]:
    if isinstance(item, str):
        parts = item.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isalpha():
            return {"method": parts[0].upper(), "path": parts[1], "source": "metadata"}
        return {"method": "ANY", "path": item, "source": "metadata"}
    if isinstance(item, dict):
        path = item.get("path")
        method = item.get("method", "ANY")
        source = item.get("source", "metadata")
        return {
            "method": str(method).upper(),
            "path": str(path) if path is not None else "",
            "source": str(source),
        }
    return {"method": "ANY", "path": "", "source": "metadata"}


def _filter_route_inventory(
    routes: list[dict[str, str]],
    api_prefixes: list[str] | None,
    limit: int,
) -> list[dict[str, str]]:
    prefixes = [prefix.rstrip("/") for prefix in api_prefixes or []]
    filtered = [
        route
        for route in routes
        if route.get("path")
        and (
            not prefixes
            or any(str(route["path"]).startswith(prefix) for prefix in prefixes)
        )
    ]
    concrete_paths = {route["path"] for route in filtered if route.get("method") != "ANY"}
    filtered = [
        route
        for route in filtered
        if route.get("method") != "ANY" or route["path"] not in concrete_paths
    ]
    return sorted(filtered, key=lambda item: (item["path"], item.get("method", "")))[:limit]


def _metadata_roots(project_root: Path, metadata: dict[str, Any], *keys: str) -> list[Path]:
    roots: list[Path] = []
    for key in keys:
        value = metadata.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, str):
                continue
            path = Path(item)
            if not path.is_absolute():
                path = project_root / path
            roots.append(path)
    return sorted(dict.fromkeys(roots))


def _iter_route_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".py"}
        and "node_modules" not in path.parts
        and "build" not in path.parts
        and ".git" not in path.parts
    ][:5000]


def _spring_annotation_routes(text: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    pattern = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\(([^)]*)\)")
    method_by_annotation = {
        "Get": "GET",
        "Post": "POST",
        "Put": "PUT",
        "Delete": "DELETE",
        "Patch": "PATCH",
        "Request": "ANY",
    }
    matches = list(pattern.finditer(text))
    class_prefixes: list[str] = []
    for index, match in enumerate(matches):
        method = method_by_annotation[match.group(1)]
        body = match.group(2)
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        following = text[match.end() : next_start]
        explicit_method = re.search(r"method\s*=\s*RequestMethod\.([A-Z]+)", body)
        if explicit_method is not None:
            method = explicit_method.group(1)
        route_paths = _quoted_paths(body)
        if match.group(1) == "Request" and re.search(r"\b(class|interface)\s+\w+", following):
            class_prefixes = [path for path in route_paths if path.startswith("/")]
            continue
        for route_path in _quoted_paths(body):
            if route_path.startswith("/api/"):
                routes.append({"method": method, "path": route_path, "source": ""})
                continue
            for class_prefix in class_prefixes:
                combined = _join_route_paths(class_prefix, route_path)
                if combined.startswith("/api/"):
                    routes.append({"method": method, "path": combined, "source": ""})
    return routes


def _join_route_paths(prefix: str, suffix: str) -> str:
    if not suffix:
        return prefix
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{prefix.rstrip('/')}{suffix}"


def _quoted_api_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"""["'](/api/[^"']*)["']""", text):
        prefix = text[max(0, match.start() - 80) : match.start()]
        if re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\([^)]*$", prefix):
            continue
        paths.append(match.group(1))
    return paths


def _quoted_paths(text: str) -> list[str]:
    paths: list[str] = []
    for quote in ['"', "'"]:
        parts = text.split(quote)
        for index, part in enumerate(parts):
            if index % 2 == 1 and part.startswith("/"):
                paths.append(part)
    return paths


def _proof_excerpt(result: QAVerificationResult) -> str:
    combined = "\n".join(part for part in [result.stdout_excerpt, result.stderr_excerpt] if part)
    return combined[-2000:]


def _deep_merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(existing, value)
        else:
            merged[key] = value
    return merged
