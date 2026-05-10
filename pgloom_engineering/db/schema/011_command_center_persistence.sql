alter table engineering_task_contracts
  add column if not exists milestone_id text,
  add column if not exists task_slice_id text;

create index if not exists idx_engineering_task_contracts_milestone
  on engineering_task_contracts(feature_id, milestone_id, created_at);

alter table engineering_handoffs
  add column if not exists title text,
  add column if not exists summary text;

