create table if not exists engineering_token_savior_usage (
  id bigserial primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  workflow_id text references workflows(id) on delete set null,
  task_id text references tasks(id) on delete set null,
  model_usage_id bigint references model_usage(id) on delete set null,
  profile_name text,
  input_tokens_original integer not null check (input_tokens_original >= 0),
  input_tokens_after_savior integer not null check (input_tokens_after_savior >= 0),
  tokens_saved integer not null check (tokens_saved >= 0),
  reduction_ratio numeric(8,6) not null check (reduction_ratio >= 0 and reduction_ratio <= 1),
  estimated_cost_saved_usd numeric(12,6) not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_engineering_token_savior_feature
  on engineering_token_savior_usage(feature_id);

create index if not exists idx_engineering_token_savior_task
  on engineering_token_savior_usage(task_id);
