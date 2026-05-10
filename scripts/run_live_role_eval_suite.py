from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pgloom_engineering.live_role_eval import dry_run_result, selected_cases


def main() -> int:
    args = _parse_args()
    suite = _read_json(Path(args.suite))
    output_root = Path(args.output_dir or _default_output_dir(suite))
    output_root.mkdir(parents=True, exist_ok=True)
    cases = selected_cases(suite, args.role)
    if args.dry_run:
        results = [
            dry_run_result(
                case,
                output_dir=output_root / _run_name(case),
                backend=args.backend,
                model=args.model,
                reasoning=args.reasoning,
            )
            for case in cases
        ]
    elif args.jobs == 1:
        results = [_execute_case(args, suite, case, output_root) for case in cases]
    else:
        indexed: list[tuple[int, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(_execute_case, args, suite, case, output_root): index
                for index, case in enumerate(cases)
            }
            for future in as_completed(futures):
                indexed.append((futures[future], future.result()))
        results = [result for _, result in sorted(indexed, key=lambda item: item[0])]
    summary = {
        "suite": suite.get("name"),
        "output_root": str(output_root),
        "runs": results,
        "thresholds": {"passed": True, "status": "dry_run"}
        if args.dry_run
        else _evaluate_thresholds(suite, results),
    }
    (output_root / "suite-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (output_root / "suite-summary.md").write_text(_markdown_summary(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if args.dry_run or summary["thresholds"]["passed"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="docs/evals/live-role-suite.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--role", action="append")
    parser.add_argument("--database-url")
    parser.add_argument("--backend", choices=["codex", "claude"], default="codex")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def _execute_case(
    args: argparse.Namespace,
    suite: dict[str, Any],
    case: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    del suite
    run_dir = output_root / _run_name(case)
    run_dir.mkdir(parents=True, exist_ok=True)
    case_path = run_dir / "case.json"
    case_path.write_text(json.dumps(case, indent=2, sort_keys=True), encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_live_role_eval.py",
            "--role",
            str(case["role"]),
            "--case",
            str(case_path),
            "--output-dir",
            str(run_dir),
            "--backend",
            args.backend,
            "--model",
            str(case.get("model") or args.model),
            "--reasoning",
            str(case.get("reasoning") or args.reasoning),
            "--max-steps",
            str(args.max_steps),
            *(
                ["--database-url", args.database_url]
                if args.database_url is not None
                else []
            ),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    elapsed = round(time.monotonic() - started, 3)
    (run_dir / "runner.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "runner.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    outcome = _read_json(run_dir / "outcome.json") if (run_dir / "outcome.json").exists() else {}
    return {
        "case_id": case.get("id"),
        "role": case.get("role"),
        "run_dir": str(run_dir),
        "returncode": completed.returncode,
        "elapsed_seconds": elapsed,
        "status": outcome.get("status") or "error",
        "checks": outcome.get("checks", []),
    }


def _evaluate_thresholds(suite: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    acceptance = suite.get("acceptance") if isinstance(suite.get("acceptance"), dict) else {}
    required_status = acceptance.get("required_status", "pass")
    min_pass_rate = float(acceptance.get("min_pass_rate", 1.0))
    if not results:
        return {"passed": False, "reason": "no_cases"}
    passed = [
        result
        for result in results
        if result.get("status") == required_status and int(result.get("returncode") or 0) == 0
    ]
    pass_rate = len(passed) / len(results)
    return {
        "passed": pass_rate >= min_pass_rate,
        "pass_rate": pass_rate,
        "required_status": required_status,
        "min_pass_rate": min_pass_rate,
    }


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary.get('suite') or 'Live role eval suite'}",
        "",
        f"- output_root: `{summary['output_root']}`",
        f"- passed: `{summary['thresholds']['passed']}`",
        "",
        "| case | role | status | returncode | elapsed_seconds |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for run in summary["runs"]:
        lines.append(
            "| {case} | {role} | {status} | {returncode} | {elapsed} |".format(
                case=run.get("case_id"),
                role=run.get("role"),
                status=run.get("status"),
                returncode=run.get("returncode", ""),
                elapsed=run.get("elapsed_seconds", ""),
            )
        )
    return "\n".join(lines) + "\n"


def _run_name(case: dict[str, Any]) -> str:
    return f"{case.get('id', 'case')}__{case.get('role', 'role')}"


def _default_output_dir(suite: dict[str, Any]) -> Path:
    name = suite.get("name") or "live-role-suite"
    slug = str(name).lower().replace(" ", "-")
    return Path("docs/reports") / slug


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
