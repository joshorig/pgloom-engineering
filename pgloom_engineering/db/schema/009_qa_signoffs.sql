create table if not exists engineering_qa_signoffs (
  id text primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  task_id text references tasks(id) on delete set null,
  plan_contract_id text references engineering_plan_contracts(id) on delete set null,
  milestone_id text,
  validator_type text not null check (validator_type in ('scrutiny', 'usertest')),
  verdict text not null check (verdict in ('pass', 'fail', 'inconclusive')),
  qa_result_contract jsonb not null,
  evidence jsonb not null default '[]'::jsonb,
  artifact_ids jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_engineering_qa_signoffs_validator
  on engineering_qa_signoffs(feature_id, milestone_id, validator_type)
  nulls not distinct;

create index if not exists idx_engineering_qa_signoffs_feature
  on engineering_qa_signoffs(feature_id, created_at);
