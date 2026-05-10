update engineering_task_contracts
set
  milestone_id = coalesce(
    milestone_id,
    input_contract->>'milestone_id',
    input_contract->'inputs'->>'milestone_id'
  ),
  task_slice_id = coalesce(
    task_slice_id,
    input_contract->>'task_slice_id',
    input_contract->'inputs'->>'task_slice_id'
  )
where milestone_id is null
   or task_slice_id is null;

update engineering_handoffs
set
  title = coalesce(
    title,
    nullif(left(regexp_replace(coalesce(
      contract->>'title',
      contract->>'label',
      contract->>'task_slice_id',
      contract->'inputs'->>'task_slice_id',
      replace(handoff_type, '_', ' ')
    ), '\s+', ' ', 'g'), 80), ''),
    'handoff'
  ),
  summary = coalesce(
    summary,
    nullif(left(regexp_replace(coalesce(
      contract->>'summary',
      contract->>'objective',
      contract->'inputs'->>'objective',
      contract->'handoff_envelope'->>'summary'
    ), '\s+', ' ', 'g'), 240), '')
  )
where title is null
   or summary is null;

with usage_by_task as (
  select
    task_id,
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
    coalesce(sum(
      case
        when coalesce(cost_usd, 0) <> 0 then cost_usd
        when metadata->>'provider' = 'codex' then (
          greatest(
            0,
            input_tokens
              - coalesce((metadata->>'cached_input_tokens')::bigint, 0)
              - coalesce((metadata->>'cache_read_input_tokens')::bigint, 0)
          ) * 5.0
          + (
            coalesce((metadata->>'cached_input_tokens')::bigint, 0)
            + coalesce((metadata->>'cache_read_input_tokens')::bigint, 0)
          ) * 0.50
          + output_tokens * 30.0
          + (
            coalesce((metadata->>'reasoning_tokens')::bigint, 0)
            + coalesce((metadata->>'reasoning_output_tokens')::bigint, 0)
          ) * 30.0
        ) / 1000000.0
        else cost_usd
      end
    ), 0) as cost_usd,
    string_agg(distinct nullif(metadata->>'provider', ''), ',' order by nullif(metadata->>'provider', ''))
      as model_provider,
    string_agg(distinct nullif(metadata->>'model', ''), ',' order by nullif(metadata->>'model', ''))
      as model,
    string_agg(distinct nullif(metadata->>'reasoning_level', ''), ',' order by nullif(metadata->>'reasoning_level', ''))
      as reasoning_level,
    string_agg(distinct nullif(profile_name, ''), ',' order by nullif(profile_name, ''))
      as model_profile,
    jsonb_agg(id order by created_at, id) as model_usage_ids
  from model_usage
  where task_id is not null
  group by task_id
)
update engineering_worker_runs ewr
set
  input_tokens = case when ewr.input_tokens = 0 then ubt.input_tokens else ewr.input_tokens end,
  output_tokens = case when ewr.output_tokens = 0 then ubt.output_tokens else ewr.output_tokens end,
  reasoning_tokens = case when ewr.reasoning_tokens = 0 then ubt.reasoning_tokens else ewr.reasoning_tokens end,
  cached_input_tokens = case
    when ewr.cached_input_tokens = 0 then ubt.cached_input_tokens
    else ewr.cached_input_tokens
  end,
  cache_creation_tokens = case
    when ewr.cache_creation_tokens = 0 then ubt.cache_creation_tokens
    else ewr.cache_creation_tokens
  end,
  cost_usd = case when ewr.cost_usd = 0 then ubt.cost_usd else ewr.cost_usd end,
  model_seconds = coalesce(ewr.model_seconds, ubt.model_seconds),
  model_provider = coalesce(ewr.model_provider, ubt.model_provider),
  model = coalesce(ewr.model, ubt.model),
  reasoning_level = coalesce(ewr.reasoning_level, ubt.reasoning_level),
  model_profile = coalesce(ewr.model_profile, ubt.model_profile),
  model_usage_ids = case
    when ewr.model_usage_ids = '[]'::jsonb then ubt.model_usage_ids
    else ewr.model_usage_ids
  end
from usage_by_task ubt
where ewr.task_id = ubt.task_id;
