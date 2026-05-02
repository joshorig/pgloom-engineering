create table if not exists engineering_features (
  id text primary key references workflows(id) on delete cascade,
  project text not null,
  branch text,
  pr_url text,
  state text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists engineering_feature_children (
  feature_id text not null references engineering_features(id) on delete cascade,
  task_id text not null references tasks(id) on delete cascade,
  role text not null,
  created_at timestamptz not null default now(),
  primary key(feature_id, task_id)
);
