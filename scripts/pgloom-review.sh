#!/usr/bin/env bash
# pgloom-review — list workflows or run a full per-feature telemetry review.
#
# Usage:
#   ./scripts/pgloom-review.sh list [LIMIT]
#   ./scripts/pgloom-review.sh review FEATURE_ID
#   ./scripts/pgloom-review.sh help
#
# Environment:
#   PGLOOM_DATABASE_URL   defaults to postgresql://localhost:5432/pgloom_engineering_dev
#
# FEATURE_ID is the workflows(id) primary key (also engineering_features.id),
# typically formatted "wf_<32hex>".
#
# Examples:
#   ./scripts/pgloom-review.sh list
#   ./scripts/pgloom-review.sh list 50
#   ./scripts/pgloom-review.sh review wf_03418122e944475492056b7264ce0772

set -euo pipefail

DB_URL="${PGLOOM_DATABASE_URL:-postgresql://localhost:5432/pgloom_engineering_dev}"

usage() {
  cat <<'EOF'
pgloom-review — pgloom-engineering per-feature telemetry tool

USAGE:
  pgloom-review.sh list [LIMIT]       List features (default LIMIT=20)
  pgloom-review.sh review FEATURE_ID  Full per-feature review
  pgloom-review.sh help               This message

FEATURE_ID is engineering_features.id — same as the workflows(id) value
that appears in worktree directory names (the "wf_<hash>" segment).

REVIEW OUTPUT:
  0. Feature row (project, branch, pr_url, state)
  1. Per-call worker runs (every model invocation)
  2. Aggregate by role/phase
  3. Per-profile model usage (pgloom.model_usage)
  4. Token Savior breakdown
  5. Plan contract(s)
  6. Task contracts (slice metadata)
  7. Handoff chain
  8. Recovery actions
  9. QA sign-offs (scrutiny / usertest)
 10. Operator interventions
 11. Self-repair issues (if any)
EOF
}

cmd_list() {
  local limit="${1:-20}"
  psql "$DB_URL" -P pager=off -v limit="$limit" <<'SQL'
\echo '=== Recent engineering features ==='
SELECT
  ef.id                                          AS feature_id,
  ef.project,
  COALESCE(ef.branch, '-')                        AS branch,
  ef.state,
  COALESCE(ef.pr_url, '-')                        AS pr_url,
  to_char(ef.created_at, 'YYYY-MM-DD HH24:MI')   AS created_at,
  (SELECT COUNT(*) FROM engineering_worker_runs ewr
     WHERE ewr.feature_id = ef.id)                AS runs,
  (SELECT ROUND(SUM(cost_usd)::numeric, 2)
     FROM engineering_worker_runs ewr
     WHERE ewr.feature_id = ef.id)                AS cost_usd,
  (SELECT string_agg(DISTINCT role, ',' ORDER BY role)
     FROM engineering_worker_runs ewr
     WHERE ewr.feature_id = ef.id)                AS roles_seen,
  (SELECT blocker_code FROM engineering_worker_runs ewr
     WHERE ewr.feature_id = ef.id AND blocker_code IS NOT NULL
     ORDER BY started_at DESC LIMIT 1)            AS last_blocker
FROM engineering_features ef
ORDER BY ef.created_at DESC
LIMIT :limit;
SQL
}

cmd_review() {
  local wf="$1"
  if [[ -z "$wf" ]]; then
    echo "ERROR: review requires a FEATURE_ID" >&2
    usage >&2
    exit 2
  fi
  psql "$DB_URL" -P pager=off -v wf="$wf" <<'SQL'
\echo '\n=== 0. FEATURE ROW ==='
SELECT id, project, branch, pr_url, state, abort_reason, abort_detail,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
       to_char(aborted_at, 'YYYY-MM-DD HH24:MI:SS') AS aborted_at,
       to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
FROM engineering_features
WHERE id = :'wf';

\echo '\n=== 0b. TASK TERMINAL REASONS ==='
SELECT id, task_type, state, blocker_code, terminal_reason, terminal_detail
FROM tasks
WHERE workflow_id = :'wf'
  AND (terminal_reason IS NOT NULL OR terminal_detail IS NOT NULL)
ORDER BY created_at;

\echo '\n=== 1. PER-CALL WORKER RUNS ==='
SELECT task_id, role, phase, validator_type, status, attempt, repair_count,
       ROUND(running_seconds::numeric, 1)      AS secs,
       ROUND(model_seconds::numeric, 1)        AS model_s,
       ROUND(verification_seconds::numeric, 1) AS verify_s,
       model_provider, model, reasoning_level,
       input_tokens, output_tokens, reasoning_tokens,
       cached_input_tokens, cache_creation_tokens,
       ROUND(cost_usd::numeric, 4)             AS cost_usd,
       ROUND(cumulative_cost_usd::numeric, 4)  AS cum_usd,
       token_savior_saved_tokens               AS ts_saved,
       ROUND(token_savior_reduction_ratio::numeric, 3) AS ts_ratio,
       rtk_saved_tokens,
       blocker_code, terminal_reason, terminal_detail
FROM engineering_worker_runs
WHERE feature_id = :'wf'
ORDER BY started_at;

\echo '\n=== 2. AGGREGATE BY ROLE/PHASE ==='
SELECT role, phase, COUNT(*) AS runs,
       SUM(input_tokens)               AS in_tokens,
       SUM(cached_input_tokens)        AS cached_in,
       SUM(cache_creation_tokens)      AS cache_create,
       SUM(output_tokens)              AS out_tokens,
       SUM(reasoning_tokens)           AS reasoning,
       ROUND(SUM(running_seconds)::numeric, 1) AS total_secs,
       ROUND(SUM(cost_usd)::numeric, 4)        AS cost_usd,
       SUM(token_savior_saved_tokens)  AS ts_saved,
       SUM(rtk_saved_tokens)           AS rtk_saved
FROM engineering_worker_runs
WHERE feature_id = :'wf'
GROUP BY role, phase
ORDER BY MIN(started_at);

\echo '\n=== 3. PER-PROFILE MODEL USAGE (pgloom.model_usage) ==='
SELECT profile_name, COUNT(*) AS calls,
       SUM(input_tokens)                AS in_tokens,
       SUM(output_tokens)               AS out_tokens,
       SUM(COALESCE((metadata->>'cached_input_tokens')::integer, 0)) AS cached_in,
       SUM(COALESCE((metadata->>'cache_creation_input_tokens')::integer, 0)) AS cache_create,
       SUM(COALESCE((metadata->>'cache_read_input_tokens')::integer, 0)) AS cache_read,
       SUM(COALESCE((metadata->>'reasoning_tokens')::integer, 0)) AS reasoning,
       ROUND(SUM(cost_usd)::numeric, 4) AS cost_usd,
       string_agg(DISTINCT COALESCE(metadata->>'provider', '-'), ',' ORDER BY COALESCE(metadata->>'provider', '-')) AS providers,
       string_agg(DISTINCT COALESCE(metadata->>'model', '-'), ',' ORDER BY COALESCE(metadata->>'model', '-')) AS models
FROM model_usage
WHERE workflow_id = :'wf'
GROUP BY profile_name
ORDER BY profile_name;

\echo '\n=== 4. TOKEN SAVIOR BREAKDOWN ==='
SELECT profile_name, COUNT(*) AS rows,
       SUM(input_tokens_original)     AS original,
       SUM(input_tokens_after_savior) AS packed,
       SUM(tokens_saved)              AS saved,
       ROUND((AVG(reduction_ratio) * 100)::numeric, 2) AS avg_pct,
       ROUND(SUM(estimated_cost_saved_usd)::numeric, 4) AS cost_saved_usd
FROM engineering_token_savior_usage
WHERE feature_id = :'wf'
GROUP BY profile_name
ORDER BY profile_name;

\echo '\n=== 5. PLAN CONTRACT(S) ==='
SELECT id, version, status, active,
       jsonb_array_length(coalesce(validation_errors, '[]'::jsonb)) AS validator_err_count,
       jsonb_array_length(coalesce(council_reports,    '[]'::jsonb)) AS council_reports,
       LEFT(contract_hash, 12) AS hash_prefix,
       to_char(created_at, 'YYYY-MM-DD HH24:MI') AS created_at
FROM engineering_plan_contracts
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 6. TASK CONTRACTS (slice metadata) ==='
SELECT task_id, role, status, contract_version,
       LEFT(input_contract_hash, 12) AS in_hash,
       jsonb_array_length(coalesce(validation_errors, '[]'::jsonb)) AS validator_errs,
       to_char(created_at, 'YYYY-MM-DD HH24:MI') AS created_at
FROM engineering_task_contracts
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 7. HANDOFF CHAIN ==='
SELECT handoff_type, from_task_id, to_task_id, status,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM engineering_handoffs
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 8. RECOVERY ACTIONS ==='
SELECT blocker_code, action, status, attempt, max_attempts,
       LEFT(outcome,  60) AS outcome
FROM engineering_recovery_actions
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 9. QA SIGN-OFFS (scrutiny / usertest) ==='
SELECT validator_type, verdict, milestone_id, task_id,
       jsonb_array_length(coalesce(evidence,     '[]'::jsonb)) AS evidence_n,
       jsonb_array_length(coalesce(artifact_ids, '[]'::jsonb)) AS artifact_n,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM engineering_qa_signoffs
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 10. OPERATOR INTERVENTIONS ==='
SELECT actor, action_type,
       LEFT(payload::text, 80) AS payload_snippet,
       to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM engineering_operator_interventions
WHERE feature_id = :'wf'
ORDER BY created_at;

\echo '\n=== 11. SELF-REPAIR ISSUES (if any) ==='
SELECT i.id, i.task_id, i.code, i.state, LEFT(i.summary, 80) AS summary,
       (SELECT COUNT(*) FROM engineering_self_repair_deliberations d
          WHERE d.issue_id = i.id) AS deliberations,
       to_char(i.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
FROM engineering_self_repair_issues i
JOIN engineering_feature_children efc ON efc.task_id = i.task_id
WHERE efc.feature_id = :'wf'
ORDER BY i.created_at;
SQL
}

main() {
  local sub="${1:-help}"
  case "$sub" in
    list)   cmd_list "${2:-20}" ;;
    review) cmd_review "${2:-}" ;;
    help|-h|--help) usage ;;
    *)
      echo "ERROR: unknown subcommand: $sub" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
