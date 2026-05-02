create table if not exists engineering_self_repair_issues (
  id text primary key,
  task_id text references tasks(id) on delete set null,
  code text not null,
  state text not null,
  summary text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists engineering_self_repair_deliberations (
  id text primary key,
  issue_id text not null references engineering_self_repair_issues(id) on delete cascade,
  panel text not null,
  verdict text not null,
  rationale text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
