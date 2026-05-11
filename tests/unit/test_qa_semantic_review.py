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


def test_semantic_review_blocks_range_benchmark_that_uses_read_slice_pooled() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private LvcStore store;

                @Benchmark
                public int rangeSmoke() {
                    int matches = 0;
                    for (int slot = 0; slot < 1024; slot++) {
                        try (LvcStore.ReadOnlySlice slice = store.readSlicePooled(slot)) {
                            matches += slice.length();
                        }
                    }
                    return matches;
                }
            }
            """
        },
        plan_text="Range benchmark smoke must prove StoreVisitor allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_benchmark_not_public_api"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_accepts_range_benchmark_that_calls_public_range_api() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;

                @Benchmark
                public void rangeSmoke() {
                    store.ascendingRange(0, 1023, visitor);
                }
            }
            """
        },
        plan_text="Range benchmark smoke must prove StoreVisitor allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding for finding in findings
        if finding.code == "qa_semantic_range_benchmark_not_public_api"
    ] == []


def test_semantic_review_blocks_ascending_only_range_benchmark() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;

                @Benchmark
                public void rangeSmoke() {
                    store.ascendingRange(0, 1023, visitor);
                }
            }
            """
        },
        plan_text=(
            "Range benchmark smoke must prove ascending, descending, and prefix range "
            "visitor behavior."
        ),
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_benchmark_behavior_gap"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_accepts_range_benchmark_with_descending_and_prefix() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;

                @Benchmark
                public void ascendingRangeSmoke() {
                    store.ascendingRange(0, 1023, visitor);
                }

                @Benchmark
                public void descendingRangeSmoke() {
                    store.descendingRange(1023, 0, visitor);
                }

                @Benchmark
                public void prefixRangeSmoke() {
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """
        },
        plan_text=(
            "Range benchmark smoke must prove ascending, descending, and prefix range "
            "visitor behavior."
        ),
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_benchmark_behavior_gap"
    ] == []


def test_semantic_review_blocks_too_strict_range_benchmark_smoke_threshold() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/build.gradle": """
            tasks.register('jmhSmokeCheck') {
                def smokeBenchmarkThresholds = [
                    'com.example.CiSmokeBenchmark.rangeScanSmoke': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.005d,
                        gcCount: 0.000d
                    ]
                ]
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;
                public void smoke() {
                    store.ascendingRange(0, 1023, visitor);
                    store.descendingRange(1023, 0, visitor);
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding.code
        for finding in findings
        if finding.code == "qa_semantic_range_benchmark_smoke_threshold_too_strict"
    ] == ["qa_semantic_range_benchmark_smoke_threshold_too_strict"]
    assert findings[0].severity == "blocking"


def test_semantic_review_accepts_range_benchmark_smoke_threshold_with_noise_margin() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/build.gradle": """
            tasks.register('jmhSmokeCheck') {
                def smokeBenchmarkThresholds = [
                    'com.example.CiSmokeBenchmark.rangeScanSmoke': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.050d,
                        gcCount: 0.000d
                    ]
                ]
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;
                public void smoke() {
                    store.ascendingRange(0, 1023, visitor);
                    store.descendingRange(1023, 0, visitor);
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_benchmark_smoke_threshold_too_strict"
    ] == []


def test_semantic_review_blocks_relaxing_existing_ci_smoke_thresholds() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/build.gradle": """
            tasks.register('jmhSmokeCheck') {
                def smokeBenchmarkThresholds = [
                    'com.joshorig.ull.lvc.bench.CiSmokeBenchmark.pollerBitsetSmoke': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.050d,
                        gcCount: 0.000d
                    ],
                    'com.joshorig.ull.lvc.bench.RangeScanBenchmark.ascendingRange': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.050d,
                        gcCount: 0.000d
                    ]
                ]
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;
                public void smoke() {
                    store.ascendingRange(0, 1023, visitor);
                    store.descendingRange(1023, 0, visitor);
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    relaxed = [
        finding
        for finding in findings
        if finding.code == "qa_semantic_existing_smoke_threshold_relaxed"
    ]
    assert len(relaxed) == 1
    assert relaxed[0].severity == "blocking"
    assert relaxed[0].details["benchmark"].endswith("CiSmokeBenchmark.pollerBitsetSmoke")


def test_semantic_review_allows_range_only_smoke_threshold_noise_margin() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/build.gradle": """
            tasks.register('jmhSmokeCheck') {
                def smokeBenchmarkThresholds = [
                    'com.joshorig.ull.lvc.bench.CiSmokeBenchmark.pollerBitsetSmoke': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.005d,
                        gcCount: 0.000d
                    ],
                    'com.joshorig.ull.lvc.bench.RangeScanBenchmark.ascendingRange': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.050d,
                        gcCount: 0.000d
                    ]
                ]
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;
                public void smoke() {
                    store.ascendingRange(0, 1023, visitor);
                    store.descendingRange(1023, 0, visitor);
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_existing_smoke_threshold_relaxed"
    ] == []
    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_benchmark_smoke_threshold_too_strict"
    ] == []


def test_semantic_review_blocks_payload_prefix_when_key_prefix_required() -> None:
    conformance_path = (
        "changed-files/conformance-tests/src/test/java/com/example/"
        "RangeScanConformanceTest.java"
    )
    findings = review_semantic_quality(
        files={
            conformance_path: """
            class RangeScanConformanceTest {
                private static final byte[] PREFIX_MATCH = new byte[] {0x11, 0x22};

                void prefixFilterMatchesAndRejectsKeysForStores() {
                    Map<Integer, byte[]> expectedPayloads = seedStore(store);
                    assertEntriesEqual(List.of(entry(1, expectedPayloads.get(1))),
                        collectAscending(store, 0, 7, PREFIX_MATCH));
                }

                private static byte[] payloadFor(int slot) {
                    byte[] payload = new byte[16];
                    byte[] prefix = slot <= 2 ? PREFIX_MATCH : new byte[] {0x33, 0x44};
                    payload[0] = prefix[0];
                    payload[1] = prefix[1];
                    payload[2] = (byte) slot;
                    return payload;
                }
            }
            """,
        },
        plan_text="R-003 requires optional key-prefix filtering for range scans.",
        task_text="Write QA tests for ascendingRange prefix behavior.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "range_prefix_behavior": {
                        "required": True,
                        "key_prefix_filter_required": True,
                    }
                }
            }
        },
    )

    key_prefix = [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_key_prefix_not_payload_prefix"
    ]
    assert len(key_prefix) == 1
    assert key_prefix[0].severity == "blocking"
    assert key_prefix[0].details["payload_prefix_seed_detected"] is True


def test_semantic_review_accepts_explicit_logical_key_prefix_coverage() -> None:
    conformance_path = (
        "changed-files/conformance-tests/src/test/java/com/example/"
        "RangeScanConformanceTest.java"
    )
    findings = review_semantic_quality(
        files={
            conformance_path: """
            class RangeScanConformanceTest {
                void keyPrefixFilterUsesLogicalKeyMapping() {
                    KeyIndex index = new FixedOneDimIndex(16);
                    GenericLvc lvc = GenericLvcFactory.newGenericLvc(store, index);
                    writeLogicalKey(lvc, "AB-001", payloadOne);
                    writeLogicalKey(lvc, "CD-001", payloadTwo);
                    assertVisitedKeys(List.of("AB-001"),
                        collectAscendingByKeyPrefix(lvc, 0, 15, keyBytes("AB")));
                }
            }
            """,
        },
        plan_text="R-003 requires optional key-prefix filtering for range scans.",
        task_text="Write QA tests for ascendingRange prefix behavior.",
        project_metadata={
            "qa": {
                "semantic_conventions": {
                    "range_prefix_behavior": {
                        "required": True,
                        "key_prefix_filter_required": True,
                    }
                }
            }
        },
    )

    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_range_key_prefix_not_payload_prefix"
    ] == []


def test_semantic_review_blocks_too_strict_range_benchmark_class_threshold() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/benchmarks/build.gradle": """
            tasks.register('jmhSmokeCheck') {
                def smokeBenchmarkThresholds = [
                    'com.example.RangeScanBenchmark.ascendingRange': [
                        allocBytesPerOp: smokeAllocThresholdBytesPerOp ?: 0.005d,
                        gcCount: 0.000d
                    ]
                ]
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            class RangeScanBenchmark {
                private LvcStore store;
                private StoreVisitor visitor;
                private byte[] prefix;
                public void smoke() {
                    store.ascendingRange(0, 1023, visitor);
                    store.descendingRange(1023, 0, visitor);
                    store.ascendingRange(0, 1023, prefix, visitor);
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove allocation behavior.",
        task_text="Write JMH benchmark smoke for range scans.",
        project_metadata={},
    )

    assert [
        finding.code
        for finding in findings
        if finding.code == "qa_semantic_range_benchmark_smoke_threshold_too_strict"
    ] == ["qa_semantic_range_benchmark_smoke_threshold_too_strict"]


def test_semantic_review_blocks_benchmark_store_visitor_signature_mismatch() -> None:
    findings = review_semantic_quality(
        files={
            "core/src/main/java/com/example/StoreVisitor.java": """
            package com.example;
            public interface StoreVisitor {
                void visit(int slotId, DirectBuffer payload, int offset, int length);
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private StoreVisitor visitor;

                void setup() {
                    visitor = this::recordPayload;
                }

                @Benchmark
                public void ascendingRangeSmoke() {
                    store.ascendingRange(0, 31, visitor);
                }

                private void recordPayload(int slotId, DirectBuffer payload) {
                    checksum += slotId + payload.capacity();
                }
            }
            """,
        },
        plan_text="Range benchmark smoke must prove StoreVisitor allocation behavior.",
        task_text="Write JMH benchmark smoke for StoreVisitor range scans.",
        project_metadata={},
    )

    mismatch = [
        finding
        for finding in findings
        if finding.code == "qa_semantic_benchmark_visitor_signature_mismatch"
    ]
    assert len(mismatch) == 1
    assert mismatch[0].severity == "blocking"
    assert mismatch[0].details["method_reference"] == "recordPayload"
    assert mismatch[0].details["expected_signature"] == [
        "int",
        "DirectBuffer",
        "int",
        "int",
    ]
    assert mismatch[0].details["actual_signature"] == ["int", "DirectBuffer"]


def test_semantic_review_accepts_matching_benchmark_store_visitor_signature() -> None:
    findings = review_semantic_quality(
        files={
            "core/src/main/java/com/example/StoreVisitor.java": """
            package com.example;
            public interface StoreVisitor {
                void visit(int slotId, DirectBuffer payload, int offset, int length);
            }
            """,
            "changed-files/benchmarks/src/jmh/java/com/example/RangeScanBenchmark.java": """
            import org.openjdk.jmh.annotations.Benchmark;

            class RangeScanBenchmark {
                private StoreVisitor visitor;
                private byte[] prefix;

                void setup() {
                    visitor = this::recordPayload;
                }

                @Benchmark
                public void ascendingRangeSmoke() {
                    store.ascendingRange(0, 31, visitor);
                }

                @Benchmark
                public void descendingRangeSmoke() {
                    store.descendingRange(31, 0, visitor);
                }

                @Benchmark
                public void prefixRangeSmoke() {
                    store.ascendingRange(0, 31, prefix, visitor);
                }

                private void recordPayload(
                        int slotId,
                        DirectBuffer payload,
                        int offset,
                        int length) {
                    checksum += slotId + payload.getByte(offset) + length;
                }
            }
            """,
        },
        plan_text=(
            "Range benchmark smoke must prove ascending, descending, prefix, and "
            "StoreVisitor allocation behavior."
        ),
        task_text="Write JMH benchmark smoke for StoreVisitor range scans.",
        project_metadata={},
    )

    assert [
        finding
        for finding in findings
        if finding.code == "qa_semantic_benchmark_visitor_signature_mismatch"
    ] == []


def test_semantic_review_blocks_reflective_range_api_tests() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/core/src/test/java/com/example/RangeScanApiTest.java": """
            import java.lang.reflect.InvocationHandler;
            import java.lang.reflect.Method;
            import java.lang.reflect.Proxy;

            class RangeScanApiTest {
                @Test
                void rangeApiShape() throws Exception {
                    Class<?> visitorType = Class.forName("com.example.StoreVisitor");
                    Method ascending = LvcStore.class.getMethod(
                        "ascendingRange", int.class, int.class, visitorType);
                    Object visitor = Proxy.newProxyInstance(
                        visitorType.getClassLoader(),
                        new Class<?>[]{visitorType},
                        (InvocationHandler) (proxy, method, args) -> null);
                    ascending.invoke(store, 0, 31, visitor);
                }
            }
            """
        },
        plan_text="Range scans expose a StoreVisitor public API.",
        task_text="Write range API acceptance tests.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_test_reflective_api"
    ]
    assert findings[0].severity == "blocking"


def test_semantic_review_blocks_reflective_range_signature_tests() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/core/src/test/java/com/example/RangeScanApiTest.java": """
            import java.lang.reflect.Method;
            import java.lang.reflect.Modifier;

            class RangeScanApiTest {
                @Test
                void rangeApiShape() throws Exception {
                    Method[] methods = StoreVisitor.class.getDeclaredMethods();
                    assertTrue(Modifier.isAbstract(methods[0].getModifiers()));
                    assertArrayEquals(
                        new Class<?>[] {int.class, DirectBuffer.class, int.class, int.class},
                        methods[0].getParameterTypes());
                }
            }
            """
        },
        plan_text="Range scans expose a StoreVisitor public API.",
        task_text="Write range API acceptance tests.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_test_reflective_api"
    ]
    assert findings[0].line is not None


def test_semantic_review_accepts_typed_range_api_tests() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/core/src/test/java/com/example/RangeScanApiTest.java": """
            class RangeScanApiTest {
                @Test
                void rangeApiShape() {
                    StoreVisitor visitor = slotId -> visited.add(slotId);
                    store.ascendingRange(0, 31, visitor);
                    assertEquals(List.of(1, 7, 11), visited);
                }
            }
            """
        },
        plan_text="Range scans expose a StoreVisitor public API.",
        task_text="Write range API acceptance tests.",
        project_metadata={},
    )

    assert [
        finding for finding in findings
        if finding.code == "qa_semantic_range_test_reflective_api"
    ] == []


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


def test_semantic_review_blocks_missing_range_prefix_behavior_case_insensitive_context() -> None:
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
        plan_text="Range scans include optional Key-Prefix filtering.",
        task_text="Write conformance tests for StoreVisitor behavior.",
        project_metadata={},
    )

    assert [finding.code for finding in findings] == [
        "qa_semantic_range_prefix_behavior_missing"
    ]


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


def test_semantic_review_accepts_range_prefix_miss_wording() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/conformance-tests/src/test/java/RangeScanConformanceTest.java": """
            class RangeScanConformanceTest {
                private static final byte[] PREFIX_MATCH = new byte[] {1};
                private static final byte[] PREFIX_MISS = new byte[] {2};
                @Test
                void rangeScanSemantics() {
                    store.ascendingRange(0, 7, PREFIX_MATCH, visitor);
                    assertEntriesEqual("ascending prefix match", expected, actual);
                    store.ascendingRange(0, 7, PREFIX_MISS, visitor);
                    assertEntriesEqual("ascending prefix miss", List.of(), actual);
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


def test_semantic_review_accepts_range_prefix_skip_nonmatch_helper() -> None:
    findings = review_semantic_quality(
        files={
            "changed-files/conformance-tests/src/test/java/RangeScanConformanceTest.java": """
            class RangeScanConformanceTest {
                @Test
                void prefixFilterMatchesAndSkipsOnSingleStores() {
                    UnsafeBuffer matchingPrefix = prefixFor(payloadFor(4), 2);
                    UnsafeBuffer missingPrefix = new UnsafeBuffer(new byte[] {99, 100});
                    assertPrefixMatches(store, matchingPrefix, expected);
                    assertPrefixSkipsNonMatches(store, missingPrefix);
                }
                private static void assertPrefixMatches(
                    LvcStore store, DirectBuffer prefix, List<Entry> expected
                ) {
                    store.ascendingRange(1, 5, prefix, 0, 2, visitor);
                    assertEntriesEqual(expected, visited);
                }
                private static void assertPrefixSkipsNonMatches(
                    LvcStore store, DirectBuffer prefix
                ) {
                    store.ascendingRange(1, 5, prefix, 0, 2, visitor);
                    assertEntriesEqual(List.of(), visited);
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
