import { useEffect, useState } from "react";
import { postIntervention, useApi, type DagPayload } from "./api";
import { RoleBadge } from "./components/primitives";

export function ReplanDialog({ featureId }: { featureId: string }) {
  const [open, setOpen] = useState(false);
  const [milestone, setMilestone] = useState("");
  const [reason, setReason] = useState("");
  const { data } = useApi<DagPayload>(open ? `/api/features/${featureId}/dag` : null);

  useEffect(() => {
    const listener = () => setOpen(true);
    window.addEventListener("cc:replan", listener);
    return () => window.removeEventListener("cc:replan", listener);
  }, []);

  useEffect(() => {
    if (!milestone && data?.milestones[0]) setMilestone(data.milestones[0].id);
  }, [data, milestone]);

  if (!open) return null;
  const targetIndex = data?.milestones.findIndex((item) => item.id === milestone) ?? 0;
  const frozenMilestones = new Set((data?.milestones || []).slice(0, Math.max(0, targetIndex)).map((item) => item.id));
  const frozen = (data?.tasks || []).filter((task) => frozenMilestones.has(task.milestone_id));
  const superseded = (data?.tasks || []).filter((task) => !frozenMilestones.has(task.milestone_id));

  const submit = async () => {
    if (!reason.trim() || !milestone) return;
    await postIntervention(featureId, "replan_from_milestone", { milestone_id: milestone, reason });
    setOpen(false);
  };

  return (
    <div className="cc" style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(7,9,11,0.55)", backdropFilter: "blur(2px)" }}>
      <div className="cc-replan-dialog">
        <div className="cc-replan-hd">
          <div><div className="cc-kicker mono">REPLAN · from milestone</div><h2 className="cc-replan-title">Replan from <span className="mono cc-accent-ink">{milestone || "milestone"}</span>?</h2></div>
          <button className="cc-btn cc-btn-ghost cc-replan-x" onClick={() => setOpen(false)}>x</button>
        </div>
        <div className="cc-replan-body">
          <p className="cc-replan-desc">Planner receives the prior consolidated plan as baseline. Tasks before the selected milestone are frozen and must remain byte-identical; tasks at or after it may be superseded and rewritten.</p>
          <select className="cc-input" value={milestone} onChange={(event) => setMilestone(event.target.value)}>
            {(data?.milestones || []).map((item) => <option key={item.id} value={item.id}>{item.id} · {item.label}</option>)}
          </select>
          <div className="cc-replan-grid">
            <TaskList title="Frozen prefix" tasks={frozen} suffix="byte-identical" />
            <TaskList title="Superseded · queryable" tasks={superseded} suffix="superseded" />
          </div>
          <div className="cc-replan-reason">
            <label className="cc-kicker mono" htmlFor="replan-reason">REASON · stamped on the audit row</label>
            <textarea id="replan-reason" className="mono" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} />
          </div>
        </div>
        <div className="cc-replan-ft">
          <span className="mono cc-dim">writes one intervention row · baseline plan attached downstream</span>
          <div style={{ display: "flex", gap: 6 }}><button className="cc-btn cc-btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button className="cc-btn cc-btn-primary" disabled={!reason.trim()} onClick={() => void submit()}>Replan</button></div>
        </div>
      </div>
    </div>
  );
}

function TaskList({ title, tasks, suffix }: { title: string; tasks: DagPayload["tasks"]; suffix: string }) {
  return (
    <div className="cc-replan-col">
      <div className="cc-replan-col-hd"><span className="mono cc-dim">{title}</span><span className="mono">{tasks.length}</span></div>
      <ul className="cc-replan-list">
        {tasks.map((task) => (
          <li key={task.id}>
            <RoleBadge role={task.role} />
            <span className="mono">{task.id}</span>
            <span className="cc-dim">{task.status}</span>
            <span className="mono cc-replan-old">{suffix}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
