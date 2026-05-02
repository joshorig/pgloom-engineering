create table if not exists engineering_plan_contracts (
  id text primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  planner_task_id text references tasks(id) on delete set null,
  version text not null,
  status text not null,
  active boolean not null default false,
  contract_hash text not null,
  contract jsonb not null,
  validation_errors jsonb not null default '[]'::jsonb,
  council_reports jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_engineering_plan_contracts_one_active
  on engineering_plan_contracts(feature_id)
  where active is true;

create index if not exists idx_engineering_plan_contracts_feature
  on engineering_plan_contracts(feature_id, created_at desc);

create table if not exists engineering_task_contracts (
  task_id text primary key references tasks(id) on delete cascade,
  feature_id text not null references engineering_features(id) on delete cascade,
  plan_contract_id text not null references engineering_plan_contracts(id) on delete cascade,
  role text not null,
  contract_version text not null,
  input_contract jsonb not null,
  input_contract_hash text not null,
  output_contract jsonb not null default '{}'::jsonb,
  status text not null default 'active',
  validation_errors jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_engineering_task_contracts_feature
  on engineering_task_contracts(feature_id, created_at);

create table if not exists engineering_handoffs (
  id text primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  from_task_id text references tasks(id) on delete set null,
  to_task_id text references tasks(id) on delete set null,
  handoff_type text not null,
  contract jsonb not null,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_engineering_handoffs_feature
  on engineering_handoffs(feature_id, created_at);

create table if not exists engineering_recovery_actions (
  id text primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  task_id text references tasks(id) on delete set null,
  blocker_code text not null,
  action text not null,
  status text not null,
  attempt integer not null default 1 check (attempt >= 1),
  max_attempts integer not null default 3 check (max_attempts >= 1),
  decision_contract jsonb not null,
  outcome text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_engineering_recovery_actions_feature
  on engineering_recovery_actions(feature_id, created_at desc);
