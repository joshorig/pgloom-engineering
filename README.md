# pgloom-engineering

`pgloom-engineering` is the engineering-focused orchestrator built on top of
`pgloom`. It hosts planner, implementer, reviewer, QA, historian, GitHub,
worktree, Telegram, dashboard, and report integrations while keeping the core
runtime in `pgloom`.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pgloom-engineering --help
pgloom-engineering pgloom --help
```

Run core database migrations through the wrapped pgloom CLI:

```bash
export PGLOOM_DATABASE_URL=postgresql://localhost/pgloom_engineering_dev
pgloom-engineering pgloom db migrate
pgloom-engineering db migrate
```

## Status

Current focus is the autonomous engineering workflow:

- Planner produces typed multi-agent `PlanContract`s with token-economy context.
- QA author can create failing tests in isolated worktrees, validate required
  project gates, run deterministic semantic quality checks, and emit
  `QAAuthorContract` output.
- Implementer, reviewer, QA scrutiny, and QA user-test now have a repeatable
  live-role eval harness that seeds fixture tasks and drives the same
  `worker.run_once` path as production dispatch.
- Project metadata in `docs/evals/project-registry.yaml` describes QA commands,
  required gates, test roots, endpoint conventions, benchmark conventions, and
  semantic rules used by prompts and deterministic gates.
- Generated eval reports under `docs/reports/*/` are local artifacts; source eval
  fixtures live under `docs/evals/`.

Run the non-planner live-role suite with a live model backend:

```bash
pgloom-engineering pgloom db migrate
pgloom-engineering db migrate
uv run python scripts/run_live_role_eval_suite.py \
  --suite docs/evals/live-role-suite.json \
  --database-url "$PGLOOM_DATABASE_URL" \
  --backend codex \
  --model gpt-5.4
```

Use `--role implementer`, `--role reviewer`, `--role qa-scrutiny`,
`--role qa-usertest`, or `--role orchestration` to iterate on one role at a
time. The suite records worker telemetry, model usage, Token Savior role-context
usage, RTK/log-filter savings, handoffs, and validation evidence through the
same tables used by live dispatch.

## Intended Architecture

Full diagram: [docs/diagrams/intended-architecture.html](docs/diagrams/intended-architecture.html)

```mermaid
flowchart LR
    Human[Human<br/>goals + final PR] --> CLI[CLI / API<br/>project register<br/>feature create/run]
    CLI --> Driver[Workflow Driver<br/>slot scheduling<br/>retry + replan]
    Driver --> Runtime[Worker Runtime<br/>claim, gate, dispatch]
    Runtime --> DB[(pgloom + engineering DB<br/>workflows, tasks, features<br/>contracts, handoffs, recovery)]

    DB --> Planner[Planner<br/>multi-agent council<br/>PlanContract + milestones]
    DB --> QA[QA Author<br/>live worktree tests<br/>red proof + semantic review]
    DB --> Implementer[Implementer<br/>uses QA worktree<br/>verify + repair]
    DB --> Reviewer[Reviewer<br/>adversarial review]
    DB --> Validators[Scrutiny + User-test Validators<br/>fresh context<br/>milestone gates]

    Planner -. context .-> TokenSavior[(Token Savior<br/>context packing<br/>capsule cache)]
    QA -. context .-> Memory[(Memory<br/>observations<br/>session digest)]
    Implementer --> Worktrees[(Project Worktrees<br/>isolated attempts)]
    Validators --> CommandCenter[Command Center<br/>telemetry, evidence<br/>operator interventions]
    CommandCenter --> Final[Human Gate<br/>final PR review / merge]

    Registry[Project Registry<br/>metadata, gates, paths] --> Planner
    Registry --> QA
    Registry --> Implementer
```

`pgloom-engineering` keeps orchestration policy, role handlers, contracts, and
project-specific metadata in this repository while relying on `pgloom` for the
core task runtime. The intended production boundary is autonomous until final PR
merge: humans define the project and feature goal up front, then agents plan,
author QA, implement, review, validate, repair, and produce finalization
evidence. Human oversight happens through Command Center visibility and audited
interventions, not per-step approvals.

## Autonomous Workflow

Full diagram: [docs/diagrams/autonomous-workflow.html](docs/diagrams/autonomous-workflow.html)

```mermaid
flowchart TD
    Register[Register project<br/>metadata + gates] --> Goal[Create feature goal<br/>requirements + acceptance criteria]
    Goal --> Plan[Planner council<br/>PlanContract + milestones<br/>validation contract]
    Plan --> Design[Designer<br/>boundaries + constraints]
    Design --> QAAuthor[QA Author<br/>author tests, compile, prove red]
    QAAuthor --> QAWorktree[(QA Worktree<br/>failing tests + QAAuthorContract)]
    QAWorktree --> Implement[Implementer<br/>make tests green]
    Implement --> Review[Reviewer<br/>contract verdict]
    Review --> Scrutiny[QA Scrutiny<br/>lint, type, tests<br/>code-review fan-out]
    Scrutiny --> UserTest[QA User-test<br/>spawn app/service<br/>exercise real flows]
    UserTest --> Evidence[(Finalization Evidence<br/>checks, artifacts, usage, recovery)]
    Evidence --> PR[Final PR<br/>human review / merge]

    QAAuthor -. repair same worktree .-> QAAuthor
    Implement -. verification repair .-> Implement
    Review -. coder repair .-> Implement
    Scrutiny -. corrective slice .-> Plan
    UserTest -. corrective slice .-> Plan
    Evidence -. exhausted retry budget .-> Plan
```

The workflow driver advances ready role slots, records deterministic blocker and
recovery decisions, and replans with concrete failure knowledge when retries are
exhausted or token cost makes another blind retry wasteful.

Every worker run should record wall-clock timing, model cost, token usage, Token
Savior savings, RTK/log-filter savings, model route, blocker state, artifact
links, and handoff ids. Handoffs stay compact; raw prompts, responses, logs,
diffs, screenshots, network traces, and benchmark reports belong in artifacts.

Run the local validation suite with:

```bash
uv run pytest -q
```
