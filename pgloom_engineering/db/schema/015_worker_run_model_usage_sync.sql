create or replace function engineering_worker_run_model_usage_sync()
returns trigger
language plpgsql
as $$
declare
  usage_row record;
begin
  if new.task_id is null then
    return new;
  end if;
  if new.status not in ('done', 'blocked', 'cancelled', 'failed', 'crashed', 'retry') then
    return new;
  end if;

  select
    coalesce(sum(input_tokens), 0) as input_tokens,
    coalesce(sum(output_tokens), 0) as output_tokens,
    coalesce(sum(
      coalesce((metadata->>'reasoning_tokens')::bigint, 0)
      + coalesce((metadata->>'reasoning_output_tokens')::bigint, 0)
    ), 0) as reasoning_tokens,
    coalesce(sum(
      coalesce((metadata->>'cached_input_tokens')::bigint, 0)
      + coalesce((metadata->>'cache_read_input_tokens')::bigint, 0)
    ), 0) as cached_input_tokens,
    coalesce(sum(coalesce((metadata->>'cache_creation_input_tokens')::bigint, 0)), 0)
      as cache_creation_tokens,
    coalesce(sum(coalesce((metadata->>'duration_seconds')::double precision, 0)), 0)
      as model_seconds,
    coalesce(sum(cost_usd), 0) as cost_usd,
    string_agg(distinct nullif(metadata->>'provider', ''), ',' order by nullif(metadata->>'provider', ''))
      as model_provider,
    string_agg(distinct nullif(metadata->>'model', ''), ',' order by nullif(metadata->>'model', ''))
      as model,
    string_agg(distinct nullif(metadata->>'reasoning_level', ''), ',' order by nullif(metadata->>'reasoning_level', ''))
      as reasoning_level,
    string_agg(distinct nullif(profile_name, ''), ',' order by nullif(profile_name, ''))
      as model_profile,
    jsonb_agg(id order by created_at, id) as model_usage_ids
  into usage_row
  from model_usage
  where task_id = new.task_id;

  if usage_row.model_usage_ids is null then
    return new;
  end if;

  if new.input_tokens = 0 then
    new.input_tokens = usage_row.input_tokens;
  end if;
  if new.output_tokens = 0 then
    new.output_tokens = usage_row.output_tokens;
  end if;
  if new.reasoning_tokens = 0 then
    new.reasoning_tokens = usage_row.reasoning_tokens;
  end if;
  if new.cached_input_tokens = 0 then
    new.cached_input_tokens = usage_row.cached_input_tokens;
  end if;
  if new.cache_creation_tokens = 0 then
    new.cache_creation_tokens = usage_row.cache_creation_tokens;
  end if;
  if new.cost_usd = 0 then
    new.cost_usd = usage_row.cost_usd;
  end if;
  new.model_seconds = coalesce(new.model_seconds, usage_row.model_seconds);
  new.model_provider = coalesce(new.model_provider, usage_row.model_provider);
  new.model = coalesce(new.model, usage_row.model);
  new.reasoning_level = coalesce(new.reasoning_level, usage_row.reasoning_level);
  new.model_profile = coalesce(new.model_profile, usage_row.model_profile);
  if new.model_usage_ids = '[]'::jsonb then
    new.model_usage_ids = usage_row.model_usage_ids;
  end if;

  return new;
end;
$$;

drop trigger if exists engineering_worker_run_model_usage_sync_trigger
  on engineering_worker_runs;

create trigger engineering_worker_run_model_usage_sync_trigger
before insert or update on engineering_worker_runs
for each row
execute function engineering_worker_run_model_usage_sync();

update engineering_worker_runs
set status = status
where task_id is not null
  and status in ('done', 'blocked', 'cancelled', 'failed', 'crashed', 'retry')
  and (
    input_tokens = 0
    or output_tokens = 0
    or cost_usd = 0
    or model_usage_ids = '[]'::jsonb
  );
