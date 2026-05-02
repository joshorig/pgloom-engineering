create table if not exists engineering_projects (
  name text primary key,
  root text not null,
  github_repo text,
  base_branch text not null default 'main',
  smoke_command jsonb not null default '[]'::jsonb,
  regression_command jsonb not null default '[]'::jsonb,
  agent_topology jsonb not null default '{}'::jsonb,
  state text not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_engineering_projects_state
  on engineering_projects(state, name);
