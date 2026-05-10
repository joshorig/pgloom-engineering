import useSWR, { mutate } from "swr";

export type FeatureRow = {
  id?: string;
  feature_id?: string;
  project: string;
  branch?: string;
  state: string;
  paused?: boolean;
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
  started_at?: string | null;
};

export type ModelUsageRow = {
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

export type DagPayload = {
  milestones: Array<{ id: string; label: string; task_ids: string[] }>;
  tasks: Array<{
    id: string;
    role: string;
    status: string;
    depends_on: string[];
    milestone_id: string;
    last_run?: RunRow;
  }>;
  edges: Array<{ from: string; to: string; kind: string }>;
};

export type CCEvent = {
  kind: string;
  feature_id?: string;
  row_id?: string | number;
  fields?: string[];
  reason?: string;
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
