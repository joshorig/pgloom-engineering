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
    findings.extend(_java_try_resource_close_findings(files, conventions))
    findings.extend(_journal_cursor_findings(files, context, conventions))
    findings.extend(_spring_endpoint_harness_findings(files, conventions))
    findings.extend(_structured_payload_assertion_findings(files, conventions))
    findings.extend(_jmh_cold_restore_findings(files, context, conventions))
    findings.extend(_jmh_reflective_invocation_findings(files, conventions))
    findings.extend(_range_benchmark_api_findings(files, context, conventions))
    findings.extend(_range_benchmark_behavior_findings(files, context, conventions))
    findings.extend(_range_benchmark_smoke_threshold_findings(files, context, conventions))
    findings.extend(_existing_smoke_threshold_relaxation_findings(files, context, conventions))
    findings.extend(_benchmark_visitor_signature_findings(files, context, conventions))
    findings.extend(_range_test_reflective_api_findings(files, context, conventions))
    findings.extend(_range_test_null_receiver_findings(files, context, conventions))
    findings.extend(_range_prefix_behavior_findings(files, context, conventions))
    findings.extend(_range_key_prefix_semantics_findings(files, context, conventions))
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


def _java_try_resource_close_findings(
    files: dict[str, str],
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    java_config = _mapping(conventions.get("java_tests"))
    if java_config.get("allow_autocloseable_checked_close"):
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(".java") or "test" not in path.lower():
            continue
        if "implements AutoCloseable" not in text:
            continue
        line_no = _first_line_containing_any(
            text,
            ["void close() throws Exception", "void close() throws Throwable"],
        )
        if line_no is None:
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_java_try_resource_checked_close",
                severity="blocking",
                message=(
                    "Java QA tests define an AutoCloseable helper whose close() throws a "
                    "broad checked exception. In -Werror builds this can make "
                    "try-with-resources fail before the implementation is evaluated; catch "
                    "or wrap cleanup exceptions so close() does not declare Exception."
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


def _range_benchmark_api_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_benchmark"))
    if range_config.get("require_public_range_api") is False:
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context or "benchmark" not in normalized_context:
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not _looks_like_jmh_benchmark(path, text):
            continue
        lowered = text.lower()
        if "range" not in lowered:
            continue
        calls_range_api = "ascendingrange" in lowered or "descendingrange" in lowered
        if calls_range_api:
            continue
        if "readslicepooled" not in lowered and "readonlyslice" not in lowered:
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_range_benchmark_not_public_api",
                severity="blocking",
                message=(
                    "Range benchmark smoke must exercise the public StoreVisitor range "
                    "API; looping over readSlicePooled or ReadOnlySlice measures a lower "
                    "level path and does not prove the visitor range hot path."
                ),
                file=path,
                line=_first_line_containing_any(
                    text,
                    ["readSlicePooled", "ReadOnlySlice"],
                ),
            )
        )
    return findings


def _range_benchmark_behavior_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_benchmark"))
    if range_config.get("require_behavior_coverage") is False:
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context or "benchmark" not in normalized_context:
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not _looks_like_jmh_benchmark(path, text):
            continue
        lowered = text.lower()
        if "range" not in lowered:
            continue
        has_ascending = "ascendingrange" in lowered
        has_descending = "descendingrange" in lowered
        has_prefix = "ascendingrange" in lowered and "prefix" in lowered
        if not (has_ascending or has_descending):
            continue
        if has_ascending and has_descending and has_prefix:
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_range_benchmark_behavior_gap",
                severity="blocking",
                message=(
                    "Range benchmark smoke must cover the behavior surfaces named by "
                    "the feature: ascending, descending, and prefix-filtered visitor "
                    "range scans. A single ascending-only benchmark can miss ordering "
                    "or filter-path allocation defects."
                ),
                file=path,
                line=_first_line_containing_any(
                    text,
                    ["ascendingRange", "descendingRange", "prefix"],
                ),
            )
        )
    return findings


def _benchmark_visitor_signature_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    benchmark_config = _mapping(conventions.get("range_benchmark"))
    if benchmark_config.get("validate_store_visitor_signature") is False:
        return []
    normalized_context = context.lower()
    if "storevisitor" not in normalized_context and "range" not in normalized_context:
        return []
    expected = _store_visitor_signature(files)
    if not expected:
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not _looks_like_jmh_benchmark(path, text):
            continue
        if "StoreVisitor" not in text or "::" not in text:
            continue
        method_params = _java_method_parameter_types(text)
        for method_name, line_no in _store_visitor_method_references(text):
            actual = method_params.get(method_name)
            if actual is None or actual == expected:
                continue
            findings.append(
                SemanticFinding(
                    code="qa_semantic_benchmark_visitor_signature_mismatch",
                    severity="blocking",
                    message=(
                        "Generated JMH benchmark StoreVisitor method references must "
                        "match the current public StoreVisitor callback signature; "
                        "mismatched arity or parameter types can compile against stale "
                        "fixtures and fail downstream review."
                    ),
                    file=path,
                    line=line_no,
                    details={
                        "method_reference": method_name,
                        "expected_signature": expected,
                        "actual_signature": actual,
                    },
                )
            )
    return findings


def _range_test_reflective_api_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_api_tests"))
    if range_config.get("allow_reflective_api_discovery"):
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context and "storevisitor" not in normalized_context:
        return []
    reflective_markers = [
        "Class.forName(",
        "java.lang.reflect.Method",
        "java.lang.reflect.Modifier",
        ".getDeclaredMethod(",
        ".getDeclaredMethods(",
        ".getMethod(",
        ".getParameterTypes(",
        "Modifier.isAbstract(",
        "Method.invoke(",
        "InvocationHandler",
        "Proxy.newProxyInstance(",
    ]
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(".java") or "test" not in path.lower():
            continue
        lowered = text.lower()
        if "range" not in lowered and "storevisitor" not in lowered:
            continue
        if not any(marker in text for marker in reflective_markers):
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_range_test_reflective_api",
                severity="blocking",
                message=(
                    "Range-scan QA tests should compile against the typed public API "
                    "directly; reflection, dynamic proxies, or Method.invoke hide API "
                    "shape mistakes and are not production-grade acceptance coverage."
                ),
                file=path,
                line=_first_line_containing_any(text, reflective_markers),
            )
        )
    return findings


def _range_test_null_receiver_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_api_tests"))
    if range_config.get("allow_null_receiver_api_smoke"):
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context and "storevisitor" not in normalized_context:
        return []
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(".java") or "test" not in path.lower():
            continue
        lowered = text.lower()
        if "range" not in lowered and "storevisitor" not in lowered:
            continue
        line_no = _null_lvc_receiver_range_call_line(text) or _null_helper_range_call_line(text)
        if line_no is None:
            continue
        findings.append(
            SemanticFinding(
                code="qa_semantic_range_null_receiver_api_test",
                severity="blocking",
                message=(
                    "Range API tests must not prove method existence by invoking range "
                    "methods on a null LvcStore. Once the API compiles, the JUnit test "
                    "fails with NullPointerException instead of testing product behavior; "
                    "use a real store fixture or a typed fake implementation."
                ),
                file=path,
                line=line_no,
            )
        )
    return findings


def _range_prefix_behavior_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_prefix_behavior"))
    if range_config.get("required") is False:
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context or "prefix" not in normalized_context:
        return []
    candidate_files = {
        path: text
        for path, text in files.items()
        if path.endswith(".java") and "test" in path.lower()
    }
    if not candidate_files:
        return []
    combined = "\n".join(candidate_files.values()).lower()
    has_range_call = any(
        marker in combined
        for marker in [
            "ascendingrange",
            "descendingrange",
            "ascendingentries",
            "descendingentries",
        ]
    )
    has_matching_prefix = any(
        marker in combined
        for marker in [
            "prefix_match",
            "prefixmatch",
            "matchingprefix",
            "matching prefix",
            "match prefix",
            "matching-prefix",
            "prefixfiltermatches",
            "prefix value",
            "prefix_value",
        ]
    )
    has_nonmatching_prefix = any(
        marker in combined
        for marker in [
            "prefix_non_match",
            "prefixnonmatch",
            "nonmatchingprefix",
            "prefix_miss",
            "prefixmiss",
            "non-matching prefix",
            "nonmatching prefix",
            "non match prefix",
            "prefix miss",
            "missingprefix",
            "assertprefixskipsnonmatches",
            "skip nonmatch",
            "skips nonmatch",
        ]
    )
    if has_range_call and has_matching_prefix and has_nonmatching_prefix:
        return []
    first_path = next(iter(candidate_files))
    return [
        SemanticFinding(
            code="qa_semantic_range_prefix_behavior_missing",
            severity="blocking",
            message=(
                "Range-scan QA must include public-API behavior assertions for both "
                "matching-prefix and non-matching-prefix scans; overload existence or "
                "build wiring alone does not cover prefix acceptance."
            ),
            file=first_path,
            line=_first_line_containing_any(
                candidate_files[first_path],
                ["ascendingRange", "descendingRange", "prefix"],
            ),
        )
    ]


def _range_key_prefix_semantics_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    range_config = _mapping(conventions.get("range_prefix_behavior"))
    if not range_config.get("key_prefix_filter_required"):
        return []
    normalized_context = context.lower()
    if "range" not in normalized_context or "prefix" not in normalized_context:
        return []
    if "key-prefix" not in normalized_context and "key prefix" not in normalized_context:
        return []
    candidate_files = {
        path: text
        for path, text in files.items()
        if path.endswith(".java") and "test" in path.lower()
    }
    if not candidate_files:
        return []
    combined = "\n".join(candidate_files.values())
    lowered = combined.lower()
    has_prefix_range_exercise = any(
        marker in lowered
        for marker in [
            "ascendingrange",
            "descendingrange",
            "collectascending",
            "collectdescending",
            "visitascending",
            "visitdescending",
        ]
    )
    if not has_prefix_range_exercise:
        return []
    if "prefix" not in lowered:
        return []
    has_key_prefix_signal = any(
        marker in lowered
        for marker in [
            "keyprefix",
            "key prefix",
            "keyindex",
            "genericlvc",
            "longkeyindex",
            "toslotid",
            "logical key",
            "logical-key",
            "key bytes",
            "keybytes",
            "prefix_range_start",
            "prefix_range_end",
            "prefix_value",
            "prefix_bits",
            "prefixbits",
        ]
    )
    payload_prefix_seed = _payload_prefix_seed_signal(combined)
    if has_key_prefix_signal:
        if not _key_prefix_multi_match_signal(combined):
            first_path = next(iter(candidate_files))
            return [
                SemanticFinding(
                    code="qa_semantic_range_key_prefix_too_narrow",
                    severity="blocking",
                    message=(
                        "R-003 key-prefix QA is too narrow: a full-key prefix that matches "
                        "one slot does not prove prefix filtering. Add a partial-prefix or "
                        "logical-key fixture where one prefix matches multiple populated keys "
                        "and a different prefix matches none."
                    ),
                    file=first_path,
                    line=_first_line_containing_any(
                        candidate_files[first_path],
                        ["PREFIX_BITS", "prefixBytesForSlot", "ascendingRange", "prefix"],
                    ),
                )
            ]
        return []
    first_path = next(iter(candidate_files))
    return [
        SemanticFinding(
            code="qa_semantic_range_key_prefix_not_payload_prefix",
            severity="blocking",
            message=(
                "R-003 requires key-prefix filtering. QA tests must prove prefix "
                "matching against logical keys or an explicit key mapping; seeding "
                "matching bytes into payload[0..] only proves payload-prefix filtering."
            ),
            file=first_path,
            line=_first_line_containing_any(
                candidate_files[first_path],
                ["ascendingRange", "descendingRange", "prefix"],
            ),
            details={"payload_prefix_seed_detected": payload_prefix_seed},
        )
    ]


def _key_prefix_multi_match_signal(text: str) -> bool:
    compact = re.sub(r"\s+", "", text.lower())
    if any(marker in text.lower() for marker in ["logical key", "logical-key"]):
        return True
    if "logicalkey" in compact or "genericlvc" in compact:
        return True
    if "prefix_bits" in compact or "prefixbits" in compact:
        return True
    if re.search(r"prefixbytesforslot\([^,]+,\s*integer\.bytes-[1-9]", compact):
        return True
    if re.search(r"newbyte\[[123]\]", compact):
        return True
    return False


def _payload_prefix_seed_signal(text: str) -> bool:
    compact = re.sub(r"\s+", "", text.lower())
    return any(
        marker in compact
        for marker in [
            "payload[0]=",
            "payload[1]=",
            ".putbyte(0,",
            ".putbyte(1,",
            "payload.putbyte(0,",
            "payload.putbyte(1,",
            "matchesprefix(buffer,payloadoffset,len,prefix)",
            "matchesprefix(slab,payloadoffset,len,prefix)",
            "buffer.getbyte(offset+i)!=prefix[i]",
        ]
    )


def _range_benchmark_smoke_threshold_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    threshold_config = _mapping(conventions.get("range_benchmark_smoke_threshold"))
    if threshold_config.get("required") is False:
        return []
    if "range" not in context or "benchmark" not in context:
        return []
    minimum = float(threshold_config.get("minimum_alloc_bytes_per_op") or 0.05)
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(("build.gradle", "build.gradle.kts")):
            continue
        if (
            "rangeScanSmoke" not in text
            and "RangeScanBenchmark" not in text
            and "range scan" not in text.lower()
        ) or "jmhSmokeCheck" not in text:
            continue
        current_benchmark = ""
        for line_no, line in enumerate(text.splitlines(), start=1):
            key_match = re.search(r"['\"]([^'\"]*Benchmark\.[^'\"]+)['\"]\s*:", line)
            if key_match:
                current_benchmark = key_match.group(1)
            threshold_match = re.search(
                r"allocBytesPerOp\s*:\s*[^,\n]*\?:\s*([0-9]+(?:\.[0-9]+)?)d?",
                line,
            )
            if threshold_match is None:
                continue
            if current_benchmark and "rangescan" not in current_benchmark.lower():
                continue
            threshold = float(threshold_match.group(1))
            if threshold >= minimum:
                continue
            findings.append(
                SemanticFinding(
                    code="qa_semantic_range_benchmark_smoke_threshold_too_strict",
                    severity="blocking",
                    message=(
                        "Range-scan JMH smoke gate uses a near-zero allocation threshold. "
                        "Smoke benchmarks should prove coverage and catch gross allocation "
                        "regressions; sub-0.01 B/op thresholds are too noisy for autonomous "
                        "feature validation."
                    ),
                    file=path,
                    line=line_no,
                    details={
                        "benchmark": current_benchmark,
                        "threshold_bytes_per_op": threshold,
                        "minimum_threshold_bytes_per_op": minimum,
                    },
                )
            )
    return findings


def _existing_smoke_threshold_relaxation_findings(
    files: dict[str, str],
    context: str,
    conventions: dict[str, Any],
) -> list[SemanticFinding]:
    threshold_config = _mapping(conventions.get("existing_smoke_thresholds"))
    if threshold_config.get("allow_relaxation"):
        return []
    if "benchmark" not in context and "allocation" not in context and "range" not in context:
        return []
    max_existing = float(threshold_config.get("max_existing_alloc_bytes_per_op") or 0.005)
    findings: list[SemanticFinding] = []
    for path, text in files.items():
        if not path.endswith(("build.gradle", "build.gradle.kts")):
            continue
        current_benchmark = ""
        for line_no, line in enumerate(text.splitlines(), start=1):
            key_match = re.search(r"['\"]([^'\"]*Benchmark\.[^'\"]+)['\"]\s*:", line)
            if key_match:
                current_benchmark = key_match.group(1)
            threshold_match = re.search(
                r"allocBytesPerOp\s*:\s*[^,\n]*\?:\s*([0-9]+(?:\.[0-9]+)?)d?",
                line,
            )
            if threshold_match is None or not current_benchmark:
                continue
            threshold = float(threshold_match.group(1))
            if (
                "cismokebenchmark." not in current_benchmark.lower()
                or "rangescan" in current_benchmark.lower()
                or threshold <= max_existing
            ):
                continue
            findings.append(
                SemanticFinding(
                    code="qa_semantic_existing_smoke_threshold_relaxed",
                    severity="blocking",
                    message=(
                        "Range benchmark QA may add feature-specific smoke coverage, but "
                        "must not relax existing CiSmokeBenchmark allocation thresholds; "
                        "that weakens unrelated project gates."
                    ),
                    file=path,
                    line=line_no,
                    details={
                        "benchmark": current_benchmark,
                        "threshold_bytes_per_op": threshold,
                        "max_existing_threshold_bytes_per_op": max_existing,
                    },
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


def _store_visitor_signature(files: dict[str, str]) -> list[str]:
    for path, text in files.items():
        if not path.endswith("StoreVisitor.java") or "interface StoreVisitor" not in text:
            continue
        for match in re.finditer(
            r"(?:public\s+)?(?:abstract\s+)?void\s+\w+\s*\(([^)]*)\)\s*;",
            text,
            re.MULTILINE,
        ):
            params = _java_parameter_types(match.group(1))
            if params:
                return params
    return []


def _store_visitor_method_references(text: str) -> list[tuple[str, int]]:
    references: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "::" not in line:
            continue
        if "StoreVisitor" not in line and "=" not in line:
            continue
        for match in re.finditer(r"(?:(?:this|[A-Z]\w*)\s*::\s*)([A-Za-z_]\w*)", line):
            references.append((match.group(1), line_no))
    return references


def _java_method_parameter_types(text: str) -> dict[str, list[str]]:
    methods: dict[str, list[str]] = {}
    pattern = re.compile(
        r"(?:public|protected|private)?\s*"
        r"(?:static\s+)?(?:final\s+)?"
        r"[\w<>\[\].?,\s]+\s+"
        r"([A-Za-z_]\w*)\s*\(([^)]*)\)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        methods[match.group(1)] = _java_parameter_types(match.group(2))
    return methods


def _java_parameter_types(params: str) -> list[str]:
    params = params.strip()
    if not params:
        return []
    types: list[str] = []
    for raw_param in params.split(","):
        param = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", raw_param.strip())
        param = re.sub(r"\bfinal\s+", "", param)
        pieces = param.split()
        if len(pieces) < 2:
            continue
        param_type = " ".join(pieces[:-1]).replace("...", "[]")
        param_type = re.sub(r"<[^>]+>", "", param_type)
        types.append(param_type.split(".")[-1])
    return types


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


def _null_lvc_receiver_range_call_line(text: str) -> int | None:
    null_vars = set(re.findall(r"\bLvcStore\s+(\w+)\s*=\s*null\s*;", text))
    if not null_vars:
        return None
    for line_no, line in enumerate(text.splitlines(), start=1):
        for var_name in null_vars:
            if re.search(
                rf"\b{re.escape(var_name)}\.(?:ascendingRange|descendingRange)\s*\(",
                line,
            ):
                return line_no
    return None


def _null_helper_range_call_line(text: str) -> int | None:
    helper_calls: dict[str, int] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = re.search(r"\b(\w+)\s*\(\s*null\s*\)\s*;", line)
        if match:
            helper_calls.setdefault(match.group(1), line_no)
    if not helper_calls:
        return None
    for method_name, call_line in helper_calls.items():
        signature = re.compile(
            rf"\b(?:private|public|protected)?\s*(?:static\s+)?"
            rf"(?:void|[\w<>[\]]+)\s+{re.escape(method_name)}\s*"
            r"\(\s*LvcStore\s+(\w+)\s*\)",
            re.MULTILINE,
        )
        match = signature.search(text)
        if not match:
            continue
        receiver = match.group(1)
        body = text[match.end() :]
        next_method = re.search(
            r"\n\s*(?:private|public|protected)\s+(?:static\s+)?"
            r"(?:void|[\w<>[\]]+)\s+\w+\s*\(",
            body,
        )
        if next_method:
            body = body[: next_method.start()]
        if re.search(
            rf"\b{re.escape(receiver)}\.(?:ascendingRange|descendingRange)\s*\(",
            body,
        ):
            return call_line
    return None
