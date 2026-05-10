alter table engineering_features
  add column if not exists abort_reason text,
  add column if not exists abort_detail text,
  add column if not exists aborted_at timestamptz;

alter table tasks
  add column if not exists terminal_reason text,
  add column if not exists terminal_detail text;

alter table engineering_worker_runs
  add column if not exists terminal_reason text,
  add column if not exists terminal_detail text;

create index if not exists engineering_features_aborted_idx
  on engineering_features (state) where state = 'aborted';

