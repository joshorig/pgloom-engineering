from pathlib import Path

from pgloom_engineering.path_policy import discover_qa_write_paths, is_qa_write_path
from pgloom_engineering.planner.token_savior_context import _filter_relevant_qa_write_paths


def test_path_policy_treats_module_test_roots_as_qa_paths() -> None:
    assert is_qa_write_path("tests/")
    assert is_qa_write_path("qa/fixtures/")
    assert is_qa_write_path("app-api/src/test/")
    assert is_qa_write_path("app-api/src/test/java/")
    assert is_qa_write_path("ui/tests/e2e/")
    assert not is_qa_write_path("app-api/src/main/java/")


def test_path_policy_keeps_default_qa_roots_with_metadata_paths() -> None:
    assert is_qa_write_path(
        "qa/fixtures/r003-range-usertest-evidence.txt",
        qa_write_paths=["core/src/test/java", "benchmarks/src/jmh/java"],
    )
    assert is_qa_write_path(
        "benchmarks/src/jmh/java/",
        qa_write_paths=["benchmarks/src/jmh/java"],
    )
    assert not is_qa_write_path(
        "store/src/main/java/",
        qa_write_paths=["core/src/test/java", "benchmarks/src/jmh/java"],
    )


def test_discover_qa_write_paths_finds_project_test_roots(tmp_path: Path) -> None:
    tmp_path.joinpath("app-api/src/test/java").mkdir(parents=True)
    tmp_path.joinpath("ui/tests/e2e").mkdir(parents=True)

    paths = discover_qa_write_paths(tmp_path)

    assert "tests/" in paths
    assert "qa/fixtures/" in paths
    assert "app-api/src/test/" in paths
    assert "app-api/src/test/java/" in paths
    assert "ui/tests/" in paths


def test_dag_config_query_keeps_dag_qa_roots() -> None:
    paths = [
        "tests/",
        "qa/fixtures/",
        "app-api/src/test/",
        "dag-framework-api/src/test/",
        "runtime-core/src/test/",
        "benchmarks/src/test/",
    ]
    relevant_paths = [
        "dag-framework-api/src/main/java/com/example/BackpressurePolicy.java",
        "runtime-core/src/main/java/com/example/GraphPartitionRunner.java",
    ]

    filtered = _filter_relevant_qa_write_paths(
        paths,
        query="Add per-edge config for a backpressure policy selector.",
        relevant_paths=relevant_paths,
    )

    assert "dag-framework-api/src/test/" in filtered
    assert "runtime-core/src/test/" in filtered
    assert "benchmarks/src/test/" in filtered
    assert "app-api/src/test/" not in filtered
