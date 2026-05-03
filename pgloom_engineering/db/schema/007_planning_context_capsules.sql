create table if not exists engineering_project_context_capsules (
  id bigserial primary key,
  project text not null,
  project_root text not null,
  git_head text not null,
  query_hash text not null,
  capsule_version text not null,
  context jsonb not null,
  packed_context text not null,
  input_tokens_original integer not null check (input_tokens_original >= 0),
  input_tokens_after_savior integer not null check (input_tokens_after_savior >= 0),
  tokens_saved integer not null check (tokens_saved >= 0),
  reduction_ratio numeric(8,6) not null check (reduction_ratio >= 0 and reduction_ratio <= 1),
  method text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  last_used_at timestamptz not null default now(),
  unique(project, git_head, query_hash, capsule_version)
);

create index if not exists idx_engineering_context_capsules_project
  on engineering_project_context_capsules(project, last_used_at desc);
