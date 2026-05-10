from __future__ import annotations

from pgloom_engineering.qa_semantic_review import review_semantic_quality


def test_semantic_review_blocks_direct_spring_controller_when_http_harness_required() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/app-api/src/test/java/com/example/web/ConfigControllerTest.java": """
            class ConfigControllerTest {
                @Test
                void callsControllerDirectly() {
                    ConfigController controller = new ConfigController();
                    ResponseEntity<?> response = controller.runtime("crypto");
                    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
                }
            }
            """
        },
        plan_text="Every /api/config route preserves domain query semantics.",
        task_text="Write endpoint route tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "endpoint_acceptance": {"require_http_harness": True}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_direct_spring_controller_call"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_allows_mockmvc_for_spring_endpoint_contract() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/app-api/src/test/java/com/example/web/ConfigControllerTest.java": """
            @WebMvcTest(ConfigController.class)
            class ConfigControllerTest {
                @Autowired MockMvc mockMvc;

                @Test
                void callsRoute() throws Exception {
                    mockMvc.perform(get("/api/config/runtime").queryParam("domain", "crypto"))
                        .andExpect(status().isOk());
                }
            }
            """
        },
        plan_text="Every /api/config route preserves domain query semantics.",
        task_text="Write endpoint route tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "endpoint_acceptance": {"require_http_harness": True}
                }
            }
        },
    )

    assert findings == []


def test_semantic_review_merges_qa_author_semantic_conventions() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/app-api/src/test/java/com/example/web/ConfigControllerTest.java": """
            class ConfigControllerTest {
                @Test
                void callsControllerDirectly() {
                    ConfigController controller = new ConfigController();
                    controller.runtime("crypto");
                }
            }
            """
        },
        plan_text="Every /api/config route preserves domain query semantics.",
        task_text="Write endpoint route tests.",
        project_metadata={
            "qa_author": {
                "semantic_conventions": {
                    "endpoint_acceptance": {"require_http_harness": True}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_direct_spring_controller_call"
    ]


def test_semantic_review_blocks_direct_controller_calls_outside_web_package() -> None:
    path = "changed-files/app-api/src/test/java/com/example/controller/ConfigControllerTest.java"
    findings = review_semantic_quality(
        files={
            path: """
            class ConfigControllerTest {
                @Test
                void callsControllerDirectly() {
                    ConfigController controller = new ConfigController();
                    controller.runtime("crypto");
                }
            }
            """
        },
        plan_text="Every /api/config route preserves domain query semantics.",
        task_text="Write endpoint route tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "endpoint_acceptance": {"require_http_harness": True}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_direct_spring_controller_call"
    ]


def test_semantic_review_blocks_brittle_payload_string_assertions() -> None:
    path = "changed-files/app-api/src/test/java/com/example/web/DiagnosticsControllerTest.java"
    findings = review_semantic_quality(
        files={
            path: """
            class DiagnosticsControllerTest {
                @Test
                void returnsDomainPayload() throws Exception {
                    JsonNode json = objectMapper.readTree(response.getContentAsString());
                    String text = json.toString();
                    assertTrue(text.contains("crypto"));
                    assertTrue(text.contains("crypto-cycle"));
                    assertTrue(text.contains("BTC/USD"));
                    assertTrue(text.contains("BINANCE"));
                    assertFalse(text.contains("equities"));
                    assertFalse(text.contains("AAPL"));
                    assertFalse(text.contains("NASDAQ"));
                }
            }
            """
        },
        plan_text="Diagnostics endpoint payloads must be domain scoped.",
        task_text="Write endpoint payload assertions.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "payload_assertions": {
                        "prefer_structured_json_paths": True,
                        "max_raw_contains_per_file": 4,
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_brittle_payload_assertions"
    ]
    assert findings[0].details["raw_contains_count"] == 7


def test_semantic_review_allows_contains_on_explicit_textual_fields() -> None:
    path = "changed-files/app-api/src/test/java/com/example/web/DiagnosticsControllerTest.java"
    findings = review_semantic_quality(
        files={
            path: """
            class DiagnosticsControllerTest {
                @Test
                void returnsDomainPayload() throws Exception {
                    JsonNode payload = objectMapper.readTree(response.getContentAsString());
                    assertEquals("crypto", payload.path("domain").asText());
                    assertEquals("crypto-cycle", payload.path("graphId").asText());
                    assertTrue(payload.path("body").asText().contains("domain: crypto"));
                    assertTrue(payload.path("diff").asText().contains("crypto"));
                    assertTrue(payload.path("message").asText("").contains("BTC/USD"));
                }
            }
            """
        },
        plan_text="Diagnostics endpoint payloads must be domain scoped.",
        task_text="Write endpoint payload assertions.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "payload_assertions": {
                        "prefer_structured_json_paths": True,
                        "max_raw_contains_per_file": 2,
                    }
                }
            }
        },
    )

    assert findings == []


def test_semantic_review_blocks_journal_cursor_advancing_after_failed_publish() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/store/src/test/java/com/example/SnapshotRestoreAcceptanceTest.java": """
            class SnapshotRestoreAcceptanceTest {
                @Test
                void stagedButUnjournaledWriteDoesNotAdvanceCursor() {
                    store.publishAtomic(slot, 1L, okPayload);
                    try {
                        store.publishAtomic(slot, 2L, failingPayload);
                        fail("expected abort");
                    } catch (RuntimeException expected) {
                    }
                    RestoredStore restored = restore();
                    assertEquals(2L, restored.publishedSeq(slot));
                }
            }
            """
        },
        plan_text="Staged-but-unjournaled writes must not advance the journal cursor.",
        task_text="Write restore tests for aborted journal writes.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "journal": {"failed_publish_must_not_advance_cursor": True}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_journal_cursor_mismatch"
    ]
    assert findings[0].details["last_acknowledged_sequence"] == 1


def test_semantic_review_blocks_brittle_array_assertions_when_project_requires_structured() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/store/src/test/java/com/example/MemoryTest.java": """
            class MemoryTest {
                @Test
                void readsBytes() {
                    assertEquals(Arrays.toString(expected), Arrays.toString(actual));
                }
            }
            """
        },
        plan_text="Byte payloads round-trip.",
        task_text="Write byte payload tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "binary_assertions": {"prefer_assert_array_equals": True}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_brittle_array_assertion"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_blocks_warm_restore_benchmark_when_cold_restore_required() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RestoreLatencyBenchmark.java": """
            class RestoreLatencyBenchmark {
                private Store restoreStore;

                @Setup(Level.Trial)
                public void setup() {
                    restoreStore = openStore(targetPath);
                }

                @Benchmark
                public void restoreLatency() {
                    restoreStore.restore(snapshotPath);
                }
            }
            """
        },
        plan_text="Add restore latency benchmark for cold restore behavior.",
        task_text="Write JMH benchmark for restore latency.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "restore_benchmark": {
                        "cold_start_semantics": True,
                        "fresh_target_strategy": "preallocated_target_pool",
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_jmh_restore_not_cold"
    ]


def test_semantic_review_blocks_exhaustible_jmh_target_pool() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RestoreLatencyBenchmark.java": """
            class RestoreLatencyBenchmark {
                private LvcStore[] restoreTargets;
                private int restoreTargetCursor;

                @Setup(Level.Trial)
                public void setup() {
                    restoreTargets = new LvcStore[32];
                }

                @Benchmark
                public void restoreLatency() {
                    LvcStore target = nextColdRestoreTarget();
                    target.restore(snapshotPath);
                }

                private LvcStore nextColdRestoreTarget() {
                    if (restoreTargetCursor >= restoreTargets.length) {
                        throw new IllegalStateException("restore target pool exhausted");
                    }
                    return restoreTargets[restoreTargetCursor++];
                }
            }
            """
        },
        plan_text="Add restore latency benchmark for cold restore behavior.",
        task_text="Write JMH benchmark for restore latency.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "restore_benchmark": {
                        "cold_start_semantics": True,
                        "fresh_target_strategy": "preallocated_target_pool",
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_jmh_exhaustible_target_pool"
    ]


def test_semantic_review_allows_sized_single_shot_jmh_target_pool() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RestoreLatencyBenchmark.java": """
            @Warmup(iterations = 2, time = 250)
            @Measurement(iterations = 5, time = 250)
            @BenchmarkMode(Mode.SingleShotTime)
            class RestoreLatencyBenchmark {
                static class State {
                    private static final int RESTORE_TARGETS = 7;
                    private LvcStore[] restoreTargets;
                    private int restoreTargetCursor;

                    @Setup(Level.Trial)
                    public void setup() {
                        restoreTargets = new LvcStore[RESTORE_TARGETS];
                    }

                    private LvcStore nextColdRestoreTarget() {
                        if (restoreTargetCursor >= restoreTargets.length) {
                            throw new IllegalStateException("restore target pool exhausted");
                        }
                        return restoreTargets[restoreTargetCursor++];
                    }
                }

                @Benchmark
                public void restoreLatency(State state) {
                    state.nextColdRestoreTarget().restore(snapshotPath);
                }
            }
            """
        },
        plan_text="Add restore latency benchmark for cold restore behavior.",
        task_text="Write JMH benchmark for restore latency.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "restore_benchmark": {
                        "cold_start_semantics": True,
                        "fresh_target_strategy": "preallocated_target_pool",
                    }
                }
            }
        },
    )

    assert findings == []


def test_semantic_review_blocks_sample_time_restore_target_rotation_for_cold_restore() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RestoreLatencyBenchmark.java": """
            @BenchmarkMode(Mode.SampleTime)
            class RestoreLatencyBenchmark {
                static class State {
                    private LvcStore[] restoreTargets;
                    private int restoreTargetCursor;

                    @Setup(Level.Trial)
                    public void setup() {
                        restoreTargets = new LvcStore[8];
                    }

                    private LvcStore nextRestoreTarget() {
                        LvcStore restoreTarget = restoreTargets[restoreTargetCursor];
                        restoreTargetCursor =
                            (restoreTargetCursor + 1) & (restoreTargets.length - 1);
                        return restoreTarget;
                    }
                }

                @Benchmark
                public void restoreLatency(State state) {
                    state.nextRestoreTarget().restore(snapshotPath);
                }
            }
            """
        },
        plan_text="Add restore latency benchmark for cold restore behavior.",
        task_text="Write JMH benchmark for restore latency.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "restore_benchmark": {
                        "cold_start_semantics": True,
                        "fresh_target_strategy": "preallocated_target_pool",
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_jmh_restore_target_reuse"
    ]


def test_semantic_review_blocks_indirect_restore_handle_into_trial_target() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RestoreLatencyBenchmark.java": """
            @BenchmarkMode(Mode.SampleTime)
            class RestoreLatencyBenchmark {
                static class State {
                    LvcStore restoreTarget;
                    MethodHandle restoreHandle;

                    @Setup(Level.Trial)
                    public void setup() {
                        restoreTarget = openStore();
                        restoreHandle = lookup.findVirtual(LvcStore.class, "restore", type);
                    }
                }

                @Benchmark
                public void restoreLatency(State state) throws Throwable {
                    state.restoreHandle.invokeExact(state.restoreTarget, snapshotPath);
                }
            }
            """
        },
        plan_text="Add restore latency benchmark for cold restore behavior.",
        task_text="Write JMH benchmark for restore latency.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "restore_benchmark": {
                        "cold_start_semantics": True,
                        "fresh_target_strategy": "preallocated_target_pool",
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_jmh_restore_not_cold"
    ]


def test_semantic_review_warns_on_build_file_string_assertions_when_disallowed() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/store/src/test/java/com/example/BenchmarkWiringTest.java": """
            class BenchmarkWiringTest {
                @Test
                void benchmarkIsWired() throws IOException {
                    String build = Files.readString(Path.of("benchmarks/build.gradle"));
                    assertTrue(build.contains("RestoreLatencyBenchmark"));
                }
            }
            """
        },
        plan_text="Benchmark is covered.",
        task_text="Write benchmark tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "build_hook_tests": {"allow_build_file_string_assertions": False}
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_build_file_string_assertion"
    ]
    assert findings[0].severity == "warning"


def test_semantic_review_blocks_build_file_string_assertions_when_gate_required() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/store/src/test/java/com/example/BenchmarkWiringTest.java": """
            class BenchmarkWiringTest {
                @Test
                void benchmarkIsWired() throws IOException {
                    String smoke = Files.readString(Path.of("qa/smoke.sh"));
                    assertTrue(smoke.contains("jmhSmokeCheck"));
                }
            }
            """
        },
        plan_text="Benchmark gate is covered.",
        task_text="Write benchmark tests.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "build_hook_tests": {
                        "allow_build_file_string_assertions": False,
                        "deterministic_gate_validation_required": True,
                    }
                }
            }
        },
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_build_file_string_assertion"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_blocks_reflective_jmh_smoke_harness() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RangeBenchmark.java": """
            import java.lang.invoke.LambdaMetafactory;
            import org.openjdk.jmh.annotations.Benchmark;

            public class RangeBenchmark {
                @Benchmark
                public int rangeSmoke() throws Throwable {
                    return createInvoker().invoke();
                }

                private RangeInvoker createInvoker() throws Throwable {
                    return (RangeInvoker) LambdaMetafactory.metafactory(
                        null, "invoke", null, null, null, null).getTarget().invokeExact();
                }
            }
            """
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_jmh_reflective_invocation"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_blocks_missing_range_prefix_behavior() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/conformance-tests/src/test/java/RangeScanConformanceTest.java": """
            class RangeScanConformanceTest {
                @Test
                void rangeScanSemantics() {
                    store.ascendingRange(0, 7, visitor);
                    assertEquals(List.of(1, 2, 4), visited);
                }
            }
            """
        },
        plan_text="Range scans must support matching and non-matching prefix filters.",
        task_text="Write conformance tests for range prefix behavior.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_prefix_behavior_missing"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_accepts_range_prefix_behavior_coverage() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/conformance-tests/src/test/java/RangeScanConformanceTest.java": """
            class RangeScanConformanceTest {
                private static final int PREFIX_MATCH = 0x10;
                private static final int PREFIX_NON_MATCH = 0x20;
                @Test
                void rangeScanSemantics() {
                    store.ascendingRange(0, 7, PREFIX_MATCH, 4, visitor);
                    assertEquals(List.of(1, 2), visited);
                    store.ascendingRange(0, 7, PREFIX_NON_MATCH, 4, visitor);
                    assertEquals(List.of(), visited);
                }
            }
            """
        },
        plan_text="Range scans must support matching and non-matching prefix filters.",
        task_text="Write conformance tests for range prefix behavior.",
        project_metadata={},
    )

    assert not [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_prefix_behavior_missing"
    ]
