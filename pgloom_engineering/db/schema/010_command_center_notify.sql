create or replace view engineering_feature_intervention_state as
select distinct on (feature_id)
  feature_id,
  action_type = 'pause_feature' as paused,
  id as last_intervention_id,
  actor as last_actor,
  created_at as last_intervention_at
from engineering_operator_interventions
where action_type in ('pause_feature', 'resume_feature')
order by feature_id, created_at desc, id desc;

create or replace function command_center_changed_fields(old_row jsonb, new_row jsonb)
returns text[]
language sql
stable
as $$
  select coalesce(array_agg(key order by key), array[]::text[])
  from (
    select n.key
    from jsonb_each(new_row) as n(key, value)
    left join jsonb_each(coalesce(old_row, '{}'::jsonb)) as o(key, value) using (key)
    where n.key <> 'updated_at'
      and (old_row is null or n.value is distinct from o.value)
      and octet_length(n.value::text) <= 1024
  ) changed;
$$;

create or replace function command_center_notify()
returns trigger
language plpgsql
as $$
declare
  event_kind text := TG_ARGV[0];
  feature_col text := TG_ARGV[1];
  row_col text := TG_ARGV[2];
  old_json jsonb;
  new_json jsonb;
  changed text[];
  payload jsonb;
begin
  old_json := case when TG_OP = 'UPDATE' then to_jsonb(OLD) else null end;
  new_json := to_jsonb(NEW);
  changed := command_center_changed_fields(old_json, new_json);

  if TG_OP = 'UPDATE' and coalesce(array_length(changed, 1), 0) = 0 then
    return NEW;
  end if;

  payload := jsonb_build_object(
    'v', 1,
    'kind', event_kind,
    'feature_id', new_json ->> feature_col,
    'row_id', new_json -> row_col,
    'fields', changed,
    'ts', to_char(clock_timestamp() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  );

  if octet_length(payload::text) > 7500 then
    payload := jsonb_build_object(
      'v', 1,
      'kind', 'resync',
      'feature_id', new_json ->> feature_col,
      'row_id', new_json -> row_col,
      'reason', 'notify payload too large',
      'ts', to_char(clock_timestamp() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
    );
  end if;

  perform pg_notify('cc_events', payload::text);
  return NEW;
end;
$$;

drop trigger if exists cc_notify_features on engineering_features;
create trigger cc_notify_features
after insert or update on engineering_features
for each row execute function command_center_notify('feature.update', 'id', 'id');

drop trigger if exists cc_notify_worker_runs on engineering_worker_runs;
create trigger cc_notify_worker_runs
after insert or update on engineering_worker_runs
for each row execute function command_center_notify('worker_run.update', 'feature_id', 'id');

drop trigger if exists cc_notify_handoffs on engineering_handoffs;
create trigger cc_notify_handoffs
after insert or update on engineering_handoffs
for each row execute function command_center_notify('handoff.update', 'feature_id', 'id');

drop trigger if exists cc_notify_qa_signoffs on engineering_qa_signoffs;
create trigger cc_notify_qa_signoffs
after insert or update on engineering_qa_signoffs
for each row execute function command_center_notify('qa.signoff', 'feature_id', 'id');

drop trigger if exists cc_notify_interventions on engineering_operator_interventions;
create trigger cc_notify_interventions
after insert or update on engineering_operator_interventions
for each row execute function command_center_notify('intervention.added', 'feature_id', 'id');

drop trigger if exists cc_notify_recovery on engineering_recovery_actions;
create trigger cc_notify_recovery
after insert or update on engineering_recovery_actions
for each row execute function command_center_notify('recovery.update', 'feature_id', 'id');

drop trigger if exists cc_notify_plan_contracts on engineering_plan_contracts;
create trigger cc_notify_plan_contracts
after insert or update on engineering_plan_contracts
for each row execute function command_center_notify('plan.update', 'feature_id', 'id');

drop trigger if exists cc_notify_task_contracts on engineering_task_contracts;
create trigger cc_notify_task_contracts
after insert or update on engineering_task_contracts
for each row execute function command_center_notify('task.update', 'feature_id', 'task_id');
