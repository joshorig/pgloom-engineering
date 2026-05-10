import { useState } from "react";
import { postIntervention, useApi, type CCEvent, type DagPayload, type FeatureRow, type RunRow } from "../api";
import { CostCell, LiveEventStrip, Panel, PauseButton, RoleBadge, Stat, StatusPill } from "../components/primitives";
import { formatSeconds, formatTokens } from "../lib/money";

type Props = { featureId: string; events: CCEvent[] };

export function FeatureOverview({ featureId, events }: Props) {
  const { data, mutate } = useApi<FeatureRow>(`/api/features/${featureId}`);
  const { data: dag } = useApi<DagPayload>(`/api/features/${featureId}/dag`);
  const { data: runs } = useApi<RunRow[]>(`/api/features/${featureId}/runs`);
  const { data: interventions } = useApi<Array<Record<string, unknown>>>(`/api/features/${featureId}/interventions`);
  const [busy, setBusy] = useState(false);
  const paused = !!data?.paused;
  const runRows = runs || [];
  const wallMix = runRows.reduce((acc, row) => ({
    queue: acc.queue + Number(row.queued_seconds || 0),
    lease: acc.lease + Number(row.leased_seconds || 0),
    model: acc.model + Number(row.model_seconds || 0),
    verify: acc.verify + Number(row.verification_seconds || 0),
    blocked: acc.blocked + Number(row.blocked_seconds || 0)
  }), { queue: 0, lease: 0, model: 0, verify: 0, blocked: 0 });
  const progression = progressionItems(dag);
  const currentMilestone = currentProgressionId(progression.items);
  const nextTask = dag?.tasks.find((task) => !["completed", "complete", "done", "passed"].includes(task.status)) || dag?.tasks[0];
  const togglePause = async () => {
    setBusy(true);
    try {
      await postIntervention(featureId, paused ? "resume_feature" : "pause_feature", {});
      await mutate();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: "flex", flexDirection: "column" }}>
      {paused && (
        <div className="cc-banner cc-banner-pause">
          <span className="mono cc-banner-tag">PAUSED</span>
          <div style={{ flex: 1 }}>New dispatch is blocked at the worker pre-gate. In-flight runs continue to completion.</div>
          <PauseButton paused onClick={togglePause} />
        </div>
      )}
      {data?.state === "aborted" && (
        <div className="cc-banner cc-banner-pause">
          <span className="mono cc-banner-tag">{data.abort_reason || "unknown"}</span>
          <div style={{ flex: 1 }}>{data.abort_detail || "Feature aborted without additional detail."}</div>
        </div>
      )}
      <div className="cc-hero">
        <div className="cc-hero-l">
          <div className="cc-kicker mono">FEATURE · {shortId(featureId)}</div>
          <h1 className="cc-hero-title">{data?.project || "Feature"} · {data?.branch || "registered run"}</h1>
          <div className="cc-hero-meta mono">
            <span><span className="cc-dim">project</span> {data?.project || "-"}</span>
            <span><span className="cc-dim">branch</span> {data?.branch || "-"}</span>
            <span><span className="cc-dim">state</span> {data?.state || "-"}</span>
            <span><span className="cc-dim">plan</span> {shortId(dag?.milestones[0]?.id || "-")}</span>
            <span><span className="cc-dim">created</span> {data?.created_at || "-"}</span>
          </div>
        </div>
        <div className="cc-hero-r">
          <button disabled={busy} className={`cc-btn ${paused ? "cc-btn-primary" : "cc-btn-danger"}`} onClick={togglePause}>{paused ? "Resume feature" : "Pause feature"}</button>
          <button className="cc-btn cc-btn-ghost" onClick={() => window.dispatchEvent(new CustomEvent("cc:replan"))}>Replan from milestone...</button>
        </div>
      </div>
      <div className="cc-stat-row">
        <Stat k="cumulative cost" v={<CostCell micros={data?.cost_usd_micros} precision={2} />} d={<span className="cc-dim">worker/model_usage max</span>} />
        <Stat k="elapsed wall-clock" v={formatSeconds(data?.running_seconds)} d={<span className="cc-dim">{wallSummary(wallMix)}</span>} />
        <Stat k="runs · attempts" v={`${data?.runs || 0} · ${runRows.reduce((sum, row) => sum + Number(row.attempt || 0), 0)}`} d={<span className="cc-dim">all roles</span>} />
        <Stat k="tokens in" v={formatTokens(data?.input_tokens)} d={<span className="cc-tok-cached">{formatTokens(data?.cached_input_tokens)} cached</span>} />
        <Stat k="next claimable" v={nextTask ? <a href={`/feature/${featureId}/task/${encodeURIComponent(nextTask.id)}`}>{shortId(nextTask.id)}</a> : "-"} d={nextTask ? <RoleBadge role={nextTask.role} /> : <span className="cc-dim">none</span>} />
        <Stat k="savings" v={formatTokens((data?.token_savior_saved_tokens || 0) + (data?.rtk_saved_tokens || 0))} d={<span className="cc-dim">Token Savior + RTK</span>} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 14, minHeight: 0, flex: 1 }}>
        <Panel kicker={progression.mode === "slice" ? "TASK SLICES" : "MILESTONES"} title="Plan progression" action={<span className="mono cc-dim">{signedProgression(progression.items)} / {progression.items.length} signed</span>}>
          <div className="cc-ms-track">
            {progression.items.map((milestone) => {
              const tasks = milestone.tasks;
              const passed = tasks.filter((task) => ["completed", "complete", "done", "passed"].includes(task.status)).length;
              const running = tasks.filter((task) => ["active", "running"].includes(task.status)).length;
              const cur = milestone.id === currentMilestone;
              return (
                <div className={`cc-ms ${isMilestoneSigned(tasks) ? "is-signed" : cur ? "is-current" : ""}`} key={milestone.id}>
                  <div className="cc-ms-hd">
                    <span className="mono cc-ms-id">{milestone.id.toUpperCase()}</span>
                    <StatusPill status={isMilestoneSigned(tasks) ? "signed" : cur ? "running" : "queued"} />
                  </div>
                  <div className="cc-ms-label">{milestone.label}</div>
                  <div className="cc-ms-bar">
                    {tasks.map((task) => <i key={task.id} className={`cc-ms-dot st-${statusClass(task.status)}`} title={`${task.id} · ${task.role}`} />)}
                  </div>
                  <div className="cc-ms-meta mono cc-dim">{passed}/{tasks.length} done{running ? ` · ${running} active` : ""}{isMilestoneSigned(tasks) ? " · signed" : cur ? " · in progress" : " · locked"}</div>
                </div>
              );
            })}
            {dag && progression.items.length === 0 && <div className="cc-state" style={{ gridColumn: "1 / -1" }}><div className="cc-state-title">No PlanContract yet</div><div className="cc-state-desc">Milestones appear after planner consolidation persists plan/task contracts.</div></div>}
          </div>
        </Panel>
        <Panel kicker="INTERVENTIONS" title="Recent operator actions">
          <div className="cc-ints cc-scroll">
            {(interventions || []).slice(-8).reverse().map((row) => (
              <div className="cc-int" key={String(row.id)}>
                <div className="cc-int-hd"><span className={`cc-int-kind mono kind-${String(row.action_type).replace(/_/g, "-")}`}>{String(row.action_type)}</span><span className="mono cc-dim cc-int-id">#{String(row.id)}</span></div>
                <div className="mono cc-int-actor cc-dim">{String(row.actor || "-")}</div>
                <div className="cc-int-note mono cc-dim">{JSON.stringify(row.payload || {})}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <LiveEventStrip events={events} />
    </div>
  );
}

function shortId(id: string) {
  return id.length > 18 ? `${id.slice(0, 8)}...${id.slice(-6)}` : id;
}

function statusClass(status: string) {
  if (["completed", "complete", "done", "passed", "pass"].includes(status)) return "pass";
  if (["failed", "fail"].includes(status)) return "fail";
  if (["blocked"].includes(status)) return "block";
  if (["active", "running"].includes(status)) return "run";
  if (["paused"].includes(status)) return "pause";
  return "queue";
}

function isMilestoneSigned(tasks: Array<{ status: string }>) {
  return tasks.length > 0 && tasks.every((task) => ["completed", "complete", "done", "passed", "pass"].includes(task.status));
}

function signedMilestones(dag?: DagPayload) {
  return (dag?.milestones || []).filter((milestone) => isMilestoneSigned((dag?.tasks || []).filter((task) => task.milestone_id === milestone.id))).length;
}

type ProgressionItem = {
  id: string;
  label: string;
  tasks: DagPayload["tasks"];
};

function progressionItems(dag?: DagPayload): { mode: "milestone" | "slice"; items: ProgressionItem[] } {
  if (!dag) return { mode: "milestone", items: [] };
  if (dag.milestones.length > 1) {
    return {
      mode: "milestone",
      items: dag.milestones.map((milestone) => ({
        id: milestone.id,
        label: milestone.label,
        tasks: dag.tasks.filter((task) => task.milestone_id === milestone.id)
      }))
    };
  }
  const grouped = new Map<string, DagPayload["tasks"]>();
  dag.tasks.forEach((task) => {
    const key = task.task_slice_id || task.milestone_id || "unassigned";
    grouped.set(key, [...(grouped.get(key) || []), task]);
  });
  return {
    mode: "slice",
    items: [...grouped.entries()].map(([id, tasks]) => ({
      id,
      label: sliceLabel(id),
      tasks
    }))
  };
}

function signedProgression(items: ProgressionItem[]) {
  return items.filter((item) => isMilestoneSigned(item.tasks)).length;
}

function currentProgressionId(items: ProgressionItem[]) {
  return items.find((item) => item.tasks.some((task) => ["active", "running", "blocked"].includes(task.status)))?.id || items[0]?.id;
}

function sliceLabel(id: string) {
  return id.replace(/^impl-/, "implement ").replace(/^qa-/, "qa ").replace(/-/g, " ");
}

function wallSummary(mix: { queue: number; lease: number; model: number; verify: number; blocked: number }) {
  const total = mix.queue + mix.lease + mix.model + mix.verify + mix.blocked;
  if (!total) return "time split unavailable";
  const pct = (value: number) => `${Math.round((value / total) * 100)}%`;
  return `model ${pct(mix.model)} · verify ${pct(mix.verify)} · queue ${pct(mix.queue)}`;
}
