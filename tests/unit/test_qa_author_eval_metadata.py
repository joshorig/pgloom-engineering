from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

from pgloom_engineering.qa_author_runtime import qa_quality_repair_file_set
from pgloom_engineering.qa_runtime import validate_required_qa_gates


def test_qa_author_eval_brief_uses_project_metadata(tmp_path: Path) -> None:
    module = _load_eval_module()
    worktree = tmp_path
    worktree.joinpath("custom/src").mkdir(parents=True)
    worktree.joinpath("custom/tests").mkdir(parents=True)
    worktree.joinpath("custom/src/controller.py").write_text(
        'ROUTES = ["/api/widgets", "/api/widgets/promote"]\n',
        encoding="utf-8",
    )
    worktree.joinpath("custom/tests/test_existing_widget.py").write_text(
        "def test_existing_widget():\n    pass\n",
        encoding="utf-8",
    )

    brief = module._qa_author_brief(
        worktree,
        module._fixture_plan(),
        module._fixture_task_contract(),
        project_metadata={
            "qa": {
                "source_roots": ["custom/src"],
                "test_roots": ["custom/tests"],
                "example_tests": ["custom/tests/test_existing_widget.py"],
                "quality_gates": ["Prefer endpoint tests over service-only tests."],
                "avoid_patterns": ["mock-only browser assertions"],
            }
        },
    )

    assert brief["project_qa_metadata"]["source_roots"] == ["custom/src"]
    assert "Prefer endpoint tests over service-only tests." in brief["quality_gates"]
    assert "Avoid: mock-only browser assertions" in brief["quality_gates"]
    assert "custom/tests/test_existing_widget.py" in brief["existing_test_examples"]


def test_qa_author_eval_brief_includes_route_coverage_requirements(tmp_path: Path) -> None:
    module = _load_eval_module()
    worktree = tmp_path
    worktree.joinpath("api/src/main/java/example").mkdir(parents=True)
    worktree.joinpath("api/src/main/java/example/DiagnosticsController.java").write_text(
        "\n".join(
            [
                "class DiagnosticsController {",
                '  @GetMapping(value = "/api/diagnostics/overview")',
                "  Object overview() { return null; }",
                '  @GetMapping(value = "/api/diagnostics/services")',
                "  Object services() { return null; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    plan = module._fixture_plan().model_copy(
        update={
            "problem_statement": "Cover every existing /api/diagnostics/* route.",
            "acceptance_test_matrix": [
                "Every existing /api/diagnostics/* route is exercised for both domains."
            ],
        }
    )
    task = module._fixture_task_contract().model_copy(
        update={"objective": "Write endpoint red tests for /api/diagnostics routes."}
    )

    brief = module._qa_author_brief(
        worktree,
        plan,
        task,
        project_metadata={
            "qa": {
                "endpoint_roots": ["api/src/main/java"],
                "test_roots": ["api/src/test/java"],
            }
        },
    )

    assert brief["endpoint_inventory"] == [
        "GET /api/diagnostics/overview (api/src/main/java/example/DiagnosticsController.java)",
        "GET /api/diagnostics/services (api/src/main/java/example/DiagnosticsController.java)",
    ]
    assert brief["route_coverage_requirements"][0]["coverage_rule"] == "all_routes"
    assert brief["route_coverage_requirements"][0]["required_routes"] == brief["endpoint_inventory"]
    skeleton = brief["deterministic_test_skeleton"]
    assert skeleton["required_domains"] == ["default"]
    assert skeleton["endpoint_behavior_skeleton"][0]["route_cases"] == [
        {
            "method": "GET",
            "path": "/api/diagnostics/overview",
            "behavior_requirement": (
                "Invoke the matching controller/HTTP route for each required domain "
                "and assert domain-specific identifiers/config/partition/service state."
            ),
        },
        {
            "method": "GET",
            "path": "/api/diagnostics/services",
            "behavior_requirement": (
                "Invoke the matching controller/HTTP route for each required domain "
                "and assert domain-specific identifiers/config/partition/service state."
            ),
        },
    ]


def test_route_inventory_only_blocks_are_ignored_for_behavior_references() -> None:
    module = _load_eval_module()
    text = """
    @Test
    void diagnosticsRouteInventoryIsCovered() {
        final List<String> routes = List.of(
            "GET /api/diagnostics/overview",
            "GET /api/diagnostics/services"
        );
        assertEquals(2, routes.size());
    }

    @Test
    void callsDiagnosticsOverviewOnly() {
        controller.diagnosticsOverview("crypto");
    }
    """

    stripped = module._without_inventory_only_blocks({"Test.java": text})

    assert module._route_path_referenced("/api/diagnostics/overview", stripped)
    assert not module._route_path_referenced("/api/diagnostics/services", stripped)


def test_direct_spring_controller_endpoint_tests_warn_for_harness_preference() -> None:
    module = _load_eval_module()
    code_files = {
        "changed-files/app-api/src/test/java/com/example/web/ConfigControllerTest.java": """
        class ConfigControllerTest {
            @Test
            void callsControllerDirectly() {
                ConfigController controller = new ConfigController();
                ResponseEntity<?> response = controller.runtime("crypto");
                assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
            }
        }
        """,
    }

    assert module._java_spring_endpoint_harness_preferred(
        code_files,
        list(code_files),
    )


def test_mockmvc_endpoint_tests_do_not_warn_for_harness_preference() -> None:
    module = _load_eval_module()
    code_files = {
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
        """,
    }

    assert not module._java_spring_endpoint_harness_preferred(
        code_files,
        list(code_files),
    )


def test_unconsumed_generated_fixtures_are_flagged() -> None:
    module = _load_eval_module()
    files = {
        "changed-files/qa/fixtures/domain-parity-metadata.json": "{}",
        "changed-files/app-api/src/test/java/com/example/web/ConfigControllerTest.java": """
        class ConfigControllerTest {
            @Test
            void callsRoute() {
                assertThat(controller.runtime("crypto")).isNotNull();
            }
        }
        """,
    }

    assert module._unconsumed_generated_fixtures(files) == [
        "changed-files/qa/fixtures/domain-parity-metadata.json"
    ]


def test_consumed_generated_fixtures_are_allowed() -> None:
    module = _load_eval_module()
    files = {
        "changed-files/qa/fixtures/graph-yaml/five-node-graph.yaml": "nodes: []",
        "changed-files/runtime-core/src/test/java/com/example/GraphYamlLoaderTest.java": """
        class GraphYamlLoaderTest {
            @Test
            void loadsFixture() {
                Path fixture = Path.of("qa/fixtures/graph-yaml/five-node-graph.yaml");
                assertThat(loader.load(fixture)).isNotNull();
            }
        }
        """,
    }

    assert module._unconsumed_generated_fixtures(files) == []


def test_jmh_benchmark_methods_with_setup_garbage_are_flagged() -> None:
    module = _load_eval_module()
    text = """
    class RestoreBenchmark {
        @Setup
        public void setup() {
            fixture = new RestoreFixture();
        }

        @Benchmark
        public void restoreLatency() {
            Path snapshot = Path.of("snapshot.bin");
            store.restore(snapshot);
        }
    }
    """

    assert module._jmh_benchmark_methods_with_setup_garbage(text) == ["restoreLatency"]


def test_jmh_benchmark_methods_can_use_setup_state_without_garbage() -> None:
    module = _load_eval_module()
    text = """
    class RestoreBenchmark {
        @Setup
        public void setup() {
            snapshot = preparedSnapshot();
        }

        @Benchmark
        public void restoreLatency() {
            store.restore(snapshot);
        }
    }
    """

    assert module._jmh_benchmark_methods_with_setup_garbage(text) == []


def test_jmh_benchmark_mode_annotation_does_not_scan_whole_class() -> None:
    module = _load_eval_module()
    text = """
    @BenchmarkMode(Mode.SampleTime)
    public class RestoreBenchmark {
        @Setup
        public void setup() {
            fixture = new RestoreFixture();
        }

        @Benchmark
        public void restoreLatency() {
            store.restore(snapshot);
        }
    }
    """

    assert module._jmh_benchmark_methods_with_setup_garbage(text) == []


def test_jmh_invocation_setup_is_flagged_as_post_setup_garbage_risk() -> None:
    module = _load_eval_module()
    text = """
    class RestoreBenchmark {
        @Setup(Level.Invocation)
        public void setupInvocation() {
            restoreTarget = openStore();
        }

        @Benchmark
        public void restoreLatency() {
            store.restore(snapshot);
        }
    }
    """

    assert module._jmh_benchmark_methods_with_setup_garbage(text) == [
        "setupInvocation setup"
    ]


def test_benchmark_variant_gap_is_flagged_when_acceptance_names_variants() -> None:
    module = _load_eval_module()
    plan = module._fixture_plan().model_copy(
        update={
            "problem_statement": "Benchmark direct and mmap SINGLE and DOUBLE restore.",
            "acceptance_test_matrix": [
                "Performance coverage spans SINGLE, DOUBLE, direct, and mmap variants."
            ],
        }
    )
    task = module._fixture_task_contract().model_copy(
        update={
            "objective": "Add JMH benchmark for direct and mmap restore latency.",
            "expected_outputs": ["JmhBenchmark"],
        }
    )

    findings = module._benchmark_variant_findings(
        {
            "changed-files/benchmarks/src/jmh/java/RestoreBenchmark.java": (
                "@Benchmark void directSingle() {}"
            )
        },
        plan,
        task,
    )

    assert findings[0]["code"] == "qa_review_benchmark_variant_gap"


def test_playwright_request_capture_is_not_mocked_only() -> None:
    module = _load_eval_module()
    text = """
    test('domain switch', async ({ page }) => {
      const domains = []
      await page.route('**/api/config/runtime**', async (route) => {
        const url = new URL(route.request().url())
        domains.push(url.searchParams.get('domain'))
        await route.fulfill({ json: {} })
      })
      await page.route('**/api/config/access**', async (route) => route.fulfill({ json: {} }))
      await page.route('**/api/diagnostics/services**', async (route) => {
        route.fulfill({ json: {} })
      })
      await expect.poll(() => domains.at(-1)).toBe('crypto')
    })
    """

    assert not module._playwright_is_fully_route_mocked(text)


def test_ui_quality_flags_broad_existing_flow_when_metadata_prefers_focused_spec() -> None:
    module = _load_eval_module()
    findings = module._ui_quality_findings(
        {
            "changed-files/ui/tests/e2e/flows.spec.ts": """
            test('one', async ({ page }) => {})
            test('two', async ({ page }) => {})
            test('three', async ({ page }) => {})
            test('four', async ({ page }) => {})
            """
        },
        {"qa": {"ui_acceptance": {"prefer_task_specific_spec": True}}},
    )

    assert findings[0]["code"] == "qa_review_broad_existing_ui_spec_modified"


def test_quality_repair_file_set_extracts_artifact_paths() -> None:
    files = qa_quality_repair_file_set(
        {
            "blocking_findings": [
                {
                    "code": "qa_review_benchmark_allocates_after_setup",
                    "file": "changed-files/benchmarks/src/jmh/java/RestoreBenchmark.java",
                },
                {
                    "code": "qa_review_broad_existing_ui_spec_modified",
                    "files": ["changed-files/ui/tests/e2e/flows.spec.ts"],
                },
                {
                    "code": "qa_review_benchmark_variant_gap",
                    "benchmark_files": [
                        "changed-files/benchmarks/src/jmh/java/OtherBenchmark.java"
                    ],
                },
            ]
        },
        ["tests/fallback.py"],
    )

    assert files == [
        "benchmarks/src/jmh/java/OtherBenchmark.java",
        "benchmarks/src/jmh/java/RestoreBenchmark.java",
        "ui/tests/e2e/flows.spec.ts",
    ]


def test_archive_changed_files_diff_includes_untracked_files(tmp_path: Path) -> None:
    module = _load_eval_module()
    repo = tmp_path / "repo"
    output_dir = tmp_path / "out"
    repo.mkdir()
    output_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=repo, check=True)
    repo.joinpath("tracked.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    repo.joinpath("tracked.txt").write_text("new\n", encoding="utf-8")
    repo.joinpath("tests").mkdir()
    repo.joinpath("tests/test_new.py").write_text(
        "def test_new():\n    assert True\n",
        encoding="utf-8",
    )

    artifacts = module._archive_changed_files(
        output_dir=output_dir,
        worktree=repo,
        changed_files=["tracked.txt", "tests/test_new.py"],
    )

    diff = output_dir.joinpath(artifacts["diff"]).read_text(encoding="utf-8")
    assert "diff --git a/tracked.txt b/tracked.txt" in diff
    assert "diff --git a/tests/test_new.py b/tests/test_new.py" in diff
    assert "new file mode" in diff
    assert sorted(artifacts["changed_files"]) == [
        "changed-files/tests/test_new.py",
        "changed-files/tracked.txt",
    ]


def test_generated_tool_artifacts_are_excluded_from_changed_files() -> None:
    from pgloom_engineering.qa_runtime import is_generated_tool_artifact

    assert is_generated_tool_artifact("test-results/.last-run.json")
    assert is_generated_tool_artifact("ui/playwright-report/index.html")
    assert is_generated_tool_artifact(".gradle/file-system.probe")
    assert is_generated_tool_artifact(".gradle-home/wrapper/dists/gradle.zip.part")
    assert is_generated_tool_artifact(".gradle-user-home/wrapper/dists/gradle.zip.part")
    assert not is_generated_tool_artifact("ui/tests/e2e/domain-switch.spec.ts")
    assert not is_generated_tool_artifact(
        "app-api/src/test/java/com/example/AcceptanceTest.java"
    )


def test_contract_repairable_handles_invalid_contract_even_without_red_exit_code() -> None:
    module = _load_eval_module()

    assert module._contract_repairable(
        {
            "findings": [{"code": "invalid_qa_author_contract"}],
            "changed_files": ["app-api/src/test/java/com/example/AcceptanceTest.java"],
            "pytest_exit_code": 0,
        }
    )


def test_qa_code_repairable_handles_non_red_test_failures() -> None:
    module = _load_eval_module()

    assert module.qa_code_repairable(
        {
            "findings": [
                {"code": "tests_not_red"},
                {"code": "missing_matrix_coverage"},
            ],
            "changed_files": ["tests/test_feature.py"],
        }
    )
    assert module.qa_code_repairable(
        {
            "findings": [{"code": "qa_tests_do_not_compile"}],
            "changed_files": ["tests/test_feature.py"],
        }
    )
    assert not module.qa_code_repairable(
        {
            "findings": [{"code": "tests_not_red"}, {"code": "verification_infra_error"}],
            "changed_files": ["tests/test_feature.py"],
        }
    )
    assert not module.qa_code_repairable(
        {
            "findings": [
                {"code": "tests_not_red"},
                {"path": "src/main/java/App.java", "reason": "not_a_qa_write_path"},
            ],
            "changed_files": ["src/main/java/App.java"],
        }
    )


def test_repair_state_is_preserved_across_replaced_outcomes() -> None:
    module = _load_eval_module()
    state = {
        "red_repair_attempted": True,
        "repair_attempted": False,
        "quality_repair_attempted": False,
    }
    outcome = {"verdict": "revise"}

    module._apply_repair_state(outcome, state)
    assert outcome["red_repair_attempted"]

    next_outcome = {"verdict": "accept"}
    state["repair_attempted"] = True
    module._apply_repair_state(next_outcome, state)

    assert next_outcome["red_repair_attempted"]
    assert next_outcome["repair_attempted"]
    assert not next_outcome["quality_repair_attempted"]


def test_qa_code_repair_prompt_includes_verification_failure_and_file_contents(
    tmp_path: Path,
) -> None:
    module = _load_eval_module()
    tmp_path.joinpath("tests").mkdir()
    tmp_path.joinpath("tests/test_feature.py").write_text(
        "def test_feature():\n    missing import\n",
        encoding="utf-8",
    )

    prompt = module.build_qa_code_repair_prompt(
        plan=module._fixture_plan(),
        task_contract=module._fixture_task_contract(),
        worktree=tmp_path,
        changed_files=["tests/test_feature.py"],
        verification_command=["pytest", "tests/test_feature.py"],
        stdout_excerpt="SyntaxError",
        stderr_excerpt="invalid syntax",
        current_contract={"tests_added": ["tests/test_feature.py"]},
    )

    assert "Compile errors, import errors, syntax errors" in prompt
    assert "Run the narrowest available compile/test command" in prompt
    assert "qa_tests_do_not_compile" in prompt
    assert "pytest" in prompt
    assert "invalid syntax" in prompt
    assert "def test_feature" in prompt


def test_matrix_coverage_alignment_expands_unambiguous_short_keys() -> None:
    module = _load_eval_module()
    criterion = (
        "Config endpoint semantic coverage: every existing /api/config/* route is exercised."
    )
    contract = module.QAAuthorContract.model_validate(
        {
            "contract_version": "engineering.contracts.v1",
            "feature_id": "F-1",
            "task_id": "T-1",
            "tests_added": ["tests/test_feature.py"],
            "paths_touched": ["tests/test_feature.py"],
            "matrix_coverage": {
                "Config endpoint semantic coverage": ["test_config_routes"],
            },
            "red_proof": [],
        }
    )

    aligned = module._align_matrix_coverage_to_acceptance(contract, [criterion])

    assert aligned.matrix_coverage == {criterion: ["test_config_routes"]}


def test_qa_author_payload_normalizes_matrix_coverage_strings() -> None:
    module = _load_eval_module()

    payload = module._qa_author_payload(
        {
            "QAAuthorContract": {
                "contract_version": "engineering.contracts.v1",
                "feature_id": "F-1",
                "task_id": "T-1",
                "tests_added": "tests/test_feature.py",
                "paths_touched": "tests/test_feature.py",
                "matrix_coverage": {"criterion": "tests/test_feature.py::test_feature"},
                "red_proof": [],
                "model_usage_ids": "usage-1",
            }
        }
    )

    assert payload["tests_added"] == ["tests/test_feature.py"]
    assert payload["paths_touched"] == ["tests/test_feature.py"]
    assert payload["matrix_coverage"] == {"criterion": ["tests/test_feature.py::test_feature"]}
    assert payload["model_usage_ids"] == ["usage-1"]


def test_required_qa_gates_are_validated_deterministically(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/smoke.sh").write_text(
        "./gradlew :benchmarks:jmhSmokeCheck\n"
        "grep gc.alloc.rate.norm build/reports/jmh/results.txt\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("qa/regression.sh").write_text(
        "./gradlew test :benchmarks:jmh\n",
        encoding="utf-8",
    )

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["./qa/smoke.sh"],
                        "must_cover": ["allocation", "benchmark_smoke"],
                    },
                    {
                        "id": "regression",
                        "command": ["./qa/regression.sh"],
                        "must_cover": ["benchmark_full", "unit_regression"],
                    },
                ]
            }
        },
    )

    assert [item["status"] for item in validation] == ["configured", "configured"]
    assert validation[0]["evidence"] == [
        "command_file:qa/smoke.sh",
        "covers:allocation",
        "covers:benchmark_smoke",
    ]


def test_required_qa_gate_validation_reports_missing_coverage(tmp_path: Path) -> None:
    tmp_path.joinpath("qa").mkdir()
    tmp_path.joinpath("qa/smoke.sh").write_text("./gradlew test\n", encoding="utf-8")

    validation = validate_required_qa_gates(
        tmp_path,
        {
            "qa": {
                "required_gates": [
                    {
                        "id": "smoke",
                        "command": ["./qa/smoke.sh"],
                        "must_cover": ["allocation"],
                    }
                ]
            }
        },
    )

    assert validation[0]["status"] == "missing"
    assert validation[0]["missing"] == ["coverage:allocation"]


def _load_eval_module() -> Any:
    path = Path(__file__).parents[2] / "scripts" / "run_qa_author_eval.py"
    spec = importlib.util.spec_from_file_location("run_qa_author_eval", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
