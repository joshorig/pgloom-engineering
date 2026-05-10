from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pgloom_engineering.qa_runtime import project_qa_metadata

Severity = Literal["blocking", "warning"]


@dataclass(frozen=True)
class SemanticFinding:
    code: str
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def asdict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.file is not None:
            payload["file"] = self.file
        if self.line is not None:
            payload["line"] = self.line
        payload.update(self.details)
        return payload


def review_semantic_quality(
    *,
    files: dict[str, str],
    plan_text: str,
    task_text: str,
    project_metadata: dict[str, Any],
) -> list[SemanticFinding]:
    qa_metadata = _qa_metadata(project_metadata)
    conventions = _semantic_conventions(qa_metadata)
    context = f"{plan_text}\n{task_text}".lower()
    findings: list[SemanticFinding] = []
    findings.extend(_java_array_assertion_findings(files, conventions))
    findings.extend(_journal_cursor_findings(files, context, conventions))
    findings.extend(_spring_endpoint_harness_findings(files, conventions))
    findings.extend(_structured_payload_assertion_findings(files, conventions))
    findings.extend(_jmh_cold_restore_findings(files, context, conventions))
    findings.extend(_jmh_reflective_invocation_findings(files, conventions))
    findings.extend(_build_file_hook_findings(files, conventions))
    return findings


def _qa_metadata(project_metadata: dict[str, Any]) -> dict[str, Any]:
    return project_qa_metadata(project_metadata)


def _semantic_conventions(qa_metadata: dict[str, Any]) -> dict[str, Any]:
    conventions = qa_metadata.get("semantic_conventions")
    return conventions if isinstance(conventions, dict) else {}


def _java_array_assertion_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    binary_config = _mapping(conventions.get("binary_assertions"))
    severity: Severity = (
        "blocking" if binary_config.get("prefer_assert_array_equals") else "warning"
    )
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(".java"):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            compact = line.replace(" ", "")
            if "assertEquals(Arrays.toString(" not in compact:
                continue
            findings.append(
                SemanticFinding(
                    code="qa_semantic_brittle_array_assertion",
                    severity=severity,
                    message=(
                        "Byte/array equality is asserted through Arrays.toString; "
                        "use assertArrayEquals or an equivalent structured array assertion."
                    ),
                    file=path,
                    line=line_no,
                )
            )
    return findings


def _journal_cursor_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    journal_config = _mapping(conventions.get("journal"))
    if not journal_config.get("failed_publish_must_not_advance_cursor"):
        return []
    journal_terms = ["journal", "cursor", "staged", "unjournaled", "aborted"]
    if not any(token in context for token in journal_terms):
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(".java"):
            continue
        for method in _java_test_methods(text):
            body = method["body"]
            lowered = body.lower()
            failure_terms = ["abort", "aborted", "fail", "crash", "unjournaled"]
            if not any(token in lowered for token in failure_terms):
                continue
            published = _published_sequence_literals(body)
            asserted = _published_seq_assertions(body)
            if len(published) < 2 or not asserted:
                continue
            last_acknowledged = published[-2]
            failed_or_staged = published[-1]
            for expected, line_no in asserted:
                if expected == failed_or_staged and expected != last_acknowledged:
                    findings.append(
                        SemanticFinding(
                            code="qa_semantic_journal_cursor_mismatch",
                            severity="blocking",
                            message=(
                                "A failed or staged journal write appears to advance the restored "
                                "published cursor; assert the last acknowledged sequence instead."
                            ),
                            file=path,
                            line=method["start_line"] + line_no - 1,
                            details={
                                "asserted_sequence": expected,
                                "last_acknowledged_sequence": last_acknowledged,
                            },
                        )
                    )
    return findings


def _spring_endpoint_harness_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    endpoint_config = _mapping(conventions.get("endpoint_acceptance"))
    if not endpoint_config.get("require_http_harness"):
        return []
    java_tests = {
        path: text
        for path, text in files.items()
        if path.endswith(".java")
    }
    findings: list[SemanticFinding] = []
    for path, text in java_tests.items():
        if not _has_spring_endpoint_signal(text):
            continue
        if _has_spring_http_harness(text):
            continue
        direct_call_lines = [
            line_no
            for line_no, line in enumerate(text.splitlines(), start=1)
            if re.search(r"\bcontroller\.\w+\(", line)
            or re.search(r"\b\w+Controller\s+\w+\s*=", line)
        ]
        if not direct_call_lines:
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_direct_spring_controller_call",
                severity="blocking",
                message=(
                    "Endpoint acceptance requires an HTTP/Spring harness; direct controller "
                    "method calls do not prove route binding, query parsing, or status semantics."
                ),
                file=path,
                line=direct_call_lines[0],
                details={"required_harnesses": ["MockMvc", "WebTestClient", "TestRestTemplate"]},
            )
        )
    return findings


def _structured_payload_assertion_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    payload_config = _mapping(conventions.get("payload_assertions"))
    if not payload_config.get("prefer_structured_json_paths"):
        return []
    max_contains = int(payload_config.get("max_raw_contains_per_file") or 6)
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith((".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx")):
            continue
        contains_count = text.count(".contains(") + text.count("toContain(")
        structured_count = sum(
            text.count(marker)
            for marker in [
                ".path(",
                ".get(",
                "jsonPath(",
                "JsonPath.",
                "expect(",
                "readTree(",
            ]
        )
        raw_json_contains = _raw_json_contains_count(text)
        if raw_json_contains == 0 and (
            contains_count <= max_contains or structured_count >= contains_count
        ):
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_brittle_payload_assertions",
                severity="blocking",
                message=(
                    "Payload assertions rely on broad raw JSON string containment; use "
                    "structured JSON path/field assertions for domain, graph, symbol, status, "
                    "and route semantics, reserving string contains for explicitly textual fields."
                ),
                file=path,
                line=(
                    _first_line_containing_any(
                        text,
                        [".toString().contains(", "JSON.stringify(", "json.dumps("],
                    )
                    if raw_json_contains
                    else _first_line_containing_any(text, [".contains(", "toContain("])
                ),
                details={
                    "raw_contains_count": contains_count,
                    "raw_json_contains_count": raw_json_contains,
                    "structured_assertion_count": structured_count,
                },
            )
        )
    return findings


def _raw_json_contains_count(text: str) -> int:
    return (
        text.count(".toString().contains(")
        + text.count("JSON.stringify(")
        + text.count("json.dumps(")
    )


def _jmh_cold_restore_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    restore_config = _mapping(conventions.get("restore_benchmark"))
    if not restore_config.get("cold_start_semantics"):
        return []
    if not all(token in context for token in ["restore", "benchmark"]):
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not _looks_like_jmh_benchmark(path, text):
            continue
        lowered = text.lower()
        if not _has_restore_benchmark_signal(text):
            continue
        if "@setup(level.trial)" not in lowered.replace(" ", ""):
            continue
        if not _has_cold_restore_rotation(text):
            line_no = _first_line_containing(text, "restore(")
            findings.append(
                SemanticFinding(
                    code="qa_semantic_jmh_restore_not_cold",
                    severity="blocking",
                    message=(
                        "Restore benchmark appears to restore repeatedly into trial-level state; "
                        "cold restore semantics need preallocated fresh targets or an explicit "
                        "project-approved reset strategy outside the measured method."
                    ),
                    file=path,
                    line=line_no,
                    details={
                        "accepted_strategy": restore_config.get("fresh_target_strategy")
                        or "preallocated_target_pool"
                    },
                )
            )
        elif _reuses_restore_targets_in_sample_time(text):
            findings.append(
                SemanticFinding(
                    code="qa_semantic_jmh_restore_target_reuse",
                    severity="blocking",
                    message=(
                        "Cold restore benchmark uses sampling mode with a rotating finite "
                        "target pool; after the first cycle it can measure warm/idempotent "
                        "restore. Use sized SingleShotTime targets or an explicit reset "
                        "strategy outside the measured method."
                    ),
                    file=path,
                    line=_first_line_containing_any(text, ["restoreTargetCursor", "targetCursor"]),
                    details={
                        "accepted_strategy": restore_config.get("fresh_target_strategy")
                        or "preallocated_target_pool"
                    },
                )
            )
        if _has_exhaustible_target_pool(text):
            findings.append(
                SemanticFinding(
                    code="qa_semantic_jmh_exhaustible_target_pool",
                    severity="blocking",
                    message=(
                        "Benchmark uses a finite one-shot target pool that can be exhausted "
                        "by normal JMH warmup/measurement invocations; use a non-allocating "
                        "rotation/reset strategy that cannot throw during measurement."
                    ),
                    file=path,
                    line=_first_line_containing_any(text, ["pool exhausted", "Pool exhausted"]),
                    details={
                        "accepted_strategy": restore_config.get("fresh_target_strategy")
                        or "preallocated_target_pool"
                    },
                )
            )
    return findings


def _build_file_hook_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    build_config = _mapping(conventions.get("build_hook_tests"))
    if build_config.get("allow_build_file_string_assertions", True):
        return []
    severity: Severity = (
        "blocking"
        if build_config.get("deterministic_gate_validation_required")
        else "warning"
    )
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith((".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx")):
            continue
        if not any(name in text for name in ["build.gradle", "pom.xml", "qa/smoke.sh"]):
            continue
        if not any(marker in text for marker in [".contains(", "containsString(", "assertTrue("]):
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_build_file_string_assertion",
                severity=severity,
                message=(
                    "Generated tests inspect build or QA script text; prefer behavior tests "
                    "and let deterministic orchestration verify command wiring."
                ),
                file=path,
                line=_first_line_containing_any(text, ["build.gradle", "pom.xml", "qa/smoke.sh"]),
            )
        )
    return findings


def _jmh_reflective_invocation_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    jmh_config = _mapping(conventions.get("jmh_benchmark"))
    severity: Severity = (
        "warning" if jmh_config.get("allow_reflective_invocation") else "blocking"
    )
    findings: list[SemanticFinding] = []
    reflective_markers = [
        "LambdaMetafactory",
        "MethodHandles.lookup()",
        ".unreflect(",
        ".getMethod(",
        "Proxy.newProxyInstance(",
        "Method.invoke(",
    ]
    for path, text in files.items():
        if not _looks_like_jmh_benchmark(path, text):
            continue
        if not any(marker in text for marker in reflective_markers):
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_jmh_reflective_invocation",
                severity=severity,
                message=(
                    "Generated JMH smoke benchmarks should call the feature through typed "
                    "interfaces, not reflection, Proxy, or LambdaMetafactory; reflective "
                    "harnesses can fail during JMH setup or measure the harness instead of "
                    "the feature."
                ),
                file=path,
                line=_first_line_containing_any(text, reflective_markers),
            )
        )
    return findings


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _java_test_methods(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    methods: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if "@Test" not in lines[index]:
            index += 1
            continue
        start = index
        while index < len(lines) and "{" not in lines[index]:
            index += 1
        if index >= len(lines):
            break
        body_lines = [lines[index]]
        depth = lines[index].count("{") - lines[index].count("}")
        index += 1
        while index < len(lines) and depth > 0:
            body_lines.append(lines[index])
            depth += lines[index].count("{") - lines[index].count("}")
            index += 1
        methods.append({"start_line": start + 1, "body": "\n".join(body_lines)})
    return methods


def _published_sequence_literals(text: str) -> list[int]:
    values: list[int] = []
    for match in re.finditer(r"publish\w*\s*\([^;\n]*?(\d+)L", text):
        values.append(int(match.group(1)))
    return values


def _published_seq_assertions(text: str) -> list[tuple[int, int]]:
    assertions: list[tuple[int, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "publishedSeq" not in line:
            continue
        match = re.search(r"assert(?:That|Equals)?\s*\(?\s*(\d+)L", line)
        if match:
            assertions.append((int(match.group(1)), line_no))
            continue
        match = re.search(r"\.isEqualTo\((\d+)L\)", line)
        if match:
            assertions.append((int(match.group(1)), line_no))
    return assertions


def _has_spring_endpoint_signal(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "Controller",
            "ResponseEntity",
            "@GetMapping",
            "@PostMapping",
            "@RequestMapping",
            "HttpStatus",
        ]
    )


def _has_spring_http_harness(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "MockMvc",
            "@WebMvcTest",
            "mockMvc.perform",
            "WebTestClient",
            "TestRestTemplate",
            "@SpringBootTest",
        ]
    )


def _looks_like_jmh_benchmark(path: str, text: str) -> bool:
    lowered = path.lower()
    return "src/jmh/" in lowered or "benchmark" in lowered or "@Benchmark" in text


def _has_cold_restore_rotation(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "targetpool",
            "target_pool",
            "restoretargets",
            "restore_targets",
            "nexttarget",
            "targetindex",
            "resettarget",
            "preallocated",
        ]
    )


def _has_restore_benchmark_signal(text: str) -> bool:
    lowered = text.lower()
    return "restore(" in lowered or ("restorehandle" in lowered and "invokeexact" in lowered)


def _has_exhaustible_target_pool(text: str) -> bool:
    lowered = text.lower()
    if _has_sufficient_single_shot_target_pool(text):
        return False
    if "pool exhausted" in lowered or "target pool exhausted" in lowered:
        return True
    if "throw new illegalstateexception" not in lowered:
        return False
    return any(token in lowered for token in ["targetpool", "target_pool", "restoretargets"])


def _has_sufficient_single_shot_target_pool(text: str) -> bool:
    compact = text.replace(" ", "")
    if "@BenchmarkMode(Mode.SingleShotTime)" not in compact:
        return False
    warmup = _jmh_iteration_count(text, "Warmup")
    measurement = _jmh_iteration_count(text, "Measurement")
    if warmup is None or measurement is None:
        return False
    target_count = _restore_target_count(text)
    if target_count is None:
        return False
    return target_count >= warmup + measurement


def _reuses_restore_targets_in_sample_time(text: str) -> bool:
    compact = text.replace(" ", "")
    if "@BenchmarkMode(Mode.SingleShotTime)" in compact:
        return False
    sample_mode = (
        "@BenchmarkMode(Mode.SampleTime)" in compact
        or "@BenchmarkMode(Mode.AverageTime)" in compact
        or "@BenchmarkMode(Mode.Throughput)" in compact
    )
    if not sample_mode:
        return False
    lowered = text.lower()
    has_restore_pool = any(token in lowered for token in ["restoretargets", "restore_targets"])
    has_rotation = any(
        token in text
        for token in ["%", "& (restoreTargets.length - 1)", "restoreTargetCursor = 0"]
    )
    return has_restore_pool and has_rotation


def _jmh_iteration_count(text: str, annotation: str) -> int | None:
    match = re.search(rf"@{annotation}\s*\([^)]*iterations\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _restore_target_count(text: str) -> int | None:
    constants = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"static\s+final\s+int\s+(\w+)\s*=\s*(\d+)\s*;", text)
    }
    direct = re.search(r"restore\w*\s*=\s*new\s+\w+\s*\[\s*(\d+)\s*\]", text, re.IGNORECASE)
    if direct:
        return int(direct.group(1))
    symbolic = re.search(r"restore\w*\s*=\s*new\s+\w+\s*\[\s*(\w+)\s*\]", text, re.IGNORECASE)
    if symbolic:
        return constants.get(symbolic.group(1))
    return None


def _first_line_containing(text: str, needle: str) -> int | None:
    return _first_line_containing_any(text, [needle])


def _first_line_containing_any(text: str, needles: list[str]) -> int | None:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if any(needle in line for needle in needles):
            return line_no
    return None
