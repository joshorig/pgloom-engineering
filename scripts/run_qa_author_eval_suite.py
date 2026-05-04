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
    suite = _read_json(Path(args.suite))
    output_root = Path(args.output_dir or _default_output_dir(suite))
    output_root.mkdir(parents=True, exist_ok=True)
    runs = _selected_runs(suite, args, output_root)
    results: list[dict[str, Any]]
    if args.dry_run:
        results = [_dry_run_result(suite, run) for run in runs]
    elif int(args.jobs) == 1:
        results = [_execute_run(suite, run) for run in runs]
    else:
        indexed: list[tuple[int, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=int(args.jobs)) as executor:
            futures = {
                executor.submit(_execute_run, suite, run): index
                for index, run in enumerate(runs)
            }
            for future in as_completed(futures):
                indexed.append((futures[future], future.result()))
        results = [result for _, result in sorted(indexed, key=lambda item: item[0])]
    summary = {
        "suite": suite.get("name"),
        "output_root": str(output_root),
        "runs": results,
        "thresholds": _evaluate_thresholds(suite, results),
    }
    thresholds = summary["thresholds"]
    assert isinstance(thresholds, dict)
    (output_root / "suite-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_root / "suite-summary.md").write_text(_markdown_summary(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.dry_run:
        return 0
    return 0 if thresholds["passed"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="docs/evals/qa-author-model-suite.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--case", action="append")
    parser.add_argument("--backend", action="append", choices=["claude", "codex"])
    parser.add_argument("--model", action="append")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
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
    selected_models = set(args.model or [])
    runs: list[dict[str, Any]] = []
    for case in suite.get("cases", []):
        if selected_cases and case.get("id") not in selected_cases:
            continue
        for model in suite.get("models", []):
            if selected_backends and model.get("backend") not in selected_backends:
                continue
            if selected_models and model.get("model") not in selected_models:
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
    return {
        "case_id": case.get("id"),
        "backend": model.get("backend"),
        "model": model.get("model"),
        "run_dir": str(run["run_dir"]),
        "status": "dry_run",
        "command": _qa_eval_command(suite, case, model, run["run_dir"]),
    }


def _execute_run(suite: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    case = run["case"]
    model = run["model"]
    run_dir = run["run_dir"]
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(
        _qa_eval_command(suite, case, model, run_dir),
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=int(_case_value(suite, case, "timeout_seconds", 900)) + 60,
        check=False,
    )
    elapsed = round(time.monotonic() - started, 3)
    (run_dir / "runner.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "runner.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    outcome = _read_json(run_dir / "outcome.json") if (run_dir / "outcome.json").exists() else {}
    raw_usage = outcome.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    quality = outcome.get("qa_quality_review")
    blocking_findings = (
        quality.get("blocking_findings", []) if isinstance(quality, dict) else []
    )
    return {
        "case_id": case.get("id"),
        "project": case.get("project"),
        "backend": model.get("backend"),
        "model": model.get("model"),
        "reasoning": model.get("reasoning"),
        "run_dir": str(run_dir),
        "returncode": completed.returncode,
        "elapsed_seconds": elapsed,
        "verdict": outcome.get("verdict"),
        "initial_verdict": outcome.get("initial_verdict"),
        "findings": outcome.get("findings", []),
        "blocking_findings": blocking_findings,
        "usage": {
            key: usage.get(key)
            for key in [
                "actual_input_tokens",
                "actual_output_tokens",
                "actual_total_tokens",
                "cache_read_input_tokens",
                "reasoning_output_tokens",
                "api_equivalent_cost_usd",
                "cost_without_cache_usd",
                "cache_savings_usd",
            ]
        },
    }


def _qa_eval_command(
    suite: dict[str, Any],
    case: dict[str, Any],
    model: dict[str, Any],
    run_dir: Path,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/run_qa_author_eval.py",
        "--backend",
        str(model["backend"]),
        "--model",
        str(model["model"]),
        "--from-plan-outcome",
        str(case["from_plan_outcome"]),
        "--projects-file",
        str(_case_value(suite, case, "projects_file", "docs/evals/project-registry.yaml")),
        "--output-dir",
        str(run_dir),
        "--timeout-seconds",
        str(_case_value(suite, case, "timeout_seconds", 900)),
        "--verification-index",
        str(case.get("verification_index", 0)),
    ]
    if model.get("backend") == "codex":
        command.extend(["--reasoning", str(model.get("reasoning") or "low")])
    if model.get("backend") == "claude":
        command.extend(
            [
                "--claude-max-budget-usd",
                str(model.get("claude_max_budget_usd") or "1.00"),
            ]
        )
    if not bool(case.get("repair_missing_contract", True)):
        command.append("--no-repair-missing-contract")
    return command


def _evaluate_thresholds(
    suite: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_acceptance = suite.get("acceptance")
    acceptance = raw_acceptance if isinstance(raw_acceptance, dict) else {}
    cases_by_id = {
        case.get("id"): case
        for case in suite.get("cases", [])
        if isinstance(case, dict)
    }
    failures: list[dict[str, Any]] = []
    required_verdict = acceptance.get("required_verdict")
    for result in results:
        if result.get("status") == "dry_run":
            continue
        case = cases_by_id.get(result.get("case_id"), {})
        max_tokens = _case_value(
            suite,
            case if isinstance(case, dict) else {},
            "max_actual_total_tokens",
            acceptance.get("max_actual_total_tokens"),
        )
        max_cost = _case_value(
            suite,
            case if isinstance(case, dict) else {},
            "max_api_equivalent_cost_usd",
            acceptance.get("max_api_equivalent_cost_usd"),
        )
        if result.get("returncode") != 0:
            failures.append(
                {
                    "case_id": result.get("case_id"),
                    "backend": result.get("backend"),
                    "model": result.get("model"),
                    "reason": "returncode",
                    "actual": result.get("returncode"),
                }
            )
        if required_verdict and result.get("verdict") != required_verdict:
            failures.append(
                {
                    "case_id": result.get("case_id"),
                    "backend": result.get("backend"),
                    "model": result.get("model"),
                    "reason": "verdict",
                    "expected": required_verdict,
                    "actual": result.get("verdict"),
                    "findings": result.get("findings"),
                }
            )
        usage = result.get("usage")
        total_tokens = usage.get("actual_total_tokens") if isinstance(usage, dict) else None
        if (
            max_tokens is not None
            and total_tokens is not None
            and int(total_tokens) > int(max_tokens)
        ):
            failures.append(
                {
                    "case_id": result.get("case_id"),
                    "backend": result.get("backend"),
                    "model": result.get("model"),
                    "reason": "actual_total_tokens",
                    "expected_max": max_tokens,
                    "actual": total_tokens,
                }
            )
        cost = usage.get("api_equivalent_cost_usd") if isinstance(usage, dict) else None
        if max_cost is not None and cost is not None and float(cost) > float(max_cost):
            failures.append(
                {
                    "case_id": result.get("case_id"),
                    "backend": result.get("backend"),
                    "model": result.get("model"),
                    "reason": "api_equivalent_cost_usd",
                    "expected_max": max_cost,
                    "actual": cost,
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
        "## Runs",
        "",
        "| Case | Backend | Model | Verdict | Return | Tokens | Cost |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for run in summary.get("runs", []):
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        lines.append(
            f"| {run.get('case_id')} | {run.get('backend')} | {run.get('model')} | "
            f"{run.get('verdict')} | {run.get('returncode')} | "
            f"{usage.get('actual_total_tokens') or ''} | "
            f"{usage.get('api_equivalent_cost_usd') or ''} |"
        )
    failures = summary.get("thresholds", {}).get("failures") or []
    if failures:
        lines.extend(["", "## Failures", ""])
        for failure in failures:
            lines.append(f"- `{failure}`")
    return "\n".join(lines) + "\n"


def _run_name(case: dict[str, Any], model: dict[str, Any]) -> str:
    reasoning = f"-{model['reasoning']}" if model.get("reasoning") else ""
    return f"{case['id']}__{model['backend']}-{model['model']}{reasoning}".replace("/", "-")


def _case_value(
    suite: dict[str, Any],
    case: dict[str, Any],
    key: str,
    default: Any,
) -> Any:
    if key in case:
        return case[key]
    defaults = suite.get("defaults")
    if isinstance(defaults, dict) and key in defaults:
        return defaults[key]
    return default


def _default_output_dir(suite: dict[str, Any]) -> str:
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    return f"docs/reports/qa-author-suite-{stamp}-{suite.get('name', 'suite')}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return raw if isinstance(raw, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
