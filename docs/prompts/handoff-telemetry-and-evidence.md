# Implementor brief - handoff telemetry and evidence contracts

> **Status: planning brief.** This work creates the durable audit surface for
> autonomous engineering. Every worker invocation, validation phase, repair
> phase, blocked attempt, retry, and crash must leave enough timing, model,
> token, artifact, and handoff data for diagnosis and Command Center display.

## 1. Central Worker Run Table

Add `engineering_worker_runs` as the run-level join and summary table:

```sql
create table engineering_worker_runs (
  id bigserial primary key,
  feature_id text not null,
  task_id text,
  role text not null,
  phase text not null,
  validator_type text,
  attempt integer not null default 1,
  repair_count integer not null default 0,
  status text not null,
  blocker_code text,
  queued_at timestamptz,
  leased_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  queued_seconds double precision,
  leased_seconds double precision,
  running_seconds double precision,
  model_seconds double precision,
  verification_seconds double precision,
  blocked_seconds double precision,
  model_provider text,
  model text,
  reasoning_level text,
  model_profile text,
  route_tier text,
  input_tokens bigint default 0,
  output_tokens bigint default 0,
  reasoning_tokens bigint default 0,
  cached_input_tokens bigint default 0,
  cache_creation_tokens bigint default 0,
  cost_usd numeric(12, 6) default 0,
  token_savior_original_tokens bigint default 0,
  token_savior_packed_tokens bigint default 0,
  token_savior_saved_tokens bigint default 0,
  token_savior_reduction_ratio double precision,
  rtk_raw_log_tokens bigint default 0,
  rtk_filtered_log_tokens bigint default 0,
  rtk_saved_tokens bigint default 0,
  cumulative_cost_usd numeric(12, 6) default 0,
  cumulative_wall_clock_seconds double precision default 0,
  cumulative_input_tokens bigint default 0,
  cumulative_output_tokens bigint default 0,
  cumulative_tokens_saved bigint default 0,
  cumulative_model_calls integer default 0,
  commands_run jsonb not null default '[]',
  evidence_ids jsonb not null default '[]',
  artifact_ids jsonb not null default '[]',
  model_usage_ids jsonb not null default '[]',
  token_savior_usage_ids jsonb not null default '[]',
  handoff_id text,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
```

Existing `model_usage`, `engineering_token_savior_usage`, and artifacts remain
source tables. `engineering_worker_runs` links them and provides the dashboard
summary surface.

## 2. Mandatory Telemetry

Every worker run and repair phase must record:

- wall-clock duration: queued, leased, running, model time, verification time,
  blocked/waiting time where measurable
- model cost: per call, per phase, per worker, cumulative feature cost
- token usage: input, output, reasoning, cached input, cache creation, provider
  metadata
- token economy: Token Savior original tokens, packed tokens, saved tokens,
  reduction ratio, estimated cost saved
- RTK/subprocess economy: raw log token count, filtered token count, saved
  tokens, raw artifact ids
- model routing: provider, model, reasoning level, profile, phase, fallback or
  escalation tier
- outcome: status, blocker, repair count, commands run, evidence ids, handoff id

Failed, blocked, retried, and crashed runs must be recorded. Missing telemetry
on failure is itself a production bug.

## 3. Handoff Cumulative Fields

Add cumulative telemetry fields to every handoff envelope:

```python
cumulative_cost_usd: Decimal
cumulative_wall_clock_seconds: float
cumulative_input_tokens: int
cumulative_output_tokens: int
cumulative_tokens_saved: int
cumulative_model_calls: int
```

The values should be computed from `engineering_worker_runs` for the feature or
milestone boundary and copied into the handoff summary for compact downstream
context.

## 4. Commands And Evidence

Use structured command records:

```python
class CommandRun(BaseModel):
    cmd: list[str]
    exit_code: int | None
    duration_s: float
    started_at: datetime
    finished_at: datetime | None
    artifact_ids: list[str] = []
```

Use typed validation evidence:

```python
class ValidationEvidence(BaseModel):
    evidence_id: str
    kind: Literal[
        "test_run",
        "code_review",
        "ui_exercise",
        "integration_check",
        "lint_type_check",
        "benchmark",
        "screenshot",
        "network_trace",
        "command_log",
    ]
    summary: str
    verdict: Literal["pass", "fail", "inconclusive"]
    command_run_ids: list[str] = []
    artifact_ids: list[str] = []
    metadata: dict[str, Any] = {}
```

Artifact ids should point to raw logs, filtered logs, prompts, responses, diffs,
screenshots, traces, coverage reports, and benchmark output.

## 5. Required Procedures

Add `required_procedures` to task contracts and
`procedures_attestation` to worker results. Workers must self-attest whether
they followed each required procedure and include a short explanation for any
miss or deviation. Validators should treat false, missing, or ambiguous
attestation as a finding.

## 6. Tests For Later Implementation

- Worker-run CRUD and indexes by feature, task, role, phase, model, and status.
- Timing aggregation and cumulative handoff fields.
- Links to `model_usage`, Token Savior usage, artifacts, handoffs, and task
  events.
- Failure paths where blocked/crashed workers still record status, blocker,
  artifact ids, partial cost/tokens if any, and recovery handoff.
- RTK/log-filter savings aggregation.
