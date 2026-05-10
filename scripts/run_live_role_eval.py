from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pgloom_engineering.live_role_eval import dry_run_result, run_live_role_eval


def main() -> int:
    args = _parse_args()
    case = _read_json(Path(args.case)) if args.case else {"id": args.role, "role": args.role}
    role = str(case.get("role") or args.role)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        payload = dry_run_result(
            case,
            output_dir=output_dir,
            backend=args.backend,
            model=args.model,
            reasoning=args.reasoning,
        )
    else:
        payload = run_live_role_eval(
            case,
            role=role,
            output_dir=output_dir,
            database_url=args.database_url,
            backend=args.backend,
            model=args.model,
            reasoning=args.reasoning,
            max_steps=args.max_steps,
        ).asdict()
    (output_dir / "runner-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if args.dry_run or payload.get("status") == "pass" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--case")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--backend", choices=["codex", "claude"], default="codex")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
