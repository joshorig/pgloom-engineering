from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pgloom_engineering.contracts import PlanContract, validate_plan_contract
from pgloom_engineering.planner.critic import (
    build_plan_quality_report,
    compute_verdict,
    deterministic_check_results,
)
from pgloom_engineering.planner.production_grade import evaluate_production_grade


def main() -> int:
    args = _parse_args()
    pricing = _load_pricing(args.pricing_file)
    comparisons = [_summarize_run(Path(path), pricing=pricing) for path in args.run_dirs]
    payload = {
        "runs": comparisons,
        "notes": [
            "Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.",
            (
                "Claude text-mode artifacts from this run do not expose provider usage, so Claude "
                "token totals are estimated from prompt/response characters."
            ),
            "Claude JSON output includes total_cost_usd when available.",
            "Codex CLI text output from these runs exposes tokens but not USD cost.",
            (
                "api_equivalent_cost_usd is computed from input/cache/output token "
                "splits when pricing is configured."
            ),
        ],
    }
    output = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    if args.markdown_output:
        Path(args.markdown_output).write_text(_markdown(payload), encoding="utf-8")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--output")
    parser.add_argument("--markdown-output")
    parser.add_argument("--pricing-file")
    return parser.parse_args()


def _summarize_run(run_dir: Path, *, pricing: dict[str, Any]) -> dict[str, Any]:
    outcome = _read_json(run_dir / "outcome.json")
    usage_records = _read_usage_records(run_dir)
    if not usage_records:
        usage_records = _reconstruct_usage_records(run_dir, outcome.get("backend"))
    actual_records = [
        record for record in usage_records if record.get("actual_total_tokens") is not None
    ]
    estimated_total = sum(
        int(record.get("estimated_total_tokens") or 0) for record in usage_records
    )
    actual_total = sum(int(record["actual_total_tokens"]) for record in actual_records)
    final = outcome.get("final") if isinstance(outcome.get("final"), dict) else {}
    token_savior = (
        outcome.get("token_savior") if isinstance(outcome.get("token_savior"), dict) else {}
    )
    quality = _quality_summary(final)
    production_grade = _production_grade_summary(final)
    api_cost = _sum_api_equivalent_cost(usage_records, pricing)
    return {
        "run_dir": str(run_dir),
        "backend": outcome.get("backend") or run_dir.name.rsplit("-", 1)[-1],
        "model": outcome.get("model"),
        "reasoning": outcome.get("reasoning"),
        "status": outcome.get("status"),
        "accepted_at_iteration": outcome.get("accepted_at_iteration"),
        "slice_count": len(final.get("task_slices") or []) if isinstance(final, dict) else 0,
        "roles": sorted(
            {
                item.get("role")
                for item in final.get("task_slices", [])
                if isinstance(item, dict) and item.get("role")
            }
        )
        if isinstance(final, dict)
        else [],
        "quality": quality,
        "production_grade": production_grade,
        "token_savior": {
            "method": token_savior.get("method"),
            "original_tokens": token_savior.get("input_tokens_original"),
            "optimized_tokens": token_savior.get("input_tokens_after_savior"),
            "tokens_saved": token_savior.get("tokens_saved"),
            "reduction_ratio": token_savior.get("reduction_ratio"),
        },
        "usage": {
            "call_count": len(usage_records),
            "actual_total_tokens": actual_total if actual_records else None,
            "actual_usage_call_count": len(actual_records),
            "estimated_input_tokens": sum(
                int(record.get("estimated_input_tokens") or 0) for record in usage_records
            ),
            "estimated_output_tokens": sum(
                int(record.get("estimated_output_tokens") or 0) for record in usage_records
            ),
            "estimated_total_tokens": estimated_total,
            "elapsed_seconds": _sum_elapsed(usage_records),
            "total_cost_usd": _sum_cost(usage_records),
            "api_equivalent_cost_usd": api_cost,
            "api_equivalent_cost_source": "pricing_table" if api_cost is not None else None,
            "records": usage_records,
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _quality_summary(final: dict[str, Any]) -> dict[str, Any]:
    if not final:
        return {
            "verdict": "missing",
            "score": 0,
            "validator_error_count": None,
            "blocking_check_count": None,
            "blocking_findings": [],
        }
    try:
        plan = PlanContract.model_validate(final)
    except Exception as exc:
        return {
            "verdict": "invalid_contract",
            "score": 0,
            "validator_error_count": None,
            "blocking_check_count": None,
            "blocking_findings": [{"code": "contract_parse_failed", "message": str(exc)}],
        }
    validator_errors = validate_plan_contract(plan)
    checks = deterministic_check_results(plan, validator_errors)
    verdict = compute_verdict(checks, validator_errors)
    report = build_plan_quality_report(
        verdict=verdict,
        validator_errors=validator_errors,
        model_results=checks,
        deterministic_results=checks,
    )
    return {
        "verdict": report.verdict,
        "score": report.score,
        "validator_error_count": report.validator_error_count,
        "blocking_check_count": report.blocking_check_count,
        "blocking_findings": [
            finding.model_dump(mode="json")
            for finding in report.deterministic_blocking_findings[:10]
        ],
    }


def _production_grade_summary(final: dict[str, Any]) -> dict[str, Any]:
    if not final:
        return {
            "verdict": "missing",
            "score": 0,
            "blocking_findings": [],
            "advisory_findings": [],
        }
    try:
        plan = PlanContract.model_validate(final)
    except Exception as exc:
        return {
            "verdict": "invalid_contract",
            "score": 0,
            "blocking_findings": [{"code": "contract_parse_failed", "message": str(exc)}],
            "advisory_findings": [],
        }
    report = evaluate_production_grade(plan)
    return report.model_dump(mode="json")


def _read_usage_records(run_dir: Path) -> list[dict[str, Any]]:
    usage_path = run_dir / "model_usage.jsonl"
    if not usage_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if isinstance(raw, dict):
            records.append(raw)
    return records


def _reconstruct_usage_records(run_dir: Path, backend: object) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for prompt_path, response_path, prefix in _prompt_response_pairs(run_dir):
        if not response_path.exists():
            continue
        prompt = prompt_path.read_text(encoding="utf-8")
        response = response_path.read_text(encoding="utf-8")
        profile_name, call_index = _split_prefix(prefix)
        actual_total = _actual_total_tokens(response)
        claude_usage = _claude_usage(response) if backend == "claude" else {}
        codex_usage = _codex_usage(response) if backend == "codex" else {}
        if claude_usage.get("total_tokens") is not None:
            actual_total = int(claude_usage["total_tokens"])
        if codex_usage.get("total_tokens") is not None:
            actual_total = int(codex_usage["total_tokens"])
        records.append(
            {
                "backend": backend,
                "profile_name": profile_name,
                "call_index": call_index,
                "prompt_chars": len(prompt),
                "response_chars": len(response),
                "estimated_input_tokens": _estimate_tokens(prompt),
                "estimated_output_tokens": _estimate_tokens(response),
                "estimated_total_tokens": _estimate_tokens(prompt) + _estimate_tokens(response),
                "actual_total_tokens": actual_total,
                "actual_input_tokens": claude_usage.get("input_tokens")
                or codex_usage.get("input_tokens"),
                "actual_output_tokens": claude_usage.get("output_tokens")
                or codex_usage.get("output_tokens"),
                "cache_creation_input_tokens": claude_usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": claude_usage.get("cache_read_input_tokens")
                or codex_usage.get("cached_input_tokens"),
                "reasoning_output_tokens": codex_usage.get("reasoning_output_tokens"),
                "total_cost_usd": claude_usage.get("total_cost_usd"),
                "actual_usage_source": _actual_usage_source(
                    backend, actual_total, claude_usage, codex_usage
                ),
            }
        )
    return records


def _prompt_response_pairs(run_dir: Path) -> list[tuple[Path, Path, str]]:
    pairs: list[tuple[Path, Path, str]] = []
    for prompt_path in sorted(run_dir.glob("*.prompt.txt")):
        prefix = prompt_path.name.removesuffix(".prompt.txt")
        pairs.append((prompt_path, run_dir / f"{prefix}.response.txt", prefix))
    single_prompt = run_dir / "prompt.txt"
    single_response = run_dir / "response.txt"
    if single_prompt.exists() and single_response.exists():
        pairs.append((single_prompt, single_response, "single-model-01"))
    return pairs


def _split_prefix(prefix: str) -> tuple[str, int | None]:
    match = re.match(r"^(.*)-([0-9]+)$", prefix)
    if not match:
        return prefix, None
    return match.group(1), int(match.group(2))


def _actual_total_tokens(response: str) -> int | None:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)\s*$", response, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _claude_usage(response: str) -> dict[str, Any]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cache_creation = _int_or_none(usage.get("cache_creation_input_tokens"))
    cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
    token_parts = [input_tokens, output_tokens, cache_creation, cache_read]
    total_tokens = sum(item for item in token_parts if item is not None)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens": total_tokens,
        "total_cost_usd": payload.get("total_cost_usd"),
    }


def _codex_usage(response: str) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for line in response.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") != "turn.completed":
            continue
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage = raw_usage
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cached_input_tokens = _int_or_none(usage.get("cached_input_tokens"))
    reasoning_output_tokens = _int_or_none(usage.get("reasoning_output_tokens"))
    if input_tokens is None and output_tokens is None:
        return {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": (input_tokens or 0) + (output_tokens or 0),
    }


def _actual_usage_source(
    backend: object,
    actual_total_tokens: int | None,
    claude_usage: dict[str, Any],
    codex_usage: dict[str, Any],
) -> str | None:
    if claude_usage.get("total_tokens") is not None:
        return "claude_json_usage"
    if codex_usage.get("total_tokens") is not None:
        return "codex_json_usage"
    if backend == "codex" and actual_total_tokens is not None:
        return "codex_cli_tokens_used"
    return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _sum_elapsed(records: list[dict[str, Any]]) -> float | None:
    values = [record.get("elapsed_seconds") for record in records]
    if not values or any(value is None for value in values):
        return None
    return round(sum(float(value) for value in values), 3)


def _sum_cost(records: list[dict[str, Any]]) -> float | None:
    values = [record.get("total_cost_usd") for record in records]
    costs = [float(value) for value in values if value is not None]
    if not costs:
        return None
    return round(sum(costs), 6)


def _sum_api_equivalent_cost(
    records: list[dict[str, Any]],
    pricing: dict[str, Any],
) -> float | None:
    costs = [_api_equivalent_cost(record, pricing) for record in records]
    values = [cost for cost in costs if cost is not None]
    if not values:
        return None
    return round(sum(values), 6)


def _api_equivalent_cost(
    record: dict[str, Any],
    pricing: dict[str, Any],
) -> float | None:
    model = str(record.get("model") or "")
    backend = str(record.get("backend") or "")
    model_pricing = _model_pricing(pricing, backend=backend, model=model)
    if model_pricing is None:
        return None
    input_tokens = int(record.get("actual_input_tokens") or 0)
    output_tokens = int(record.get("actual_output_tokens") or 0)
    cache_creation = int(record.get("cache_creation_input_tokens") or 0)
    cache_read = int(record.get("cache_read_input_tokens") or 0)
    reasoning = int(record.get("reasoning_output_tokens") or 0)
    uncached_input = max(0, input_tokens - cache_read - cache_creation)
    output_billable = max(output_tokens, reasoning)
    cost = (
        uncached_input * float(model_pricing.get("input_per_million", 0))
        + cache_creation * float(model_pricing.get("cache_creation_per_million", 0))
        + cache_read * float(model_pricing.get("cache_read_per_million", 0))
        + output_billable * float(model_pricing.get("output_per_million", 0))
    ) / 1_000_000
    return cost


def _model_pricing(
    pricing: dict[str, Any],
    *,
    backend: str,
    model: str,
) -> dict[str, Any] | None:
    models = pricing.get("models")
    if not isinstance(models, dict):
        return None
    keys = [
        f"{backend}:{model}",
        model,
        f"{backend}:default",
    ]
    for key in keys:
        value = models.get(key)
        if isinstance(value, dict):
            return value
    return None


def _load_pricing(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Planner Model Usage Comparison",
        "",
        (
        "| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | "
        "Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for run in payload["runs"]:
        usage = run["usage"]
        savior = run["token_savior"]
        quality = run["quality"]
        production_grade = run.get("production_grade") or {}
        actual = usage["actual_total_tokens"] if usage["actual_total_tokens"] is not None else "n/a"
        cost = usage["total_cost_usd"] if usage["total_cost_usd"] is not None else "n/a"
        api_cost = (
            usage["api_equivalent_cost_usd"]
            if usage["api_equivalent_cost_usd"] is not None
            else "n/a"
        )
        saved = savior.get("tokens_saved")
        ratio = savior.get("reduction_ratio")
        if isinstance(ratio, int | float):
            token_savior = f"{saved} saved ({ratio:.1%})"
        else:
            token_savior = "n/a"
        lines.append(
        "| {backend} | {status} | {iteration} | {calls} | {actual} | {estimated} | "
            "{cost} | {api_cost} | {quality} / prod {prod} | {token_savior} | {slices} |".format(
                backend=_run_label(run),
                status=run["status"],
                iteration=run["accepted_at_iteration"] or "n/a",
                calls=usage["call_count"],
                actual=actual,
                estimated=usage["estimated_total_tokens"],
                cost=cost,
                api_cost=api_cost,
                quality=f"{quality['verdict']} ({quality['score']})",
                prod=f"{production_grade.get('verdict')} ({production_grade.get('score')})",
                token_savior=token_savior,
                slices=run["slice_count"],
            )
        )
    lines.extend(["", "Notes:"])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines) + "\n"


def _run_label(run: dict[str, Any]) -> str:
    model = run.get("model")
    reasoning = run.get("reasoning")
    if model and reasoning:
        return f"{run['backend']} {model}/{reasoning}"
    if model:
        return f"{run['backend']} {model}"
    return str(run["backend"])


if __name__ == "__main__":
    raise SystemExit(main())
