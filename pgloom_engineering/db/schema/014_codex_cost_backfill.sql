update model_usage
set cost_usd = (
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
where metadata->>'provider' = 'codex'
  and coalesce(cost_usd, 0) = 0
  and (coalesce(input_tokens, 0) > 0 or coalesce(output_tokens, 0) > 0);

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
  cost_usd = case
    when ewr.cost_usd = 0 or abs(ewr.cost_usd - ubt.cost_usd) > 0.000001 then ubt.cost_usd
    else ewr.cost_usd
  end,
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
where ewr.task_id = ubt.task_id
  and ubt.cost_usd > 0;
