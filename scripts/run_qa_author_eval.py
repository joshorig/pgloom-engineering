from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from pgloom.context import count_tokens
from pgloom.harness.subprocess import run_bounded

from pgloom_engineering.contracts import (
    CONTRACT_VERSION,
    DesignContract,
    PlanContract,
    QAAuthorContract,
    TaskContract,
    TaskSliceContract,
)
from pgloom_engineering.integrations.git import changed_files, create_task_worktree
from pgloom_engineering.planner.json_tools import extract_json
from pgloom_engineering.projects import ProjectConfig, get_project, resolve_project_file
from pgloom_engineering.qa_author_runtime import (
    add_configured_gate_matrix_coverage,
    benchmark_requirements_for_task,
    build_qa_author_prompt,
    build_qa_code_repair_prompt,
    build_qa_quality_repair_prompt,
    normalize_qa_author_payload,
    path_violations,
    qa_code_repairable,
    qa_quality_repairable,
    red_proof_verification_commands,
    semantic_quality_findings,
)
from pgloom_engineering.qa_runtime import (
    canonical_red_proof,
    discover_route_inventory,
    hydrate_dependencies,
    is_authored_test_compile_failure,
    is_red_test_failure,
    project_qa_metadata,
    prompt_safe_qa_metadata,
    qa_env,
    red_proof_infra_error,
    relevant_changed_files,
    route_inventory_for_prompt,
    run_qa_verification,
    validate_required_qa_gates,
)


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pgloom-qa-author-eval-") as tmp:
        tmp_path = Path(tmp)
        if args.from_plan_outcome:
            plan = _plan_from_outcome(Path(args.from_plan_outcome))
            registered_project = _load_registered_project(args, plan.project)
            if args.project_root:
                repo = Path(args.project_root)
            elif registered_project is not None:
                repo = registered_project.root
            else:
                raise SystemExit(
                    "--project-root, --projects-file, or --database-url is required "
                    "with --from-plan-outcome"
                )
            project_metadata = _merged_project_metadata(args, registered_project)
            task_contract = _task_contract_from_plan(plan, args.qa_slice)
            base_ref = args.base_ref
        else:
            repo = _fixture_repo(tmp_path)
            plan = _fixture_plan()
            task_contract = _fixture_task_contract()
            project_metadata = {}
            base_ref = "main"
        worktree = create_task_worktree(
            repo=repo,
            worktree_root=tmp_path / "worktrees",
            feature_id=plan.feature_id,
            task_id=task_contract.inputs["task_id"],
            slice_id=str(task_contract.inputs["task_slice_id"]),
            base_ref=base_ref,
        )
        if args.from_plan_outcome:
            hydrate_dependencies(repo, worktree.worktree, project_metadata)
        verification_command = _verification_command(task_contract, args.verification_index)
        prompt = build_qa_author_prompt(
            plan,
            task_contract,
            project_metadata=project_metadata,
            project_root=worktree.worktree,
        )
        prompt_path = output_dir / "qa-author.prompt.txt"
        response_path = output_dir / "qa-author.response.txt"
        event_log_path = output_dir / "qa-author.events.jsonl"
        final_message_path = output_dir / "qa-author.final.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        started = time.monotonic()
        completed = subprocess.run(
            _model_command(args, worktree.worktree, output_last_message=final_message_path),
            input=prompt,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
            check=False,
            cwd=worktree.worktree,
            env={**os.environ, **qa_env(project_metadata, project_root=repo)},
        )
        elapsed = round(time.monotonic() - started, 3)
        model_response = _final_model_response(
            completed.stdout,
            final_message_path=final_message_path,
            event_log_path=event_log_path,
            backend=args.backend,
        )
        response_path.write_text(model_response, encoding="utf-8")
        (output_dir / "qa-author.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        usage = _usage(
            backend=args.backend,
            model=args.model,
            reasoning=args.reasoning,
            elapsed_seconds=elapsed,
            prompt=prompt,
            response=completed.stdout,
            pricing=_load_pricing(args.pricing_file),
        )
        outcome = _evaluate(
            stdout=model_response,
            subprocess_returncode=completed.returncode,
            worktree=worktree.worktree,
            plan=plan,
            task_contract=task_contract,
            usage=usage,
            verification_command=verification_command,
            project_metadata=project_metadata,
        )
        initial_verdict = outcome["verdict"]
        initial_findings = outcome["findings"]
        repair_state: dict[str, bool] = {
            "red_repair_attempted": False,
            "repair_attempted": False,
            "quality_repair_attempted": False,
        }
        records = [usage]
        red_repair_count = 0
        while (
            args.repair_missing_contract
            and red_repair_count < 2
            and outcome["verdict"] != "accept"
            and qa_code_repairable(outcome)
        ):
            red_repair_count += 1
            suffix = "" if red_repair_count == 1 else f"-{red_repair_count}"
            outcome = _run_red_repair_phase(
                args=args,
                repo=repo,
                output_dir=output_dir,
                file_prefix=f"qa-author-red-repair{suffix}",
                phase="red_repair" if red_repair_count == 1 else f"red_repair_{red_repair_count}",
                worktree=worktree.worktree,
                plan=plan,
                task_contract=task_contract,
                outcome=outcome,
                records=records,
                verification_command=verification_command,
                project_metadata=project_metadata,
            )
            repair_state["red_repair_attempted"] = True
        _apply_repair_state(outcome, repair_state)
        if (
            args.repair_missing_contract
            and outcome["verdict"] != "accept"
            and _contract_repairable(outcome)
        ):
            repair_prompt = _repair_prompt(
                plan=plan,
                task_contract=task_contract,
                worktree=worktree.worktree,
                changed_files=outcome["changed_files"],
                pytest_excerpt=outcome["pytest_stdout_excerpt"],
                initial_contract=outcome.get("qa_author_contract"),
            )
            (output_dir / "qa-author-repair.prompt.txt").write_text(
                repair_prompt,
                encoding="utf-8",
            )
            repair_started = time.monotonic()
            repair_final_message_path = output_dir / "qa-author-repair.final.txt"
            repair_event_log_path = output_dir / "qa-author-repair.events.jsonl"
            repair = subprocess.run(
                _model_command(
                    args,
                    worktree.worktree,
                    sandbox="read-only",
                    output_last_message=repair_final_message_path,
                ),
                input=repair_prompt,
                text=True,
                capture_output=True,
                timeout=args.timeout_seconds,
                check=False,
                cwd=worktree.worktree,
                env={**os.environ, **qa_env(project_metadata, project_root=repo)},
            )
            repair_elapsed = round(time.monotonic() - repair_started, 3)
            repair_response = _final_model_response(
                repair.stdout,
                final_message_path=repair_final_message_path,
                event_log_path=repair_event_log_path,
                backend=args.backend,
            )
            (output_dir / "qa-author-repair.response.txt").write_text(
                repair_response,
                encoding="utf-8",
            )
            (output_dir / "qa-author-repair.stderr.txt").write_text(
                repair.stderr,
                encoding="utf-8",
            )
            repair_usage = _usage(
                backend=args.backend,
                model=args.model,
                reasoning=args.reasoning,
                elapsed_seconds=repair_elapsed,
                prompt=repair_prompt,
                response=repair.stdout,
                pricing=_load_pricing(args.pricing_file),
            )
            repair_usage["phase"] = "contract_repair"
            usage["phase"] = "author"
            records.append(repair_usage)
            outcome = _evaluate(
                stdout=repair_response,
                subprocess_returncode=repair.returncode,
                worktree=worktree.worktree,
                plan=plan,
                task_contract=task_contract,
                usage=_combined_usage(records),
                verification_command=verification_command,
                project_metadata=project_metadata,
            )
            repair_state["repair_attempted"] = True
            _apply_repair_state(outcome, repair_state)
        else:
            _apply_repair_state(outcome, repair_state)
        outcome["initial_verdict"] = initial_verdict
        outcome["initial_findings"] = initial_findings
        outcome["artifacts"] = _archive_changed_files(
            output_dir=output_dir,
            worktree=worktree.worktree,
            changed_files=outcome["changed_files"],
        )
        quality_review = _review_qa_author_quality(
            output_dir=output_dir,
            worktree=worktree.worktree,
            plan=plan,
            task_contract=task_contract,
            outcome=outcome,
            project_metadata=project_metadata,
        )
        outcome["qa_quality_review"] = quality_review
        if quality_review["blocking_findings"]:
            if args.repair_quality and qa_quality_repairable(quality_review):
                quality_repair_prompt = build_qa_quality_repair_prompt(
                    plan=plan,
                    task_contract=task_contract,
                    worktree=worktree.worktree,
                    changed_files=outcome["changed_files"],
                    quality_review=quality_review,
                    current_contract=outcome.get("qa_author_contract"),
                    project_metadata=project_metadata,
                )
                (output_dir / "qa-author-quality-repair.prompt.txt").write_text(
                    quality_repair_prompt,
                    encoding="utf-8",
                )
                quality_repair_started = time.monotonic()
                quality_repair_final_path = output_dir / "qa-author-quality-repair.final.txt"
                quality_repair_event_path = output_dir / "qa-author-quality-repair.events.jsonl"
                quality_repair = subprocess.run(
                    _model_command(
                        args,
                        worktree.worktree,
                        output_last_message=quality_repair_final_path,
                    ),
                    input=quality_repair_prompt,
                    text=True,
                    capture_output=True,
                    timeout=args.timeout_seconds,
                    check=False,
                    cwd=worktree.worktree,
                    env={**os.environ, **qa_env(project_metadata, project_root=repo)},
                )
                quality_repair_elapsed = round(time.monotonic() - quality_repair_started, 3)
                quality_repair_response = _final_model_response(
                    quality_repair.stdout,
                    final_message_path=quality_repair_final_path,
                    event_log_path=quality_repair_event_path,
                    backend=args.backend,
                )
                (output_dir / "qa-author-quality-repair.response.txt").write_text(
                    quality_repair_response,
                    encoding="utf-8",
                )
                (output_dir / "qa-author-quality-repair.stderr.txt").write_text(
                    quality_repair.stderr,
                    encoding="utf-8",
                )
                quality_repair_usage = _usage(
                    backend=args.backend,
                    model=args.model,
                    reasoning=args.reasoning,
                    elapsed_seconds=quality_repair_elapsed,
                    prompt=quality_repair_prompt,
                    response=quality_repair.stdout,
                    pricing=_load_pricing(args.pricing_file),
                )
                quality_repair_usage["phase"] = "quality_repair"
                records.append(quality_repair_usage)
                outcome = _evaluate(
                    stdout=quality_repair_response,
                    subprocess_returncode=quality_repair.returncode,
                    worktree=worktree.worktree,
                    plan=plan,
                    task_contract=task_contract,
                    usage=_combined_usage(records),
                    verification_command=verification_command,
                    project_metadata=project_metadata,
                )
                if _contract_repairable(outcome):
                    repair_prompt = _repair_prompt(
                        plan=plan,
                        task_contract=task_contract,
                        worktree=worktree.worktree,
                        changed_files=outcome["changed_files"],
                        pytest_excerpt=outcome["pytest_stdout_excerpt"],
                        initial_contract=outcome.get("qa_author_contract"),
                    )
                    (output_dir / "qa-author-quality-contract-repair.prompt.txt").write_text(
                        repair_prompt,
                        encoding="utf-8",
                    )
                    repair_started = time.monotonic()
                    repair_final_message_path = (
                        output_dir / "qa-author-quality-contract-repair.final.txt"
                    )
                    repair_event_log_path = (
                        output_dir / "qa-author-quality-contract-repair.events.jsonl"
                    )
                    repair = subprocess.run(
                        _model_command(
                            args,
                            worktree.worktree,
                            sandbox="read-only",
                            output_last_message=repair_final_message_path,
                        ),
                        input=repair_prompt,
                        text=True,
                        capture_output=True,
                        timeout=args.timeout_seconds,
                        check=False,
                        cwd=worktree.worktree,
                        env={**os.environ, **qa_env(project_metadata, project_root=repo)},
                    )
                    repair_elapsed = round(time.monotonic() - repair_started, 3)
                    repair_response = _final_model_response(
                        repair.stdout,
                        final_message_path=repair_final_message_path,
                        event_log_path=repair_event_log_path,
                        backend=args.backend,
                    )
                    (output_dir / "qa-author-quality-contract-repair.response.txt").write_text(
                        repair_response,
                        encoding="utf-8",
                    )
                    (output_dir / "qa-author-quality-contract-repair.stderr.txt").write_text(
                        repair.stderr,
                        encoding="utf-8",
                    )
                    repair_usage = _usage(
                        backend=args.backend,
                        model=args.model,
                        reasoning=args.reasoning,
                        elapsed_seconds=repair_elapsed,
                        prompt=repair_prompt,
                        response=repair.stdout,
                        pricing=_load_pricing(args.pricing_file),
                    )
                    repair_usage["phase"] = "quality_contract_repair"
                    records.append(repair_usage)
                    outcome = _evaluate(
                        stdout=repair_response,
                        subprocess_returncode=repair.returncode,
                        worktree=worktree.worktree,
                        plan=plan,
                        task_contract=task_contract,
                        usage=_combined_usage(records),
                        verification_command=verification_command,
                        project_metadata=project_metadata,
                    )
                repair_state["quality_repair_attempted"] = True
                _apply_repair_state(outcome, repair_state)
                outcome["initial_verdict"] = initial_verdict
                outcome["initial_findings"] = initial_findings
                outcome["artifacts"] = _archive_changed_files(
                    output_dir=output_dir,
                    worktree=worktree.worktree,
                    changed_files=outcome["changed_files"],
                )
                quality_review = _review_qa_author_quality(
                    output_dir=output_dir,
                    worktree=worktree.worktree,
                    plan=plan,
                    task_contract=task_contract,
                    outcome=outcome,
                    project_metadata=project_metadata,
                )
                outcome["qa_quality_review"] = quality_review
                if (
                    args.repair_missing_contract
                    and outcome["verdict"] != "accept"
                    and qa_code_repairable(outcome)
                ):
                    outcome = _run_red_repair_phase(
                        args=args,
                        repo=repo,
                        output_dir=output_dir,
                        file_prefix="qa-author-quality-red-repair",
                        phase="quality_red_repair",
                        worktree=worktree.worktree,
                        plan=plan,
                        task_contract=task_contract,
                        outcome=outcome,
                        records=records,
                        verification_command=verification_command,
                        project_metadata=project_metadata,
                    )
                    repair_state["red_repair_attempted"] = True
                    _apply_repair_state(outcome, repair_state)
                    if _contract_repairable(outcome):
                        repair_prompt = _repair_prompt(
                            plan=plan,
                            task_contract=task_contract,
                            worktree=worktree.worktree,
                            changed_files=outcome["changed_files"],
                            pytest_excerpt=outcome["pytest_stdout_excerpt"],
                            initial_contract=outcome.get("qa_author_contract"),
                        )
                        (
                            output_dir
                            / "qa-author-quality-red-contract-repair.prompt.txt"
                        ).write_text(
                            repair_prompt,
                            encoding="utf-8",
                        )
                        repair_started = time.monotonic()
                        repair_final_message_path = (
                            output_dir / "qa-author-quality-red-contract-repair.final.txt"
                        )
                        repair_event_log_path = (
                            output_dir / "qa-author-quality-red-contract-repair.events.jsonl"
                        )
                        repair = subprocess.run(
                            _model_command(
                                args,
                                worktree.worktree,
                                sandbox="read-only",
                                output_last_message=repair_final_message_path,
                            ),
                            input=repair_prompt,
                            text=True,
                            capture_output=True,
                            timeout=args.timeout_seconds,
                            check=False,
                            cwd=worktree.worktree,
                            env={**os.environ, **qa_env(project_metadata, project_root=repo)},
                        )
                        repair_elapsed = round(time.monotonic() - repair_started, 3)
                        repair_response = _final_model_response(
                            repair.stdout,
                            final_message_path=repair_final_message_path,
                            event_log_path=repair_event_log_path,
                            backend=args.backend,
                        )
                        (
                            output_dir
                            / "qa-author-quality-red-contract-repair.response.txt"
                        ).write_text(
                            repair_response,
                            encoding="utf-8",
                        )
                        (
                            output_dir
                            / "qa-author-quality-red-contract-repair.stderr.txt"
                        ).write_text(
                            repair.stderr,
                            encoding="utf-8",
                        )
                        repair_usage = _usage(
                            backend=args.backend,
                            model=args.model,
                            reasoning=args.reasoning,
                            elapsed_seconds=repair_elapsed,
                            prompt=repair_prompt,
                            response=repair.stdout,
                            pricing=_load_pricing(args.pricing_file),
                        )
                        repair_usage["phase"] = "quality_red_contract_repair"
                        records.append(repair_usage)
                        outcome = _evaluate(
                            stdout=repair_response,
                            subprocess_returncode=repair.returncode,
                            worktree=worktree.worktree,
                            plan=plan,
                            task_contract=task_contract,
                            usage=_combined_usage(records),
                            verification_command=verification_command,
                            project_metadata=project_metadata,
                        )
                        repair_state["repair_attempted"] = True
                        _apply_repair_state(outcome, repair_state)
                    outcome["artifacts"] = _archive_changed_files(
                        output_dir=output_dir,
                        worktree=worktree.worktree,
                        changed_files=outcome["changed_files"],
                    )
                    quality_review = _review_qa_author_quality(
                        output_dir=output_dir,
                        worktree=worktree.worktree,
                        plan=plan,
                        task_contract=task_contract,
                        outcome=outcome,
                        project_metadata=project_metadata,
                    )
                    outcome["qa_quality_review"] = quality_review
            else:
                _apply_repair_state(outcome, repair_state)
        else:
            _apply_repair_state(outcome, repair_state)
        if quality_review["blocking_findings"]:
            outcome["findings"].extend(quality_review["blocking_findings"])
            outcome["verdict"] = "revise"
        (output_dir / "model_usage.jsonl").write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
        (output_dir / "outcome.json").write_text(
            json.dumps(outcome, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0 if outcome["verdict"] == "accept" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["codex", "claude"], default="codex")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--pricing-file", default="docs/reports/planner-pricing-2026-05-03.json")
    parser.add_argument("--claude-max-budget-usd", default="1.00")
    parser.add_argument("--from-plan-outcome")
    parser.add_argument("--project-root")
    parser.add_argument("--projects-file")
    parser.add_argument("--database-url")
    parser.add_argument("--project-metadata-file")
    parser.add_argument("--qa-slice", default="qa-author")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--verification-index", type=int, default=0)
    parser.add_argument(
        "--no-repair-missing-contract",
        dest="repair_missing_contract",
        action="store_false",
    )
    parser.add_argument(
        "--no-repair-quality",
        dest="repair_quality",
        action="store_false",
    )
    parser.set_defaults(repair_missing_contract=True)
    parser.set_defaults(repair_quality=True)
    return parser.parse_args()


def _load_registered_project(
    args: argparse.Namespace,
    project_name: str,
) -> ProjectConfig | None:
    if args.projects_file:
        project = resolve_project_file(Path(args.projects_file), project_name)
        if project is not None:
            return project
    if args.database_url:
        return get_project(project_name, database_url=args.database_url)
    return None


def _merged_project_metadata(
    args: argparse.Namespace,
    project: ProjectConfig | None,
) -> dict[str, Any]:
    metadata = dict(project.metadata) if project is not None else {}
    if args.project_metadata_file:
        metadata = _deep_merge_dicts(
            metadata,
            _load_json_object(Path(args.project_metadata_file)),
        )
    return metadata


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _deep_merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(existing, value)
        else:
            merged[key] = value
    return merged


def _fixture_repo(root: Path) -> Path:
    repo = root / "repo"
    _run(["git", "init", "-b", "main", str(repo)])
    _run(["git", "config", "user.email", "qa-eval@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "QA Eval"], cwd=repo)
    repo.joinpath("sample_app").mkdir()
    repo.joinpath("sample_app/__init__.py").write_text("", encoding="utf-8")
    repo.joinpath("sample_app/pricing.py").write_text(
        "\n".join(
            [
                "from decimal import Decimal",
                "",
                "def discounted_total(amount: Decimal, percent: int) -> Decimal:",
                "    return amount",
                "",
            ]
        ),
        encoding="utf-8",
    )
    repo.joinpath("tests").mkdir()
    repo.joinpath("tests/.gitkeep").write_text("", encoding="utf-8")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-m", "initial fixture"], cwd=repo)
    return repo


def _fixture_plan() -> PlanContract:
    matrix = [
        "discounted_total applies a positive percentage discount and rounds to cents",
        "discounted_total rejects negative amounts with ValueError",
        "discounted_total rejects percentages outside 0..100 with ValueError",
    ]
    return PlanContract(
        feature_id="qa-eval-feature",
        project="qa-eval",
        problem_statement="Add pricing discount behavior.",
        design_contract=DesignContract(
            public_api="sample_app.pricing.discounted_total(amount: Decimal, percent: int)",
            ownership_boundaries="QA may only add tests; implementation remains unchanged.",
            acceptance_tests=matrix,
        ),
        affected_surfaces=["sample_app/", "tests/"],
        task_slices=[
            TaskSliceContract(
                slice_id="qa-author",
                role="qa",
                task_type="engineering.qa.author",
                objective="Write red tests for discounted_total acceptance criteria.",
                allowed_paths=["tests/"],
                forbidden_paths=["sample_app/"],
                expected_outputs=["QAAuthorContract"],
                verification_commands=[[sys.executable, "-m", "pytest", "tests", "-q"]],
            )
        ],
        acceptance_test_matrix=matrix,
    )


def _fixture_task_contract() -> TaskContract:
    return TaskContract(
        feature_id="qa-eval-feature",
        plan_contract_id="qa-eval-plan",
        role="qa",
        task_type="engineering.qa.author",
        objective="Write red tests for discounted_total acceptance criteria.",
        inputs={"task_id": "qa-eval-task", "task_slice_id": "qa-author"},
        allowed_paths=["tests/"],
        forbidden_paths=["sample_app/"],
        expected_outputs=["QAAuthorContract"],
        verification_commands=[[sys.executable, "-m", "pytest", "tests", "-q"]],
    )


def _plan_from_outcome(path: Path) -> PlanContract:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    final = payload.get("final")
    nested_plan = final.get("plan_contract") if isinstance(final, dict) else None
    candidates = [final, nested_plan, payload.get("plan_contract")]
    for candidate in candidates:
        if isinstance(candidate, dict):
            try:
                return PlanContract.model_validate(candidate)
            except Exception:
                continue
    raise ValueError(f"{path} does not contain a valid PlanContract")


def _task_contract_from_plan(plan: PlanContract, qa_slice: str) -> TaskContract:
    selected = None
    for task_slice in plan.task_slices:
        if task_slice.slice_id == qa_slice or task_slice.task_type == "engineering.qa.author":
            selected = task_slice
            if task_slice.slice_id == qa_slice:
                break
    if selected is None:
        raise ValueError(f"plan {plan.feature_id} has no engineering.qa.author slice")
    task_id = f"eval-{uuid.uuid4().hex}"
    return TaskContract(
        feature_id=plan.feature_id,
        plan_contract_id=f"{plan.feature_id}:planner-eval",
        role=selected.role,
        task_type=selected.task_type,
        objective=selected.objective,
        inputs={"task_id": task_id, "task_slice_id": selected.slice_id},
        allowed_paths=selected.allowed_paths,
        forbidden_paths=selected.forbidden_paths,
        dependencies=selected.depends_on,
        expected_outputs=selected.expected_outputs,
        verification_commands=selected.verification_commands,
    )


def _prompt(
    plan: PlanContract,
    task_contract: TaskContract,
    verification_command: list[str],
    *,
    qa_author_brief: dict[str, Any],
) -> str:
    example_test_path = _example_test_path(task_contract.allowed_paths)
    contract_skeleton = {
        "contract_version": CONTRACT_VERSION,
        "feature_id": task_contract.feature_id,
        "task_id": task_contract.inputs["task_id"],
        "tests_added": [
            f"{example_test_path}::test_acceptance_criterion_one",
            f"{example_test_path}::test_acceptance_criterion_two",
        ],
        "matrix_coverage": {
            criterion: ["one_or_more_test_function_names_covering_this_criterion"]
            for criterion in plan.acceptance_test_matrix
        },
        "red_proof": [
            {
                "command": verification_command,
                "exit_code": 1,
                "failure_excerpt": "orchestrator will replace this with canonical red proof",
            }
        ],
        "paths_touched": [example_test_path],
        "branch": "",
        "worktree_path": "",
        "model_usage_ids": [],
    }
    return json.dumps(
        {
            "role": "qa.author",
            "objective": task_contract.objective,
            "rules": [
                "Edit only paths allowed by task_contract.allowed_paths.",
                "Do not edit paths forbidden by task_contract.forbidden_paths.",
                "Write failing tests for every acceptance_test_matrix row.",
                "You may run selected_verification_command while authoring tests.",
                (
                    "The orchestrator will rerun selected_verification_command and "
                    "create canonical red_proof."
                ),
                "Do not create wrapper tests whose main assertion is that a command failed.",
                "Return only one valid QAAuthorContract JSON object after editing tests.",
                "Do not wrap the contract in markdown or prose.",
                (
                    "Do not include command logs, exploration notes, file diffs, or "
                    "commentary in the final response."
                ),
                "QAAuthorContract.tests_added is a list of strings, not objects.",
                (
                    "QAAuthorContract.matrix_coverage is an object mapping criteria "
                    "strings to test-name lists."
                ),
                "QAAuthorContract.red_proof is a list of objects, not one object.",
                "Do not leave tests_added, matrix_coverage, or red_proof empty.",
                "Every acceptance_test_matrix row must appear as a matrix_coverage key.",
                "Use the exact feature_id and task_id shown in required_contract_shape.",
                (
                    "paths_touched must list the actual test files you changed; branch, "
                    "worktree_path, and model_usage_ids may be empty."
                ),
            ],
            "required_contract_shape": contract_skeleton,
            "qa_author_brief": qa_author_brief,
            "qa_context_capsule": _qa_context_capsule(qa_author_brief),
            "selected_verification_command": verification_command,
            "plan_contract": plan.model_dump(mode="json"),
            "task_contract": task_contract.model_dump(mode="json"),
        },
        indent=2,
        sort_keys=True,
    )


def _qa_author_brief(
    worktree: Path,
    plan: PlanContract,
    task_contract: TaskContract,
    *,
    project_metadata: dict[str, Any],
) -> dict[str, Any]:
    objective = f"{task_contract.objective}\n{plan.problem_statement}".lower()
    acceptance = "\n".join(plan.acceptance_test_matrix).lower()
    targets = _quality_targets(plan, task_contract)
    qa_metadata = project_qa_metadata(project_metadata)
    quality_gates = [
        "Generated tests must fail for product behavior, not environment setup.",
        "red_proof.failure_excerpt must come from selected_verification_command in this run.",
        "Do not create wrapper tests that assert a shell command failed.",
        "Use project-local test idioms and helpers where they exist.",
        "Only create qa/fixtures files when generated tests read them by path or resource.",
    ]
    quality_gates.extend(_metadata_string_list(qa_metadata, "quality_gates"))
    avoid_patterns = _metadata_string_list(qa_metadata, "avoid_patterns")
    quality_gates.extend(f"Avoid: {item}" for item in avoid_patterns)
    if _requires_endpoint_coverage(objective, acceptance):
        quality_gates.extend(
            [
                (
                    "Endpoint acceptance must be tested at controller/HTTP route level; "
                    "service-method tests may support but must not replace endpoint tests."
                ),
                (
                    "For Spring APIs, prefer MockMvc, WebTestClient, or TestRestTemplate "
                    "over direct controller method calls when endpoint routing semantics matter."
                ),
                (
                    "For every-route API criteria, enumerate the route inventory and map "
                    "each route family to at least one assertion."
                ),
            ]
        )
    endpoint_inventory = _discover_endpoint_inventory(worktree, targets, qa_metadata)
    route_requirements = _route_coverage_requirements(targets, endpoint_inventory, plan)
    benchmark_requirements = benchmark_requirements_for_task(plan, task_contract, qa_metadata)
    deterministic_skeleton = _deterministic_test_skeleton(
        targets=targets,
        route_requirements=route_requirements,
        benchmark_requirements=benchmark_requirements,
        qa_metadata=qa_metadata,
        plan=plan,
    )
    if _requires_browser_coverage(acceptance):
        quality_gates.append(
            "Browser/UI tests must prove the requested user-visible behavior; if API "
            "routes are mocked, assert the UI sends the expected request parameters."
        )
        ui_acceptance = qa_metadata.get("ui_acceptance")
        if isinstance(ui_acceptance, dict):
            level = ui_acceptance.get("min_level")
            if isinstance(level, str):
                quality_gates.append(
                    f"UI acceptance minimum level is {level!r}; satisfy that project contract."
                )
            if ui_acceptance.get("prefer_task_specific_spec"):
                quality_gates.append(
                    "Prefer a focused task-specific browser spec over editing a broad "
                    "existing flow file."
                )
    if targets.get("requires_benchmark_coverage"):
        quality_gates.extend(
            [
                (
                    "Benchmark acceptance must add benchmark-harness tests in the "
                    "project's benchmark roots when those roots are allowed."
                ),
                (
                    "Benchmark methods must allocate no garbage after benchmark setup; "
                    "put object graphs, temp files, fixtures, and collections in setup."
                ),
            ]
        )
    return {
        "coverage_targets": targets,
        "endpoint_inventory": endpoint_inventory,
        "route_coverage_requirements": route_requirements,
        "deterministic_test_skeleton": deterministic_skeleton,
        "benchmark_requirements": benchmark_requirements,
        "generated_route_coverage_artifact": _generated_route_coverage_artifact(route_requirements),
        "existing_test_examples": _discover_test_examples(worktree, targets, qa_metadata),
        "project_qa_metadata": prompt_safe_qa_metadata(qa_metadata),
        "project_authorized_test_support_paths": _metadata_string_list(
            qa_metadata, "test_support_paths"
        ),
        "quality_gates": quality_gates,
    }


def _qa_context_capsule(qa_author_brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": "qa_context_capsule.v1",
        "purpose": "Stable project QA context for this task; prefer this over rediscovery.",
        "coverage_targets": qa_author_brief.get("coverage_targets"),
        "deterministic_test_skeleton": qa_author_brief.get("deterministic_test_skeleton"),
        "benchmark_requirements": qa_author_brief.get("benchmark_requirements"),
        "generated_route_coverage_artifact": qa_author_brief.get(
            "generated_route_coverage_artifact"
        ),
        "existing_test_examples": qa_author_brief.get("existing_test_examples"),
        "quality_gates": qa_author_brief.get("quality_gates"),
    }


def _generated_route_coverage_artifact(
    route_requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract": "generated_route_coverage.v1",
        "producer": "pgloom-engineering.qa_runtime",
        "instructions": [
            "Use these generated route cases as source-of-truth.",
            "Do not ask the model to infer or rewrite the route inventory.",
            (
                "Tests may include this list as an audit helper only after behavior "
                "coverage invokes the matching route cases."
            ),
        ],
        "requirements": route_requirements,
    }


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _quality_targets(plan: PlanContract, task_contract: TaskContract) -> dict[str, Any]:
    text = "\n".join(
        [
            plan.problem_statement,
            task_contract.objective,
            *plan.acceptance_test_matrix,
            *plan.affected_surfaces,
            *task_contract.allowed_paths,
            *task_contract.expected_outputs,
        ]
    )
    return {
        "api_prefixes": _api_prefixes_from_text(text),
        "keywords": _keywords_from_text(text),
        "requires_endpoint_coverage": _requires_endpoint_coverage(text.lower(), text.lower()),
        "requires_browser_coverage": _requires_browser_coverage(text.lower()),
        "requires_benchmark_coverage": _requires_benchmark_coverage(text.lower()),
    }


def _api_prefixes_from_text(text: str) -> list[str]:
    prefixes: set[str] = set()
    for raw in text.replace("*", " ").replace(",", " ").split():
        token = raw.strip("`'\"()[]{}.,;:")
        if not token.startswith("/api/"):
            continue
        parts = [part for part in token.split("/") if part and part != "..."]
        if len(parts) >= 2:
            prefixes.add("/" + "/".join(parts[:2]))
        else:
            prefixes.add(token.rstrip("/"))
    return sorted(prefixes)


def _keywords_from_text(text: str) -> list[str]:
    words = set()
    for raw in text.replace("/", " ").replace("_", " ").replace("-", " ").split():
        word = raw.strip("`'\"()[]{}.,;:").lower()
        if len(word) < 4 or word in _STOPWORDS:
            continue
        if any(ch.isdigit() for ch in word):
            continue
        words.add(word)
    return sorted(words)[:24]


_STOPWORDS = {
    "acceptance",
    "allowed",
    "author",
    "before",
    "command",
    "coverage",
    "criteria",
    "domain",
    "every",
    "existing",
    "final",
    "paths",
    "prove",
    "tests",
    "with",
}


def _discover_endpoint_inventory(
    worktree: Path,
    targets: dict[str, Any],
    qa_metadata: dict[str, Any],
) -> list[str]:
    wanted_prefixes = [str(item) for item in targets.get("api_prefixes", [])]
    if not wanted_prefixes:
        return []
    return route_inventory_for_prompt(
        discover_route_inventory(
            worktree,
            {"qa": qa_metadata},
            api_prefixes=wanted_prefixes,
        ),
        limit=80,
    )


def _route_coverage_requirements(
    targets: dict[str, Any],
    endpoint_inventory: list[str],
    plan: PlanContract,
) -> list[dict[str, Any]]:
    prefixes = [str(item) for item in targets.get("api_prefixes", [])]
    if not prefixes:
        return []
    acceptance = "\n".join(plan.acceptance_test_matrix).lower()
    require_all = "every existing" in acceptance or "every " in acceptance
    requirements: list[dict[str, Any]] = []
    for prefix in prefixes:
        routes = [
            line
            for line in endpoint_inventory
            if f" {prefix.rstrip('/')}/" in line or f" {prefix.rstrip('/')}" in line
        ]
        if routes:
            requirements.append(
                {
                    "api_prefix": prefix,
                    "required_routes": routes,
                    "coverage_rule": "all_routes" if require_all else "representative_routes",
                    "authoring_instruction": (
                        "For all_routes, include each route literal or an equivalent route "
                        "tail token in tests, and cover each domain/parameter named by the "
                        "acceptance criterion."
                    ),
                }
            )
    return requirements


def _deterministic_test_skeleton(
    *,
    targets: dict[str, Any],
    route_requirements: list[dict[str, Any]],
    benchmark_requirements: list[dict[str, Any]],
    qa_metadata: dict[str, Any],
    plan: PlanContract,
) -> dict[str, Any]:
    skeletons = qa_metadata.get("preferred_test_skeletons")
    helpers = qa_metadata.get("preferred_helpers")
    rules = qa_metadata.get("behavior_coverage_rules")
    endpoint_required = bool(route_requirements)
    browser_required = bool(targets.get("requires_browser_coverage"))
    domains = _domains_from_plan(plan)
    result: dict[str, Any] = {
        "purpose": (
            "Local deterministic scaffold. Follow this shape before adding project-specific "
            "fixtures and assertions; do not replace behavior coverage with inventory-only tests."
        ),
        "required_domains": domains,
        "endpoint_behavior_skeleton": [],
        "browser_behavior_skeleton": [],
        "benchmark_behavior_skeleton": list(benchmark_requirements),
    }
    if isinstance(skeletons, dict):
        result["preferred_test_skeletons"] = skeletons
    if isinstance(helpers, (dict, list)):
        result["preferred_helpers"] = helpers
    if isinstance(rules, list):
        result["behavior_coverage_rules"] = rules
    if endpoint_required:
        conventions = qa_metadata.get("semantic_conventions")
        endpoint_acceptance = (
            conventions.get("endpoint_acceptance")
            if isinstance(conventions, dict)
            else None
        )
        if isinstance(endpoint_acceptance, dict) and endpoint_acceptance.get(
            "require_http_harness"
        ):
            result["spring_endpoint_harness_required"] = {
                "allowed_harnesses": ["MockMvc", "WebTestClient", "TestRestTemplate"],
                "test_support_dependency_guidance": (
                    "If the selected harness dependency is missing and project_authorized_"
                    "test_support_paths is non-empty, add only test-scoped dependencies in "
                    "those support files. Do not fall back to direct controller method calls."
                ),
            }
        for requirement in route_requirements:
            route_cases = [
                {
                    "method": _route_method(line),
                    "path": _route_path(line),
                    "behavior_requirement": (
                        "Invoke the matching controller/HTTP route for each required domain "
                        "and assert domain-specific identifiers/config/partition/service state."
                    ),
                }
                for line in requirement.get("required_routes", [])
                if isinstance(line, str)
            ]
            result["endpoint_behavior_skeleton"].append(
                {
                    "api_prefix": requirement.get("api_prefix"),
                    "coverage_rule": requirement.get("coverage_rule"),
                    "route_cases": route_cases,
                    "anti_pattern": (
                        "Do not create a test that only asserts this route_cases list; "
                        "each case must drive product behavior."
                    ),
                }
            )
    if browser_required:
        ui_acceptance = qa_metadata.get("ui_acceptance")
        result["browser_behavior_skeleton"].append(
            {
                "workflow": "domain_selector_switch",
                "min_level": (
                    ui_acceptance.get("min_level")
                    if isinstance(ui_acceptance, dict)
                    else "request_shape"
                ),
                "required_assertions": [
                    "visible equities state before switch",
                    "visible crypto state after switch",
                    "request URL/query includes domain=crypto for the relevant API call",
                ],
                "mocking_rule": (
                    "If API calls are mocked, the test must still assert request shape and "
                    "visible UI state; do not rely on fixtures alone."
                ),
                "preferred_file_shape": (
                    "focused task-specific spec"
                    if isinstance(ui_acceptance, dict)
                    and ui_acceptance.get("prefer_task_specific_spec")
                    else "project-local browser test"
                ),
            }
        )
    if targets.get("requires_benchmark_coverage") and not benchmark_requirements:
        result["benchmark_behavior_skeleton"].append(
            {
                "workflow": "benchmark_acceptance",
                "required_assertions": [
                    "benchmark fixture state is created before measured iterations",
                    "measured benchmark method performs only the operation under test",
                    "no collections, temp files, object graphs, or fixture data are "
                    "allocated in the measured method",
                    "benchmark variants cover every implementation family named by acceptance",
                ],
                "allocation_rule": (
                    "For JMH, use @Setup for object graphs and fixtures; @Benchmark "
                    "methods must be zero-garbage after setup."
                ),
            }
        )
    return result


def _domains_from_plan(plan: PlanContract) -> list[str]:
    text = "\n".join([plan.problem_statement, *plan.acceptance_test_matrix]).lower()
    domains = [domain for domain in ["equities", "crypto"] if domain in text]
    return domains or ["default"]


def _route_method(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[0] if parts else "ANY"


def _route_path(route_line: str) -> str:
    parts = route_line.split(maxsplit=2)
    return parts[1] if len(parts) > 1 else route_line


def _quoted_api_paths(text: str) -> list[str]:
    paths: list[str] = []
    for quote in ['"', "'"]:
        parts = text.split(quote)
        for index, part in enumerate(parts):
            if index % 2 == 1 and part.startswith("/api/"):
                paths.append(part)
    return paths


def _discover_test_examples(
    worktree: Path,
    targets: dict[str, Any],
    qa_metadata: dict[str, Any],
) -> list[str]:
    examples: list[str] = []
    terms = [str(item).lower() for item in targets.get("keywords", [])]
    explicit_examples = _metadata_paths(
        worktree,
        qa_metadata,
        "example_tests",
        "helper_files",
    )
    examples.extend(path.relative_to(worktree).as_posix() for path in explicit_examples)
    for root in _test_roots(worktree, qa_metadata):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in _TEST_SUFFIXES:
                continue
            rel = path.relative_to(worktree).as_posix()
            haystack = f"{path.name}\n{rel}".lower()
            if not terms or any(term in haystack for term in terms):
                examples.append(path.relative_to(worktree).as_posix())
    examples.extend(_browser_helper_examples(worktree, qa_metadata))
    return sorted(dict.fromkeys(examples))[:24]


_SOURCE_SUFFIXES = {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}
_TEST_SUFFIXES = _SOURCE_SUFFIXES | {".feature"}


def _source_roots(worktree: Path, qa_metadata: dict[str, Any] | None = None) -> list[Path]:
    roots: list[Path] = _metadata_paths(
        worktree,
        qa_metadata or {},
        "source_roots",
        "endpoint_roots",
    )
    for marker in ["src/main", "app", "apps", "packages", "server", "api"]:
        roots.extend(path for path in worktree.rglob(marker) if path.is_dir())
    return sorted(dict.fromkeys(roots))[:40]


def _test_roots(worktree: Path, qa_metadata: dict[str, Any] | None = None) -> list[Path]:
    roots: list[Path] = _metadata_paths(
        worktree,
        qa_metadata or {},
        "test_roots",
        "browser_test_roots",
    )
    for marker in ["src/test", "tests", "test", "__tests__", "e2e"]:
        roots.extend(path for path in worktree.rglob(marker) if path.is_dir())
    return sorted(dict.fromkeys(roots))[:80]


def _metadata_paths(worktree: Path, metadata: dict[str, Any], *keys: str) -> list[Path]:
    paths: list[Path] = []
    for key in keys:
        for raw in _metadata_string_list(metadata, key):
            path = Path(raw)
            if not path.is_absolute():
                path = worktree / path
            if path.exists():
                paths.append(path)
    return sorted(dict.fromkeys(paths))


def _iter_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in _SOURCE_SUFFIXES
        and "node_modules" not in path.parts
        and "build" not in path.parts
    ][:5000]


def _browser_helper_examples(worktree: Path, qa_metadata: dict[str, Any]) -> list[str]:
    examples: list[str] = []
    for root in _test_roots(worktree, qa_metadata):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                if "page.route(" in text or "mock" in path.name.lower():
                    examples.append(path.relative_to(worktree).as_posix())
    return examples[:12]


def _model_command(
    args: argparse.Namespace,
    worktree: Path,
    *,
    sandbox: str = "workspace-write",
    output_last_message: Path | None = None,
) -> list[str]:
    if args.backend == "claude":
        return _claude_command(args.model, args.claude_max_budget_usd, sandbox=sandbox)
    return _codex_command(
        args.model,
        args.reasoning,
        worktree,
        sandbox=sandbox,
        output_last_message=output_last_message,
    )


def _codex_command(
    model: str,
    reasoning: str,
    worktree: Path,
    *,
    sandbox: str = "workspace-write",
    output_last_message: Path | None = None,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "-s",
        sandbox,
        "-C",
        str(worktree),
        "--ephemeral",
        "--json",
        "-",
    ]
    if output_last_message is not None:
        command[-1:-1] = ["--output-last-message", str(output_last_message)]
    return command


def _final_model_response(
    stdout: str,
    *,
    final_message_path: Path,
    event_log_path: Path,
    backend: str,
) -> str:
    if backend == "codex":
        event_log_path.write_text(stdout, encoding="utf-8")
        if final_message_path.is_file():
            text = final_message_path.read_text(encoding="utf-8")
            if text.strip():
                return text
    return stdout


def _claude_command(model: str, max_budget_usd: str, *, sandbox: str) -> list[str]:
    command = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--max-budget-usd",
        max_budget_usd,
        "--permission-mode",
        "bypassPermissions",
    ]
    if sandbox == "read-only":
        command.extend(["--tools", ""])
    return command


def _evaluate(
    *,
    stdout: str,
    subprocess_returncode: int,
    worktree: Path,
    plan: PlanContract,
    task_contract: TaskContract,
    usage: dict[str, Any],
    verification_command: list[str],
    project_metadata: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    model_error = _model_error(stdout)
    if model_error is not None:
        findings.append(model_error)
    touched = _relevant_changed_files(worktree, project_metadata)
    findings.extend(path_violations(touched, task_contract, project_metadata))
    contract: QAAuthorContract | None = None
    if model_error is None:
        try:
            contract = QAAuthorContract.model_validate(
                normalize_qa_author_payload(extract_json(_model_text(stdout)))
            )
            contract = _align_matrix_coverage_to_acceptance(contract, plan.acceptance_test_matrix)
            contract = add_configured_gate_matrix_coverage(
                contract,
                plan=plan,
                worktree=worktree,
                project_metadata=project_metadata,
                task_contract=task_contract,
            )
        except Exception as exc:
            findings.append({"code": "invalid_qa_author_contract", "message": str(exc)})
    if contract is not None:
        missing = [
            criterion
            for criterion in plan.acceptance_test_matrix
            if not contract.matrix_coverage.get(criterion)
        ]
        if missing:
            findings.append({"code": "missing_matrix_coverage", "criteria": missing})
        if not contract.tests_added:
            findings.append({"code": "missing_tests_added"})
    verification = _best_red_verification(
        verification_command=verification_command,
        changed_files=touched,
        task_contract=task_contract,
        worktree=worktree,
        project_metadata=project_metadata,
    )
    if contract is not None:
        contract = contract.model_copy(update={"red_proof": canonical_red_proof(verification)})
    if verification.infra_error is not None:
        findings.append(
            {
                "code": "verification_infra_error",
                "message": verification.infra_error,
                "command": verification_command,
            }
        )
    if not is_red_test_failure(verification):
        if is_authored_test_compile_failure(verification):
            findings.append(
                {
                    "code": "qa_tests_do_not_compile",
                    "message": (
                        "authored QA tests do not compile; QA author must self-validate "
                        "and repair compile/import/syntax errors before submission"
                    ),
                    "command": verification.original.argv,
                }
            )
        else:
            findings.append(
                {
                    "code": "tests_not_red",
                    "message": "verification command did not prove a real failing test",
                    "command": verification.original.argv,
                }
            )
    verdict = "accept" if subprocess_returncode == 0 and not findings else "revise"
    return {
        "verdict": verdict,
        "findings": findings,
        "changed_files": touched,
        "pytest_exit_code": verification.original.exit_code,
        "pytest_stdout_excerpt": verification.stdout_excerpt,
        "pytest_stderr_excerpt": verification.stderr_excerpt,
        "usage": usage,
        "qa_author_contract": contract.model_dump(mode="json") if contract is not None else None,
    }


def _run_red_repair_phase(
    *,
    args: argparse.Namespace,
    repo: Path,
    output_dir: Path,
    file_prefix: str,
    phase: str,
    worktree: Path,
    plan: PlanContract,
    task_contract: TaskContract,
    outcome: dict[str, Any],
    records: list[dict[str, Any]],
    verification_command: list[str],
    project_metadata: dict[str, Any],
) -> dict[str, Any]:
    prompt = build_qa_code_repair_prompt(
        plan=plan,
        task_contract=task_contract,
        worktree=worktree,
        changed_files=outcome["changed_files"],
        verification_command=verification_command,
        stdout_excerpt=outcome["pytest_stdout_excerpt"],
        stderr_excerpt=outcome["pytest_stderr_excerpt"],
        current_contract=outcome.get("qa_author_contract"),
        project_metadata=project_metadata,
    )
    (output_dir / f"{file_prefix}.prompt.txt").write_text(prompt, encoding="utf-8")
    started = time.monotonic()
    final_message_path = output_dir / f"{file_prefix}.final.txt"
    event_log_path = output_dir / f"{file_prefix}.events.jsonl"
    completed = subprocess.run(
        _model_command(args, worktree, output_last_message=final_message_path),
        input=prompt,
        text=True,
        capture_output=True,
        timeout=args.timeout_seconds,
        check=False,
        cwd=worktree,
        env={**os.environ, **qa_env(project_metadata, project_root=repo)},
    )
    elapsed = round(time.monotonic() - started, 3)
    response = _final_model_response(
        completed.stdout,
        final_message_path=final_message_path,
        event_log_path=event_log_path,
        backend=args.backend,
    )
    (output_dir / f"{file_prefix}.response.txt").write_text(response, encoding="utf-8")
    (output_dir / f"{file_prefix}.stderr.txt").write_text(
        completed.stderr,
        encoding="utf-8",
    )
    repair_usage = _usage(
        backend=args.backend,
        model=args.model,
        reasoning=args.reasoning,
        elapsed_seconds=elapsed,
        prompt=prompt,
        response=completed.stdout,
        pricing=_load_pricing(args.pricing_file),
    )
    repair_usage["phase"] = phase
    records.append(repair_usage)
    return _evaluate(
        stdout=response,
        subprocess_returncode=completed.returncode,
        worktree=worktree,
        plan=plan,
        task_contract=task_contract,
        usage=_combined_usage(records),
        verification_command=verification_command,
        project_metadata=project_metadata,
    )


def _best_red_verification(
    *,
    verification_command: list[str],
    changed_files: list[str],
    task_contract: TaskContract,
    worktree: Path,
    project_metadata: dict[str, Any],
) -> Any:
    results = [
        run_qa_verification(
            command,
            worktree=worktree,
            project_metadata=project_metadata,
            timeout_seconds=300,
        )
        for command in red_proof_verification_commands(
            task_contract,
            changed_files,
            selected_command=verification_command,
        )
    ]
    for result in results:
        if is_red_test_failure(result):
            return result
    return results[0]


def _verification_command(task_contract: TaskContract, index: int) -> list[str]:
    if task_contract.verification_commands:
        if index < 0 or index >= len(task_contract.verification_commands):
            raise ValueError(f"verification index {index} out of range")
        return task_contract.verification_commands[index]
    return [sys.executable, "-m", "pytest", "tests", "-q"]


def _example_test_path(allowed_paths: list[str]) -> str:
    preferred_suffixes = ("src/test/java/", "src/test/", "ui/tests/", "tests/", "qa/fixtures/")
    for suffix in preferred_suffixes:
        for path in allowed_paths:
            normalized = path.rstrip("/") + "/"
            if normalized.endswith(suffix) or normalized == suffix:
                if suffix.endswith("java/"):
                    return f"{normalized}QAAuthorAcceptanceTest.java"
                if suffix == "ui/tests/":
                    return f"{normalized}qa-author-acceptance.spec.ts"
                if suffix == "qa/fixtures/":
                    return f"{normalized}qa-author-fixture.json"
                return f"{normalized}test_qa_author_acceptance.py"
    first = allowed_paths[0].rstrip("/") if allowed_paths else "tests"
    return f"{first}/test_qa_author_acceptance.py"


def _archive_changed_files(
    *,
    output_dir: Path,
    worktree: Path,
    changed_files: list[str],
) -> dict[str, Any]:
    artifact_root = output_dir / "changed-files"
    archived: list[str] = []
    for path in changed_files:
        source = worktree / path
        if not source.is_file():
            continue
        target = artifact_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        archived.append(str(target.relative_to(output_dir)))
    diff_path = output_dir / "changed-files.diff"
    diff_path.write_text(
        _changed_files_diff(worktree, changed_files),
        encoding="utf-8",
    )
    return {
        "changed_files": archived,
        "diff": str(diff_path.relative_to(output_dir)),
    }


def _changed_files_diff(worktree: Path, changed_files: list[str]) -> str:
    tracked: list[str] = []
    untracked: list[str] = []
    for path in changed_files:
        if _is_git_tracked(worktree, path):
            tracked.append(path)
        else:
            untracked.append(path)

    parts: list[str] = []
    if tracked:
        diff = run_bounded(["git", "diff", "--", *tracked], cwd=worktree, timeout_seconds=30)
        if diff.stdout:
            parts.append(diff.stdout.rstrip())
    for path in untracked:
        source = worktree / path
        if not source.is_file():
            continue
        diff = run_bounded(
            ["git", "diff", "--no-index", "--", "/dev/null", path],
            cwd=worktree,
            timeout_seconds=30,
        )
        if diff.stdout:
            parts.append(diff.stdout.rstrip())
    return "\n".join(parts) + ("\n" if parts else "")


def _is_git_tracked(worktree: Path, path: str) -> bool:
    result = run_bounded(
        ["git", "ls-files", "--error-unmatch", "--", path],
        cwd=worktree,
        timeout_seconds=10,
    )
    return result.exit_code == 0


def _review_qa_author_quality(
    *,
    output_dir: Path,
    worktree: Path,
    plan: PlanContract,
    task_contract: TaskContract,
    outcome: dict[str, Any],
    project_metadata: dict[str, Any],
) -> dict[str, Any]:
    artifacts = outcome.get("artifacts")
    artifact_paths = artifacts.get("changed_files", []) if isinstance(artifacts, dict) else []
    files = _artifact_file_contents(output_dir, artifact_paths)
    warnings: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    combined = "\n".join(files.values())
    behavior_combined = _without_inventory_only_blocks(files)
    objective = f"{task_contract.objective}\n{plan.problem_statement}\n".lower()
    acceptance = "\n".join(plan.acceptance_test_matrix).lower()
    targets = _quality_targets(plan, task_contract)
    api_prefixes = [str(item) for item in targets.get("api_prefixes", [])]
    route_inventory = discover_route_inventory(
        worktree,
        project_metadata,
        api_prefixes=api_prefixes,
    )
    expected_route_paths = _expected_route_paths(route_inventory, plan)

    for prefix in api_prefixes:
        expected_paths = [
            route_path
            for route_path in expected_route_paths
            if route_path.startswith(prefix.rstrip("/"))
        ]
        if not expected_paths:
            continue
        missing = [
            route_path
            for route_path in expected_paths
            if not _route_path_referenced(route_path, behavior_combined)
        ]
        if missing:
            blocking.append(
                {
                    "code": "qa_review_route_inventory_gap",
                    "message": (
                        f"Acceptance asks for broad route coverage under {prefix}, "
                        "but generated tests do not reference all required routes."
                    ),
                    "api_prefix": prefix,
                    "missing_routes": missing,
                }
            )
        inventory_only = [
            route_path
            for route_path in expected_paths
            if _route_path_referenced(route_path, combined)
            and not _route_path_referenced(route_path, behavior_combined)
        ]
        if inventory_only:
            blocking.append(
                {
                    "code": "qa_review_route_inventory_only",
                    "message": (
                        f"Tests mention routes under {prefix} only in inventory/list assertions; "
                        "route coverage must be tied to controller or HTTP behavior."
                    ),
                    "api_prefix": prefix,
                    "inventory_only_routes": inventory_only,
                }
            )
    if _requires_endpoint_coverage(objective, acceptance):
        code_files = {
            path: text for path, text in files.items() if Path(path).suffix in _SOURCE_SUFFIXES
        }
        endpoint_tests = [
            path for path, text in code_files.items() if _exercises_endpoint_layer(path, text)
        ]
        if code_files and not endpoint_tests:
            blocking.append(
                {
                    "code": "qa_review_endpoint_layer_not_exercised",
                    "message": (
                        "Brief asks for API endpoint coverage, but Java tests primarily "
                        "exercise service methods instead of controller/HTTP route behavior."
                    ),
                }
            )
        elif endpoint_tests and _java_spring_endpoint_harness_preferred(code_files, endpoint_tests):
            warnings.append(
                {
                    "code": "qa_review_endpoint_harness_preferred",
                    "message": (
                        "Java endpoint acceptance appears to use direct controller calls; "
                        "prefer MockMvc, WebTestClient, or TestRestTemplate when Spring "
                        "route semantics are part of the contract."
                    ),
                    "endpoint_test_files": [
                        path for path in endpoint_tests if Path(path).suffix == ".java"
                    ],
                }
            )
    unconsumed_fixtures = _unconsumed_generated_fixtures(files)
    if unconsumed_fixtures:
        blocking.append(
            {
                "code": "qa_review_unconsumed_fixture",
                "message": (
                    "Generated fixture files are not referenced by generated tests; "
                    "fixtures must support behavior assertions, not exist as detached artifacts."
                ),
                "fixture_files": unconsumed_fixtures,
            }
        )
    if _requires_browser_coverage(acceptance):
        ts_files = {path: text for path, text in files.items() if path.endswith((".ts", ".tsx"))}
        if ts_files and all(_playwright_is_fully_route_mocked(text) for text in ts_files.values()):
            blocking.append(
                {
                    "code": "qa_review_playwright_mocked_only",
                    "message": (
                        "Playwright coverage is fully route-mocked; it does not prove "
                        "real API-backed domain switching."
                    ),
                }
            )
        blocking.extend(_ui_quality_findings(ts_files, project_metadata))
    if "invalid-domain" in acceptance or "invalid or unknown domain" in acceptance:
        if "bad_request" in combined.lower() or "BAD_REQUEST" in combined:
            warnings.append(
                {
                    "code": "qa_review_invalid_domain_contract_assumption",
                    "message": (
                        "Generated tests assume BAD_REQUEST invalid-domain behavior; "
                        "verify this matches the project's current validation convention."
                    ),
                }
            )
    if _uses_brittle_string_json_assertions(files):
        warnings.append(
            {
                "code": "qa_review_brittle_string_json_assertions",
                "message": "Tests rely heavily on raw string containment for JSON/YAML payloads.",
            }
        )
    if _red_proof_infra_error_from_outcome(outcome) is not None:
        blocking.append(
            {
                "code": "qa_review_stale_red_proof_excerpt",
                "message": "QAAuthorContract red_proof excerpt contains an infrastructure failure.",
            }
        )
    if targets.get("requires_benchmark_coverage"):
        blocking.extend(_benchmark_quality_findings(files, task_contract, plan))
    gate_validation = validate_required_qa_gates(worktree, project_metadata)
    gate_findings = [
        {
            "code": "qa_gate_validation_failed",
            "message": "Required project QA gate is not deterministically configured.",
            "gate_id": item.get("gate_id"),
            "missing": item.get("missing"),
            "command": item.get("command"),
        }
        for item in gate_validation
        if item.get("status") != "configured"
    ]
    blocking.extend(gate_findings)
    semantic_findings = semantic_quality_findings(
        worktree=worktree,
        changed_paths=_worktree_paths_from_archived_files(files),
        plan=plan,
        task_contract=task_contract,
        project_metadata=project_metadata,
    )
    for finding in semantic_findings:
        if finding.get("severity") == "blocking":
            blocking.append(finding)
        else:
            warnings.append(finding)
    return {
        "blocking_findings": blocking,
        "warnings": warnings,
        "gate_validation": gate_validation,
        "reviewed_artifacts": sorted(files),
    }


def _worktree_paths_from_archived_files(files: dict[str, str]) -> list[str]:
    return [
        path.removeprefix("changed-files/")
        for path in files
        if path.startswith("changed-files/")
    ]


def _artifact_file_contents(output_dir: Path, artifact_paths: list[Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for raw_path in artifact_paths:
        if not isinstance(raw_path, str):
            continue
        path = output_dir / raw_path
        if path.is_file():
            files[raw_path] = path.read_text(encoding="utf-8", errors="replace")
    return files


def _expected_route_paths(routes: list[dict[str, str]], plan: PlanContract) -> list[str]:
    acceptance = "\n".join(plan.acceptance_test_matrix).lower()
    if "every existing" not in acceptance and "every " not in acceptance:
        return []
    return sorted({route["path"] for route in routes if route.get("path")})


def _route_path_referenced(route_path: str, text: str) -> bool:
    if route_path in text:
        return True
    compact_tail = route_path.removeprefix("/api/").replace("/", " ").replace("-", " ").lower()
    haystack = text.replace("/", " ").replace("-", " ").lower()
    tail_terms = [term for term in compact_tail.split() if term]
    return bool(tail_terms) and all(term in haystack for term in tail_terms)


def _without_inventory_only_blocks(files: dict[str, str]) -> str:
    stripped = []
    for text in files.values():
        stripped.append(_strip_inventory_only_java_methods(text))
    return "\n".join(stripped)


def _strip_inventory_only_java_methods(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        lowered = line.lower()
        if (
            "@test" in lowered
            and index + 1 < len(lines)
            and "routeinventory" in lines[index + 1].replace("_", "").lower()
        ):
            index += 2
            brace_depth = 0
            while index < len(lines):
                brace_depth += lines[index].count("{") - lines[index].count("}")
                index += 1
                if brace_depth <= 0 and "}" in lines[index - 1]:
                    break
            continue
        output.append(line)
        index += 1
    return "\n".join(output)


def _requires_endpoint_coverage(objective: str, acceptance: str) -> bool:
    return any(token in f"{objective}\n{acceptance}" for token in ["/api/", "endpoint", "route"])


def _requires_browser_coverage(text: str) -> bool:
    return any(token in text for token in ["playwright", "browser", "e2e", "ui ", " ui"])


def _requires_benchmark_coverage(text: str) -> bool:
    return any(
        token in text
        for token in [
            "benchmark",
            "jmh",
            "latency",
            "throughput",
            "performance",
            "allocation gate",
            "zero allocation",
            "zero-allocation",
        ]
    )


def _exercises_endpoint_layer(path: str, text: str) -> bool:
    suffix = Path(path).suffix
    if suffix == ".java":
        return _java_exercises_endpoint_layer(path, text)
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        endpoint_markers = [
            "supertest",
            "request(",
            "fetch(",
            "axios.",
            "app.inject",
            "server.inject",
        ]
        return any(marker in text for marker in endpoint_markers)
    if suffix == ".py":
        return any(marker in text for marker in ["client.", "TestClient", "requests.", "httpx."])
    return False


def _java_exercises_endpoint_layer(path: str, text: str) -> bool:
    if "/web/" in path:
        return True
    endpoint_markers = [
        "MockMvc",
        "@WebMvcTest",
        "TestRestTemplate",
        "WebTestClient",
        "Controller",
        "ResponseEntity",
    ]
    service_only_markers = [
        "new ",
        "Service",
        "Json(",
    ]
    has_endpoint = any(marker in text for marker in endpoint_markers)
    service_marker_count = sum(text.count(marker) for marker in service_only_markers)
    return has_endpoint and service_marker_count <= 20


def _java_spring_endpoint_harness_preferred(
    code_files: dict[str, str],
    endpoint_tests: list[str],
) -> bool:
    java_endpoint_tests = [path for path in endpoint_tests if Path(path).suffix == ".java"]
    if not java_endpoint_tests:
        return False
    combined = "\n".join(code_files.values())
    spring_signal = any(
        marker in combined
        for marker in [
            "@GetMapping",
            "@PostMapping",
            "@PutMapping",
            "@DeleteMapping",
            "@PatchMapping",
            "@RequestMapping",
            "ResponseEntity",
            "Controller",
        ]
    )
    if not spring_signal:
        return False
    harness_signal = any(
        marker in combined
        for marker in [
            "MockMvc",
            "@WebMvcTest",
            "WebTestClient",
            "TestRestTemplate",
            "@SpringBootTest",
            "mockMvc.perform",
        ]
    )
    return not harness_signal


def _unconsumed_generated_fixtures(files: dict[str, str]) -> list[str]:
    fixture_paths = sorted(path for path in files if path.startswith("changed-files/qa/fixtures/"))
    if not fixture_paths:
        return []
    test_text = "\n".join(
        text
        for path, text in files.items()
        if path not in fixture_paths
        and Path(path).suffix in {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}
    )
    unconsumed = []
    for path in fixture_paths:
        fixture = Path(path)
        relative = str(Path(*fixture.parts[2:])) if len(fixture.parts) > 2 else fixture.name
        candidates = {
            path,
            str(fixture),
            relative,
            fixture.name,
            fixture.stem,
        }
        if not any(candidate and candidate in test_text for candidate in candidates):
            unconsumed.append(path)
    return unconsumed


def _ui_quality_findings(
    ts_files: dict[str, str],
    project_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if not ts_files:
        return []
    qa_metadata = project_qa_metadata(project_metadata)
    ui_acceptance = qa_metadata.get("ui_acceptance")
    config = ui_acceptance if isinstance(ui_acceptance, dict) else {}
    findings: list[dict[str, Any]] = []
    if config.get("prefer_task_specific_spec"):
        broad_files = [
            path for path, text in ts_files.items() if _looks_like_broad_browser_flow(path, text)
        ]
        if broad_files:
            findings.append(
                {
                    "code": "qa_review_broad_existing_ui_spec_modified",
                    "message": (
                        "Browser acceptance edited a broad flow spec; project metadata "
                        "prefers focused task-specific specs for generated QA tests."
                    ),
                    "files": broad_files,
                }
            )
    min_level = str(config.get("min_level") or "request_shape")
    if min_level == "request_shape":
        weak_files = [
            path
            for path, text in ts_files.items()
            if "/api/" in text and not _playwright_has_request_shape_assertion(text)
        ]
        if weak_files:
            findings.append(
                {
                    "code": "qa_review_playwright_missing_request_shape",
                    "message": (
                        "Browser tests mock API routes but do not assert the emitted "
                        "request URL/query shape required by UI acceptance."
                    ),
                    "files": weak_files,
                }
            )
    elif min_level == "integration":
        mocked_files = [
            path for path, text in ts_files.items() if "page.route(" in text and "/api/" in text
        ]
        has_real_api_wait = any(_playwright_has_real_api_wait(text) for text in ts_files.values())
        if mocked_files and not has_real_api_wait:
            findings.append(
                {
                    "code": "qa_review_playwright_integration_required",
                    "message": (
                        "Project metadata requires UI integration coverage; generated "
                        "browser tests only mock API routes."
                    ),
                    "files": mocked_files,
                }
            )
    return findings


def _looks_like_broad_browser_flow(path: str, text: str) -> bool:
    name = Path(path).name.lower()
    test_count = text.count("test(") + text.count("test.describe(")
    return name in {"flows.spec.ts", "flow.spec.ts", "e2e.spec.ts"} or test_count >= 4


def _playwright_has_request_shape_assertion(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "waitForRequest",
            "waitForResponse",
            "request.url()",
            "route.request().url()",
            "requestedUrls",
            "expect.poll",
            "searchParams.get",
        ]
    )


def _playwright_has_real_api_wait(text: str) -> bool:
    return any(marker in text for marker in ["waitForResponse", "waitForRequest"])


def _benchmark_quality_findings(
    files: dict[str, str],
    task_contract: TaskContract,
    plan: PlanContract,
) -> list[dict[str, Any]]:
    benchmark_files = {path: text for path, text in files.items() if _is_benchmark_file(path, text)}
    allowed_benchmark_roots = [
        path
        for path in task_contract.allowed_paths
        if "bench" in path.lower() or "jmh" in path.lower()
    ]
    findings: list[dict[str, Any]] = []
    if allowed_benchmark_roots and not benchmark_files:
        findings.append(
            {
                "code": "qa_review_benchmark_missing",
                "message": (
                    "Acceptance requires benchmark evidence and benchmark roots are allowed, "
                    "but generated tests did not add benchmark-harness coverage."
                ),
                "allowed_benchmark_roots": allowed_benchmark_roots,
            }
        )
    for path, text in benchmark_files.items():
        garbage_methods = _jmh_benchmark_methods_with_setup_garbage(text)
        if garbage_methods:
            findings.append(
                {
                    "code": "qa_review_benchmark_allocates_after_setup",
                    "message": (
                        "Benchmark methods allocate or build fixtures inside measured "
                        "iterations; setup must happen outside the measured method."
                    ),
                    "file": path,
                    "methods": garbage_methods,
                }
            )
    findings.extend(_benchmark_variant_findings(benchmark_files, plan, task_contract))
    return findings


def _benchmark_variant_findings(
    benchmark_files: dict[str, str],
    plan: PlanContract,
    task_contract: TaskContract,
) -> list[dict[str, Any]]:
    if not benchmark_files:
        return []
    text = "\n".join(
        [
            plan.problem_statement,
            task_contract.objective,
            *plan.acceptance_test_matrix,
            *task_contract.expected_outputs,
        ]
    ).lower()
    required = [token for token in ["single", "double", "direct", "mmap"] if token in text]
    if len(required) < 2:
        return []
    combined = "\n".join(benchmark_files.values()).lower()
    missing = [token for token in required if token not in combined]
    has_parameterization = (
        "@param" in combined or "arguments.of" in combined or "stream.of" in combined
    )
    if missing or not has_parameterization:
        return [
            {
                "code": "qa_review_benchmark_variant_gap",
                "message": (
                    "Benchmark acceptance names implementation variants, but generated "
                    "benchmark coverage is not parameterized across those variants."
                ),
                "required_variants": required,
                "missing_variants": missing,
                "benchmark_files": sorted(benchmark_files),
            }
        ]
    return []


def _is_benchmark_file(path: str, text: str) -> bool:
    lowered = path.lower()
    return (
        "benchmark" in lowered
        or "benchmarks/" in lowered
        or "src/jmh/" in lowered
        or "@benchmark" in text.lower()
    )


def _jmh_benchmark_methods_with_setup_garbage(text: str) -> list[str]:
    if "@Benchmark" not in text:
        return []
    lines = text.splitlines()
    offenders: list[str] = []
    index = 0
    while index < len(lines):
        if _is_jmh_post_trial_setup_annotation(lines[index]):
            offenders.append(f"{_next_java_method_name(lines, index)} setup")
            index += 1
            continue
        if not _is_jmh_benchmark_annotation(lines[index]):
            index += 1
            continue
        method_start = index + 1
        while method_start < len(lines) and "{" not in lines[method_start]:
            method_start += 1
        if method_start >= len(lines):
            break
        signature = lines[method_start].strip()
        method_name = _java_method_name(signature)
        body_lines = [lines[method_start]]
        depth = lines[method_start].count("{") - lines[method_start].count("}")
        index = method_start + 1
        while index < len(lines) and depth > 0:
            body_lines.append(lines[index])
            depth += lines[index].count("{") - lines[index].count("}")
            index += 1
        body = "\n".join(body_lines)
        if _benchmark_body_allocates(body):
            offenders.append(method_name)
    return offenders


def _java_method_name(signature: str) -> str:
    head = signature.split("(", 1)[0].strip()
    return head.split()[-1] if head.split() else "<unknown>"


def _is_jmh_benchmark_annotation(line: str) -> bool:
    stripped = line.strip()
    return stripped == "@Benchmark" or stripped.startswith("@Benchmark(")


def _is_jmh_post_trial_setup_annotation(line: str) -> bool:
    compact = line.replace(" ", "")
    return "@Setup(Level.Invocation)" in compact or "@Setup(Level.Iteration)" in compact


def _next_java_method_name(lines: list[str], annotation_index: int) -> str:
    index = annotation_index + 1
    while index < len(lines) and "(" not in lines[index]:
        index += 1
    if index >= len(lines):
        return "<unknown>"
    return _java_method_name(lines[index].strip())


def _benchmark_body_allocates(body: str) -> bool:
    allocation_markers = [
        "new ",
        "List.of(",
        "Map.of(",
        "Set.of(",
        "Arrays.asList(",
        "newArrayList",
        "ArrayList<",
        "HashMap<",
        "HashSet<",
        "Files.create",
        "Files.write",
        "Path.of(",
        "UUID.randomUUID(",
        ".stream()",
        ".collect(",
        ".toList(",
    ]
    return any(marker in body for marker in allocation_markers)


def _playwright_is_fully_route_mocked(text: str) -> bool:
    api_routes = text.count("page.route(")
    has_api = "/api/" in text
    real_api_signal = any(
        marker in text
        for marker in [
            "waitForResponse",
            "waitForRequest",
            "toHaveURL",
            "request.url()",
            "route.request().url()",
            "expect.poll",
        ]
    )
    return api_routes >= 3 and has_api and not real_api_signal


def _uses_brittle_string_json_assertions(files: dict[str, str]) -> bool:
    contains_count = sum(text.count(".contains(") for text in files.values())
    structured_count = sum(
        text.count("readTree(") + text.count(".path(") for text in files.values()
    )
    return contains_count >= 8 and structured_count < contains_count


def _red_proof_infra_error_from_outcome(outcome: dict[str, Any]) -> str | None:
    contract = outcome.get("qa_author_contract")
    if not isinstance(contract, dict):
        return None
    red_proof = contract.get("red_proof")
    if not isinstance(red_proof, list):
        return None
    model_proofs = [
        item
        for item in red_proof
        if isinstance(item, dict) and item.get("source") != "orchestrator"
    ]
    return red_proof_infra_error(model_proofs)


def _contract_repairable(outcome: dict[str, Any]) -> bool:
    codes = {finding.get("code") for finding in outcome.get("findings", [])}
    allowed = {
        "invalid_qa_author_contract",
        "missing_matrix_coverage",
        "missing_tests_added",
        "missing_red_proof",
    }
    return bool(codes) and codes <= allowed and bool(outcome.get("changed_files"))


def _apply_repair_state(outcome: dict[str, Any], repair_state: dict[str, bool]) -> None:
    for key, value in repair_state.items():
        outcome[key] = value


def _align_matrix_coverage_to_acceptance(
    contract: QAAuthorContract,
    acceptance_test_matrix: list[str],
) -> QAAuthorContract:
    matrix = dict(contract.matrix_coverage)
    aligned: dict[str, list[str]] = {}
    used_keys: set[str] = set()
    for criterion in acceptance_test_matrix:
        if criterion in matrix:
            aligned[criterion] = matrix[criterion]
            used_keys.add(criterion)
            continue
        match = _single_matrix_key_match(criterion, matrix, used_keys)
        if match is not None:
            aligned[criterion] = matrix[match]
            used_keys.add(match)
    for key, value in matrix.items():
        if key not in used_keys and key not in aligned:
            aligned[key] = value
    if aligned == matrix:
        return contract
    return contract.model_copy(update={"matrix_coverage": aligned})


def _single_matrix_key_match(
    criterion: str,
    matrix: dict[str, list[str]],
    used_keys: set[str],
) -> str | None:
    criterion_norm = _matrix_key_norm(criterion)
    matches = [
        key
        for key in matrix
        if key not in used_keys
        and (
            criterion_norm.startswith(_matrix_key_norm(key))
            or _matrix_key_norm(key).startswith(criterion_norm)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _matrix_key_norm(value: str) -> str:
    return " ".join(value.lower().replace(":", " ").split())


def _repair_prompt(
    *,
    plan: PlanContract,
    task_contract: TaskContract,
    worktree: Path,
    changed_files: list[str],
    pytest_excerpt: str,
    initial_contract: object,
) -> str:
    test_contents = {
        path: (worktree / path).read_text(encoding="utf-8")
        for path in changed_files
        if (worktree / path).exists()
    }
    return json.dumps(
        {
            "role": "qa.author.contract_repair",
            "instructions": [
                "Do not edit files.",
                "Return only a valid QAAuthorContract JSON object.",
                f"contract_version must be the string {CONTRACT_VERSION!r}.",
                "tests_added must be a list of strings, not objects.",
                (
                    "matrix_coverage must be an object mapping each criterion string "
                    "to test-name strings."
                ),
                "red_proof must be a list of objects, not one object.",
                "Populate tests_added, matrix_coverage, and red_proof from the existing red tests.",
                "Every acceptance criterion must map to at least one test.",
                (
                    "red_proof must include pytest command, non-zero exit code, and short "
                    "failure excerpts."
                ),
            ],
            "feature_id": task_contract.feature_id,
            "task_id": task_contract.inputs["task_id"],
            "acceptance_test_matrix": plan.acceptance_test_matrix,
            "changed_files": changed_files,
            "test_file_names": test_contents,
            "pytest_exit_code": 1,
            "pytest_failure_excerpt": pytest_excerpt,
            "initial_contract": initial_contract,
        },
        indent=2,
        sort_keys=True,
    )


def _combined_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    phases = [str(record.get("phase") or "author") for record in records]
    extra_phases = [phase for phase in phases if phase != "author"]
    combined: dict[str, Any] = {
        "backend": records[0].get("backend"),
        "model": records[0].get("model"),
        "reasoning": records[0].get("reasoning"),
        "phase": "author_plus_" + "_plus_".join(extra_phases)
        if extra_phases
        else "author",
        "call_count": len(records),
    }
    for key in [
        "elapsed_seconds",
        "prompt_chars",
        "response_chars",
        "estimated_input_tokens",
        "estimated_output_tokens",
        "actual_input_tokens",
        "actual_output_tokens",
        "actual_total_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "reasoning_output_tokens",
        "total_cost_usd",
        "api_equivalent_cost_usd",
        "cost_without_cache_usd",
        "cache_savings_usd",
    ]:
        values = [record.get(key) for record in records]
        combined[key] = sum(value for value in values if isinstance(value, int | float))
    return combined


def _usage(
    *,
    backend: str,
    model: str,
    reasoning: str,
    elapsed_seconds: float,
    prompt: str,
    response: str,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    parsed_usage = _claude_usage(response) if backend == "claude" else _codex_usage(response)
    record: dict[str, Any] = {
        "backend": backend,
        "model": model,
        "reasoning": reasoning if backend == "codex" else None,
        "elapsed_seconds": elapsed_seconds,
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "estimated_input_tokens": count_tokens(prompt),
        "estimated_output_tokens": count_tokens(response),
        **parsed_usage,
    }
    costs = _cost_fields(record, pricing)
    record.update(costs)
    return record


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
    total_tokens = sum(
        item for item in [input_tokens, output_tokens, cache_creation, cache_read] if item
    )
    return {
        "actual_input_tokens": input_tokens,
        "actual_output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "actual_total_tokens": total_tokens or None,
        "total_cost_usd": payload.get("total_cost_usd"),
    }


def _codex_usage(response: str) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for line in response.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "turn.completed":
            raw = payload.get("usage")
            if isinstance(raw, dict):
                usage = raw
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cached_input_tokens = _int_or_none(usage.get("cached_input_tokens"))
    reasoning_output_tokens = _int_or_none(usage.get("reasoning_output_tokens"))
    return {
        "actual_input_tokens": input_tokens,
        "actual_output_tokens": output_tokens,
        "cache_read_input_tokens": cached_input_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "actual_total_tokens": (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None or output_tokens is not None
        else None,
    }


def _model_text(stdout: str) -> str:
    claude_result = _claude_result_text(stdout)
    if claude_result is not None:
        return claude_result
    result: str | None = None
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item")
        if (
            payload.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            result = item["text"]
    return result or stdout


def _claude_result_text(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, str):
        return result
    text = payload.get("text")
    return text if isinstance(text, str) else None


def _model_error(stdout: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("is_error") is not True:
        return None
    result = payload.get("result")
    return {
        "code": "model_invocation_error",
        "message": result if isinstance(result, str) else "model invocation failed",
    }


def _qa_author_payload(payload: object) -> object:
    if isinstance(payload, dict) and isinstance(payload.get("QAAuthorContract"), dict):
        payload = payload["QAAuthorContract"]
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    matrix = normalized.get("matrix_coverage")
    if isinstance(matrix, dict):
        normalized["matrix_coverage"] = {
            str(key): _string_or_list_to_list(value) for key, value in matrix.items()
        }
    for key in ["tests_added", "paths_touched", "model_usage_ids"]:
        if key in normalized:
            normalized[key] = _string_or_list_to_list(normalized[key])
    return normalized


def _string_or_list_to_list(value: object) -> object:
    if isinstance(value, str):
        return [value]
    return value


def _relevant_changed_files(worktree: Path, project_metadata: dict[str, Any]) -> list[str]:
    return relevant_changed_files(changed_files(worktree), project_metadata)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _cost_fields(record: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
    model_pricing = _model_pricing(
        pricing,
        backend=str(record.get("backend") or ""),
        model=str(record.get("model") or ""),
    )
    if model_pricing is None:
        return {
            "api_equivalent_cost_usd": None,
            "cost_without_cache_usd": None,
            "cache_savings_usd": None,
        }
    input_tokens = int(record.get("actual_input_tokens") or 0)
    output_tokens = int(record.get("actual_output_tokens") or 0)
    cache_creation = int(record.get("cache_creation_input_tokens") or 0)
    cache_read = int(record.get("cache_read_input_tokens") or 0)
    reasoning = int(record.get("reasoning_output_tokens") or 0)
    uncached_input = max(0, input_tokens - cache_read - cache_creation)
    output_billable = max(output_tokens, reasoning)
    input_price = float(model_pricing.get("input_per_million", 0))
    cache_creation_price = float(model_pricing.get("cache_creation_per_million", input_price))
    cache_read_price = float(model_pricing.get("cache_read_per_million", input_price))
    output_price = float(model_pricing.get("output_per_million", 0))
    api_cost = (
        uncached_input * input_price
        + cache_creation * cache_creation_price
        + cache_read * cache_read_price
        + output_billable * output_price
    ) / 1_000_000
    cost_without_cache = (
        (uncached_input + cache_creation + cache_read) * input_price
        + output_billable * output_price
    ) / 1_000_000
    return {
        "api_equivalent_cost_usd": round(api_cost, 6),
        "cost_without_cache_usd": round(cost_without_cache, 6),
        "cache_savings_usd": round(max(0.0, cost_without_cache - api_cost), 6),
    }


def _model_pricing(
    pricing: dict[str, Any],
    *,
    backend: str,
    model: str,
) -> dict[str, Any] | None:
    models = pricing.get("models")
    if not isinstance(models, dict):
        return None
    keys = [f"{backend}:{model}", model, f"{backend}:default"]
    for key in keys:
        value = models.get(key)
        if isinstance(value, dict):
            return value
    return None


def _load_pricing(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    pricing_path = Path(path)
    if not pricing_path.exists():
        return {}
    payload = json.loads(pricing_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _path_matches(path: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path == normalized or path.startswith(f"{normalized}/")


def _run(argv: list[str], *, cwd: Path | None = None) -> None:
    result = run_bounded(argv, cwd=cwd, timeout_seconds=30)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
