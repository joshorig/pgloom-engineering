import useSWR, { mutate } from "swr";

export type FeatureRow = {
  id?: string;
  feature_id?: string;
  project: string;
  branch?: string;
  state: string;
  paused?: boolean;
  abort_reason?: string | null;
  abort_detail?: string | null;
  aborted_at?: string | null;
  cost_usd_micros?: number;
  runs?: number;
  roles_seen?: string;
  last_blocker?: string | null;
  created_at?: string;
  updated_at?: string;
  input_tokens?: number;
  cached_input_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
  token_savior_saved_tokens?: number;
  rtk_saved_tokens?: number;
  running_seconds?: number;
};

export type RunRow = {
  id: number;
  feature_id?: string;
  project?: string;
  task_id?: string | null;
  role: string;
  phase: string;
  validator_type?: string | null;
  status: string;
  attempt: number;
  model_provider?: string | null;
  model?: string | null;
  input_tokens: number;
  cached_input_tokens: number;
  cache_creation_tokens?: number;
  output_tokens: number;
  reasoning_tokens: number;
  token_savior_saved_tokens: number;
  rtk_saved_tokens: number;
  queued_seconds?: number | null;
  leased_seconds?: number | null;
  model_seconds?: number | null;
  verification_seconds?: number | null;
  blocked_seconds?: number | null;
  running_seconds?: number | null;
  cost_usd_micros: number;
  council_run_id?: string | null;
  terminal_reason?: string | null;
  terminal_detail?: string | null;
  started_at?: string | null;
};

export type ModelUsageRow = {
  project?: string;
  profile_name: string;
  calls: number;
  input_tokens: number;
  cached_input_tokens?: number;
  output_tokens: number;
  reasoning_tokens?: number;
  cost_usd_micros: number;
  providers?: string;
  models?: string;
};

export type SlotRow = {
  slot: string;
  max: number;
  holding: number;
  running?: number;
  leased?: number;
  queued: number;
  blocked?: number;
  lock_count?: number;
  tasks?: Array<{
    project?: string | null;
    workflow_id?: string | null;
    task_id?: string | null;
    task_type?: string | null;
    state?: string | null;
    lease_owner?: string | null;
    lease_expires_at?: string | null;
    updated_at?: string | null;
  }>;
  holds?: Array<{
    resource_key?: string | null;
    project?: string | null;
    workflow_id?: string | null;
    owner_id?: string | null;
    task_id?: string | null;
    expires_at?: string | null;
  }>;
};

export type TokenSaviorRow = {
  project?: string;
  profile_name: string;
  rows: number;
  input_tokens_original: number;
  input_tokens_after_savior: number;
  tokens_saved: number;
  reduction_ratio?: number | null;
  estimated_cost_saved_usd_micros?: number;
};

export type DagPayload = {
  milestones: Array<{ id: string; label: string; task_ids: string[] }>;
  tasks: Array<{
    id: string;
    role: string;
    status: string;
    depends_on: string[];
    milestone_id: string;
    task_slice_id?: string | null;
    last_run?: RunRow;
  }>;
  edges: Array<{ from: string; to: string; kind: string }>;
};

export type TaskHeader = {
  task_id: string;
  feature_id: string;
  plan_contract_id?: string;
  role: string;
  status: string;
  runtime_state?: string | null;
  task_type?: string | null;
  slot?: string | null;
  milestone_id?: string | null;
  task_slice_id?: string | null;
  input_contract?: Record<string, unknown>;
  output_contract?: Record<string, unknown>;
  validation_errors?: unknown[];
  terminal_reason?: string | null;
  terminal_detail?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type CouncilRow = {
  id: string;
  feature_id: string;
  task_id?: string | null;
  role?: string | null;
  purpose?: string | null;
  status?: string | null;
  legacy?: boolean;
  critic_verdict?: string | null;
  cost_usd_micros?: number | null;
  total_tokens?: number | null;
  metadata?: Record<string, unknown>;
  iterations_used?: number | null;
  iteration_max?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  panelists?: Array<Record<string, unknown>>;
  worker_runs?: RunRow[];
  report?: Record<string, unknown>;
};

export type CCEvent = {
  v?: number;
  kind: string;
  feature_id?: string;
  row_id?: string | number;
  fields?: string[];
  ts?: string;
  reason?: string;
};

export type RealtimeStatus = {
  channel: string;
  subscribers: number;
  max_queue_size: number;
  start_realtime: boolean;
  database_configured: boolean;
};

const fetcher = async <T>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
};

export function useApi<T>(path: string | null) {
  return useSWR<T>(path, fetcher, { keepPreviousData: true });
}

export async function postIntervention(featureId: string, actionType: string, payload: object) {
  const res = await fetch(`/api/features/${encodeURIComponent(featureId)}/interventions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_type: actionType, payload })
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const body = await res.json();
  await mutate((key) => typeof key === "string" && key.includes(`/api/features/${featureId}`));
  return body;
}

export function refreshForEvent(event: CCEvent) {
  if (event.kind === "resync" || !event.feature_id) {
    void mutate(() => true);
    return;
  }
  const id = event.feature_id;
  void mutate((key) => typeof key === "string" && (key === "/api/features" || key.includes(`/api/features/${id}`)));
}
