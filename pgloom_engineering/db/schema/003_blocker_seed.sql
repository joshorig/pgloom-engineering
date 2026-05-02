insert into blocker_codes(code, name, severity, retryable, category, metadata)
values
  ('engineering.project_unhealthy', 'Project environment unhealthy', 2, true, 'environment', '{}'::jsonb),
  ('engineering.review_rejected', 'Engineering review rejected', 3, true, 'review', '{}'::jsonb),
  ('engineering.qa_failed', 'Engineering QA failed', 3, true, 'qa', '{}'::jsonb)
on conflict (code) do update set
  name = excluded.name,
  severity = excluded.severity,
  retryable = excluded.retryable,
  category = excluded.category,
  metadata = excluded.metadata;
