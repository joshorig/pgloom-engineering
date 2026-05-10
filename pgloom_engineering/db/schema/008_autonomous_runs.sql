create table if not exists engineering_worker_runs (
  id bigserial primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  task_id text references tasks(id) on delete set null,
  role text not null,
  phase text not null,
  validator_type text,
  attempt integer not null default 1 check (attempt >= 1),
  repair_count integer not null default 0 check (repair_count >= 0),
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
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  reasoning_tokens bigint not null default 0,
  cached_input_tokens bigint not null default 0,
  cache_creation_tokens bigint not null default 0,
  cost_usd numeric(12, 6) not null default 0,
  token_savior_original_tokens bigint not null default 0,
  token_savior_packed_tokens bigint not null default 0,
  token_savior_saved_tokens bigint not null default 0,
  token_savior_reduction_ratio double precision,
  rtk_raw_log_tokens bigint not null default 0,
  rtk_filtered_log_tokens bigint not null default 0,
  rtk_saved_tokens bigint not null default 0,
  cumulative_cost_usd numeric(12, 6) not null default 0,
  cumulative_wall_clock_seconds double precision not null default 0,
  cumulative_input_tokens bigint not null default 0,
  cumulative_output_tokens bigint not null default 0,
  cumulative_tokens_saved bigint not null default 0,
  cumulative_model_calls integer not null default 0,
  commands_run jsonb not null default '[]'::jsonb,
  evidence_ids jsonb not null default '[]'::jsonb,
  artifact_ids jsonb not null default '[]'::jsonb,
  model_usage_ids jsonb not null default '[]'::jsonb,
  token_savior_usage_ids jsonb not null default '[]'::jsonb,
  handoff_id text references engineering_handoffs(id) on delete set null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_engineering_worker_runs_feature
  on engineering_worker_runs(feature_id, created_at);

create index if not exists idx_engineering_worker_runs_task
  on engineering_worker_runs(task_id, created_at);

create index if not exists idx_engineering_worker_runs_role_phase
  on engineering_worker_runs(feature_id, role, phase, status);

create table if not exists engineering_operator_interventions (
  id bigserial primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  actor text not null,
  action_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_engineering_operator_interventions_feature
  on engineering_operator_interventions(feature_id, created_at);
