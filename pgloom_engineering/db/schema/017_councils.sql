-- First-class council persistence for planner/reviewer/future role councils.

create table if not exists engineering_councils (
  id text primary key,
  feature_id text not null references engineering_features(id) on delete cascade,
  task_id text,
  role text not null,
  purpose text not null,
  status text not null,
  iteration_max int not null default 1,
  iterations_used int not null default 0,
  consolidated_artifact_id text,
  critic_verdict text,
  cost_usd_micros bigint not null default 0,
  total_tokens bigint not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

create table if not exists engineering_council_panelists (
  id bigserial primary key,
  council_id text not null references engineering_councils(id) on delete cascade,
  iteration int not null,
  panelist_kind text not null,
  panelist_ordinal int not null default 0,
  status text not null default 'running',
  model_provider text not null default 'unknown',
  model text not null default 'unknown',
  reasoning_level text,
  worker_run_id bigint references engineering_worker_runs(id) on delete set null,
  artifact_id text,
  verdict text,
  vote text,
  cost_usd_micros bigint not null default 0,
  input_tokens int,
  output_tokens int,
  reasoning_tokens int,
  model_usage_id bigint,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (council_id, iteration, panelist_kind, panelist_ordinal)
);

create index if not exists engineering_councils_feature_idx
  on engineering_councils (feature_id, started_at);

create index if not exists engineering_councils_task_idx
  on engineering_councils (task_id, started_at);

create index if not exists engineering_council_panelists_council_idx
  on engineering_council_panelists (council_id, iteration, panelist_kind, panelist_ordinal);

alter table engineering_worker_runs
  add column if not exists council_run_id text references engineering_councils(id) on delete set null;

drop trigger if exists cc_notify_councils on engineering_councils;
create trigger cc_notify_councils
after insert or update on engineering_councils
for each row execute function command_center_notify('council.update', 'feature_id', 'id');

create or replace function command_center_notify_council_panelist()
returns trigger
language plpgsql
as $$
declare
  feature text;
  changed text[];
  old_json jsonb;
  new_json jsonb;
  payload jsonb;
begin
  select feature_id into feature
  from engineering_councils
  where id = NEW.council_id;

  old_json := case when TG_OP = 'UPDATE' then to_jsonb(OLD) else null end;
  new_json := to_jsonb(NEW);
  changed := command_center_changed_fields(old_json, new_json);

  if TG_OP = 'UPDATE' and coalesce(array_length(changed, 1), 0) = 0 then
    return NEW;
  end if;

  payload := jsonb_build_object(
    'v', 1,
    'kind', 'council_panelist.update',
    'feature_id', feature,
    'row_id', NEW.id,
    'fields', changed,
    'ts', to_char(clock_timestamp() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  );

  perform pg_notify('cc_events', payload::text);
  return NEW;
end;
$$;

drop trigger if exists cc_notify_council_panelists on engineering_council_panelists;
create trigger cc_notify_council_panelists
after insert or update on engineering_council_panelists
for each row execute function command_center_notify_council_panelist();
