from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def main() -> int:
    args = _parse_args()
    suite_path = Path(args.suite)
    suite = _read_json(suite_path)
    output_root = Path(args.output_dir or _default_output_dir(suite))
    output_root.mkdir(parents=True, exist_ok=True)
    selected_runs = _selected_runs(suite, args, output_root)
    run_dirs = [run["run_dir"] for run in selected_runs]
    results: list[dict[str, Any]] = []
    if args.dry_run:
        results = [_dry_run_result(suite, run) for run in selected_runs]
    elif selected_runs:
        if int(args.jobs) <= 1:
            results = [_execute_run(suite, run, args.timeout_seconds) for run in selected_runs]
        else:
            indexed_results: list[tuple[int, dict[str, Any]]] = []
            with ThreadPoolExecutor(max_workers=int(args.jobs)) as executor:
                futures = {
                    executor.submit(_execute_run, suite, run, args.timeout_seconds): index
                    for index, run in enumerate(selected_runs)
                }
                for future in as_completed(futures):
                    indexed_results.append((futures[future], future.result()))
            results = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]
    summary = {
        "suite": suite.get("name"),
        "output_root": str(output_root),
        "runs": results,
    }
    comparison = (
        _write_comparison(output_root, run_dirs, suite)
        if run_dirs and not args.dry_run
        else {}
    )
    summary["comparison"] = comparison
    summary["thresholds"] = _evaluate_thresholds(suite, comparison)
    (output_root / "suite-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_root / "suite-summary.md").write_text(_markdown_summary(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.dry_run:
        return 0
    return 0 if summary["thresholds"].get("passed") else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="docs/evals/live-planner-suite.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--case", action="append")
    parser.add_argument("--backend", action="append", choices=["claude", "codex"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of planner runs to execute concurrently. Defaults to sequential.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def _selected_runs(
    suite: dict[str, Any],
    args: argparse.Namespace,
    output_root: Path,
) -> list[dict[str, Any]]:
    selected_cases = set(args.case or [])
    selected_backends = set(args.backend or [])
    runs: list[dict[str, Any]] = []
    for case in suite.get("cases", []):
        if selected_cases and case.get("id") not in selected_cases:
            continue
        for model in suite.get("models", []):
            if selected_backends and model.get("backend") not in selected_backends:
                continue
            runs.append(
                {
                    "case": case,
                    "model": model,
                    "run_dir": output_root / _run_name(case, model),
                }
            )
    return runs


def _dry_run_result(suite: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    case = run["case"]
    model = run["model"]
    run_dir = run["run_dir"]
    return {
        "case_id": case.get("id"),
        "backend": model.get("backend"),
        "model": model.get("model"),
        "run_dir": str(run_dir),
        "status": "dry_run",
        "command": _planner_command(suite, case, model, run_dir),
    }


def _execute_run(
    suite: dict[str, Any],
    run: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    case = run["case"]
    model = run["model"]
    run_dir = run["run_dir"]
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(
        _planner_command(suite, case, model, run_dir),
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=int(timeout_seconds),
        check=False,
    )
    elapsed = round(time.monotonic() - started, 3)
    (run_dir / "runner.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "runner.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return {
        "case_id": case.get("id"),
        "backend": model.get("backend"),
        "model": model.get("model"),
        "reasoning": _model_value_for_case(model, case, "reasoning"),
        "run_dir": str(run_dir),
        "returncode": completed.returncode,
        "elapsed_seconds": elapsed,
    }


def _planner_command(
    suite: dict[str, Any],
    case: dict[str, Any],
    model: dict[str, Any],
    run_dir: Path,
) -> list[str]:
    defaults = suite.get("defaults", {})
    command = [
        sys.executable,
        "scripts/verify_lvc_r002_planner.py",
        "--backend",
        str(model["backend"]),
        "--model",
        str(model["model"]),
        "--feature-goal",
        str(case["feature_goal"]),
        "--project-root",
        str(case["project_root"]),
        "--workflow-id",
        str(case["id"]),
        "--panelists",
        str(case.get("panelists", defaults.get("panelists", 2))),
        "--max-iterations",
        str(case.get("max_iterations", defaults.get("max_iterations", 2))),
        "--context-budget-tokens",
        str(case.get("context_budget_tokens", defaults.get("context_budget_tokens", 3000))),
        "--output-dir",
        str(run_dir),
    ]
    reasoning = _model_value_for_case(model, case, "reasoning")
    if model.get("backend") == "codex" and reasoning:
        command.extend(["--reasoning", str(reasoning)])
    mechanical_model = _model_value_for_case(model, case, "mechanical_model")
    if mechanical_model:
        command.extend(["--mechanical-model", str(mechanical_model)])
    mechanical_reasoning = _model_value_for_case(model, case, "mechanical_reasoning")
    if model.get("backend") == "codex" and mechanical_reasoning:
        command.extend(["--mechanical-reasoning", str(mechanical_reasoning)])
    if model.get("backend") == "claude":
        max_budget = case.get(
            "claude_max_budget_usd",
            defaults.get("claude_max_budget_usd", "8.00"),
        )
        command.extend(
            [
                "--claude-max-budget-usd",
                str(max_budget),
            ]
        )
    return command


def _write_comparison(
    output_root: Path,
    run_dirs: list[Path],
    suite: dict[str, Any],
) -> dict[str, Any]:
    comparison_json = output_root / "comparison.json"
    comparison_md = output_root / "comparison.md"
    command = [
        sys.executable,
        "scripts/compare_planner_model_usage.py",
        *[str(path) for path in run_dirs if (path / "outcome.json").exists()],
        "--output",
        str(comparison_json),
        "--markdown-output",
        str(comparison_md),
    ]
    pricing_file = suite.get("pricing_file")
    if pricing_file:
        command.extend(["--pricing-file", str(pricing_file)])
    if len(command) <= 5:
        return {}
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not comparison_json.exists():
        return {"error": completed.stderr or completed.stdout}
    return _read_json(comparison_json)


def _evaluate_thresholds(
    suite: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    runs = comparison.get("runs")
    if not isinstance(runs, list):
        return {"passed": False, "failures": [{"reason": "comparison_missing"}]}
    case_thresholds = {
        case["id"]: _merge_thresholds(
            suite.get("defaults", {}).get("thresholds", {}),
            case.get("thresholds", {}),
        )
        for case in suite.get("cases", [])
    }
    failures: list[dict[str, Any]] = []
    for run in runs:
        case_id = _case_id_from_run_dir(str(run.get("run_dir") or ""))
        thresholds = case_thresholds.get(case_id, {})
        quality = run.get("quality") if isinstance(run.get("quality"), dict) else {}
        production_grade = (
            run.get("production_grade")
            if isinstance(run.get("production_grade"), dict)
            else {}
        )
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        if thresholds.get("required_quality_verdict") and (
            quality.get("verdict") != thresholds["required_quality_verdict"]
        ):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "quality_verdict",
                    "expected": thresholds["required_quality_verdict"],
                    "actual": quality.get("verdict"),
                }
            )
        if quality.get("score") is not None and int(quality["score"]) < int(
            thresholds.get("min_quality_score", 0)
        ):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "quality_score",
                    "expected_min": thresholds.get("min_quality_score"),
                    "actual": quality.get("score"),
                }
            )
        if thresholds.get("required_production_grade_verdict") and (
            production_grade.get("verdict") != thresholds["required_production_grade_verdict"]
        ):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "production_grade_verdict",
                    "expected": thresholds["required_production_grade_verdict"],
                    "actual": production_grade.get("verdict"),
                    "findings": production_grade.get("blocking_findings"),
                }
            )
        if production_grade.get("score") is not None and int(production_grade["score"]) < int(
            thresholds.get("min_production_grade_score", 0)
        ):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "production_grade_score",
                    "expected_min": thresholds.get("min_production_grade_score"),
                    "actual": production_grade.get("score"),
                }
            )
        cost = usage.get("api_equivalent_cost_usd")
        max_cost = thresholds.get("max_api_equivalent_cost_usd")
        if cost is not None and max_cost is not None and float(cost) > float(max_cost):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "api_equivalent_cost",
                    "expected_max": max_cost,
                    "actual": cost,
                }
            )
        iteration = run.get("accepted_at_iteration")
        max_iteration = thresholds.get("max_accepted_iteration")
        if iteration is None or (
            max_iteration is not None and int(iteration) > int(max_iteration)
        ):
            failures.append(
                {
                    "case_id": case_id,
                    "backend": run.get("backend"),
                    "reason": "accepted_iteration",
                    "expected_max": max_iteration,
                    "actual": iteration,
                }
            )
    return {"passed": not failures, "failures": failures}


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary.get('suite')}",
        "",
        f"Output: `{summary.get('output_root')}`",
        "",
        f"Thresholds: {'passed' if summary.get('thresholds', {}).get('passed') else 'failed'}",
        "",
    ]
    failures = summary.get("thresholds", {}).get("failures") or []
    if failures:
        lines.append("## Failures")
        for failure in failures:
            lines.append(f"- `{failure}`")
        lines.append("")
    lines.append("## Runs")
    for run in summary.get("runs", []):
        lines.append(
            f"- {run.get('case_id')} / {run.get('backend')} / {run.get('model')}: "
            f"{run.get('returncode', run.get('status'))}"
        )
    return "\n".join(lines) + "\n"


def _case_id_from_run_dir(run_dir: str) -> str:
    name = Path(run_dir).name
    parts = name.split("__", 1)
    return parts[0]


def _run_name(case: dict[str, Any], model: dict[str, Any]) -> str:
    reasoning_value = _model_value_for_case(model, case, "reasoning")
    mechanical_model = _model_value_for_case(model, case, "mechanical_model")
    mechanical_reasoning = _model_value_for_case(model, case, "mechanical_reasoning")
    reasoning = f"-{reasoning_value}" if reasoning_value else ""
    mechanical = (
        f"__mech-{mechanical_model}-{mechanical_reasoning or 'default'}"
        if mechanical_model
        else ""
    )
    return (
        f"{case['id']}__{model['backend']}-{model['model']}{reasoning}{mechanical}"
    ).replace("/", "-")


def _default_output_dir(suite: dict[str, Any]) -> str:
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    return f"docs/reports/live-planner-suite-{stamp}-{suite.get('name', 'suite')}"


def _merge_thresholds(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return {**base, **override}


def _model_value_for_case(model: dict[str, Any], case: dict[str, Any], key: str) -> Any:
    overrides = case.get("model_overrides")
    if isinstance(overrides, dict):
        backend_overrides = overrides.get(model.get("backend"))
        if isinstance(backend_overrides, dict) and key in backend_overrides:
            return backend_overrides[key]
    return model.get(key)


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
