import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useApi, type DagPayload } from "../api";
import { CostCell, RoleBadge, StatusPill, TokenCell, WallClockBar } from "../components/primitives";

const roleLanes = ["planner", "designer", "qa", "qa.author", "implementer", "reviewer", "qa.verify.scrutiny", "qa.verify.usertest", "recovery"];
const laneLabelWidth = 138;
const minMilestoneWidth = 196;
const nodeWidth = 128;
const nodeHeight = 42;
const laneHeight = 70;
const topPad = 40;
const sameLaneGap = nodeWidth + 12;

export function DagView({ featureId }: { featureId: string }) {
  const { data } = useApi<DagPayload>(`/api/features/${featureId}/dag`);
  const tasks = (data?.tasks || []).filter((task) => task.status !== "superseded");
  const groups = graphGroups(data);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedTask = tasks.find((task) => task.id === selected) || tasks[0];
  const milestoneWidth = Math.max(minMilestoneWidth, nodeWidth + Math.max(0, maxSameLaneStack(tasks, groups) - 1) * sameLaneGap + 48);
  const positions = useMemo(() => layout(tasks, groups, milestoneWidth), [tasks, groups, milestoneWidth]);
  const canvas = {
    width: laneLabelWidth + Math.max(groups.length, 1) * milestoneWidth + 56,
    height: topPad + roleLanes.length * laneHeight + 54
  };

  return (
    <div className="cc-dag-pane">
      <div className="cc-dag-canvas-wrap cc-grid-bg">
        <div className="cc-dag-toolbar">
          <div className="cc-dag-toolbar-group"><span className="cc-kicker mono">LAYOUT</span><span className="cc-chip is-on">{groups.mode === "slice" ? "slice lanes" : "milestone lanes"} · LR</span><span className="cc-chip">klay</span><span className="cc-chip">force</span></div>
          <div className="cc-dag-toolbar-group"><span className="cc-kicker mono">FILTER</span><span className="cc-chip is-on">all roles</span><span className="cc-chip">running</span><span className="cc-chip">blocked</span><span className="cc-chip">show superseded</span></div>
          <span className="mono cc-dim" style={{ marginLeft: "auto" }}>{tasks.length} tasks · {data?.edges.length || 0} edges</span>
        </div>
        {data && tasks.length === 0 ? (
          <div className="cc-state cc-state-dag-empty">
            <div className="cc-kicker mono">DAG · plan not consolidated</div>
            <div className="cc-state-title">No task graph yet</div>
            <div className="cc-state-desc">No PlanContract/task contracts have been persisted for this feature.</div>
          </div>
        ) : (
          <div className="cc-dag-scroll">
          <svg className="cc-dag-svg" viewBox={`0 0 ${canvas.width} ${canvas.height}`} style={{ width: canvas.width, height: canvas.height }} xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="dagArrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0 0L8 4L0 8z" fill="rgba(170,178,188,0.5)" />
              </marker>
            </defs>
            {groups.map((milestone, idx) => {
              const x = laneLabelWidth + idx * milestoneWidth - 10;
              return (
                <g key={milestone.id}>
                  <rect x={x} y={topPad - 12} width={milestoneWidth - 16} height={roleLanes.length * laneHeight + 16} rx="3" fill="transparent" stroke={milestone.id === groupId(selectedTask, groups.mode) ? "var(--accent-line)" : "rgba(255,255,255,0.10)"} strokeDasharray={isMilestoneComplete(tasks, milestone.id, groups.mode) ? "0" : "3 3"} />
                  <text x={x + (milestoneWidth - 16) / 2} y={topPad - 18} textAnchor="middle" fill="rgba(170,178,188,0.7)" style={{ font: "500 9.5px var(--f-mono)", letterSpacing: "0.1em" }}>{milestone.id.toUpperCase()} · {milestone.label.toUpperCase().slice(0, 22)}</text>
                </g>
              );
            })}
            {roleLanes.map((role, idx) => <text key={role} x={12} y={topPad + 24 + idx * laneHeight} fill="rgba(110,119,130,0.85)" style={{ font: "500 9px var(--f-mono)", letterSpacing: "0.1em" }}>{role.toUpperCase()}</text>)}
            {(data?.edges || []).map((edge) => {
              const a = positions[edge.from];
              const b = positions[edge.to];
              if (!a || !b) return null;
              const x1 = a.x + nodeWidth;
              const y1 = a.y + nodeHeight / 2;
              const x2 = b.x;
              const y2 = b.y + nodeHeight / 2;
              const mid = (x1 + x2) / 2;
              return <path key={`${edge.from}->${edge.to}`} d={`M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`} fill="none" stroke={edge.kind === "milestone_lock" ? "oklch(0.82 0.13 70 / 0.65)" : "rgba(170,178,188,0.35)"} strokeWidth={edge.kind === "milestone_lock" ? 1.4 : 1} strokeDasharray={edge.kind === "milestone_lock" ? "4 3" : "0"} markerEnd="url(#dagArrow)" />;
            })}
            {tasks.map((task) => {
              const p = positions[task.id];
              if (!p) return null;
              const isSelected = task.id === selectedTask?.id;
              return (
                <g key={task.id} transform={`translate(${p.x},${p.y})`} className="cc-dag-node" onClick={() => setSelected(task.id)}>
                  <rect x="0" y="0" width={nodeWidth} height={nodeHeight} rx="3" fill="var(--panel)" stroke={isSelected ? "var(--accent)" : roleColor(task.role)} strokeWidth={isSelected ? 1.8 : 1} />
                  <rect x="0" y="0" width="3" height={nodeHeight} fill={roleColor(task.role)} />
                  <text x="8" y="14" fill="var(--t1)" style={{ font: "500 10.5px var(--f-mono)" }}>{shortId(task.id)}</text>
                  <text x="8" y="27" fill="rgba(170,178,188,0.7)" style={{ font: "400 9.5px var(--f-sans)" }}>{task.role}</text>
                  <circle cx={nodeWidth - 11} cy="10" r="3" fill={`var(--st-${statusClass(task.status)})`} />
                </g>
              );
            })}
          </svg>
          </div>
        )}
      </div>
      <aside className="cc-dag-side cc-scroll">
        {selectedTask ? (
          <>
            <div className="cc-dag-side-hd">
              <div className="cc-kicker mono">SELECTED · TASK</div>
              <div className="cc-dag-side-title mono">{shortId(selectedTask.id)}</div>
              <div className="mono cc-dim cc-break">{selectedTask.id}</div>
              <div style={{ display: "flex", gap: 6, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}><StatusPill status={selectedTask.status} /><span className="mono cc-dim">milestone {selectedTask.milestone_id.toUpperCase()}</span>{selectedTask.task_slice_id && <span className="mono cc-dim">slice {selectedTask.task_slice_id}</span>}</div>
            </div>
            <div className="cc-dag-side-sect" style={{ padding: 12 }}><RoleBadge role={selectedTask.role} full /></div>
            <div className="cc-dag-side-sect">
              <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">DEPENDENCIES</span><span className="cc-panelhd-title">depends_on</span></div></div>
              <div style={{ padding: "0 12px 12px" }} className="mono cc-dim">{selectedTask.depends_on.length ? selectedTask.depends_on.map(shortId).join(" · ") : "none"}</div>
            </div>
            {selectedTask.last_run && (
              <div className="cc-dag-side-sect">
                <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">CURRENT RUN</span><span className="cc-panelhd-title">#{selectedTask.last_run.id}</span></div><StatusPill status={selectedTask.last_run.status} /></div>
                <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                  <KV k="phase" v={selectedTask.last_run.phase} />
                  <KV k="cost" v={<CostCell micros={selectedTask.last_run.cost_usd_micros} />} />
                  <KV k="tokens in" v={<><TokenCell value={selectedTask.last_run.input_tokens} /> · <TokenCell value={selectedTask.last_run.cached_input_tokens} kind="cached" /> cached</>} />
                  <KV k="tokens out" v={<><TokenCell value={selectedTask.last_run.output_tokens} kind="output" /> · <TokenCell value={selectedTask.last_run.reasoning_tokens} kind="reasoning" /> reasoning</>} />
                  <WallClockBar split={{ queue: selectedTask.last_run.queued_seconds, lease: selectedTask.last_run.leased_seconds, model: selectedTask.last_run.model_seconds, verify: selectedTask.last_run.verification_seconds, blocked: selectedTask.last_run.blocked_seconds }} label />
                </div>
              </div>
            )}
          </>
        ) : <div className="cc-state" style={{ margin: 14 }}><div className="cc-state-title">No task selected</div><div className="cc-state-desc">Task details appear here after the graph is available.</div></div>}
      </aside>
    </div>
  );
}

type GraphGroup = { id: string; label: string };
type GraphGroups = GraphGroup[] & { mode: "milestone" | "slice" };

function graphGroups(data?: DagPayload): GraphGroups {
  const items = (() => {
    if (!data) return [];
    if (data.milestones.length > 1) {
      return data.milestones.map((milestone) => ({ id: milestone.id, label: milestone.label }));
    }
    const seen = new Set<string>();
    return data.tasks.flatMap((task) => {
      const id = task.task_slice_id || task.milestone_id || "unassigned";
      if (seen.has(id)) return [];
      seen.add(id);
      return [{ id, label: sliceLabel(id) }];
    });
  })() as GraphGroups;
  items.mode = data && data.milestones.length > 1 ? "milestone" : "slice";
  return items;
}

function layout(tasks: DagPayload["tasks"], groups: GraphGroups, milestoneWidth: number) {
  const out: Record<string, { x: number; y: number }> = {};
  const groupIds = groups.length ? groups.map((m) => m.id) : ["unassigned"];
  const laneSlots: Record<string, number> = {};
  tasks.forEach((task) => {
    const mi = Math.max(0, groupIds.indexOf(groupId(task, groups.mode)));
    const lane = Math.max(0, roleLanes.indexOf(task.role));
    const key = `${mi}:${lane}`;
    const slot = laneSlots[key] || 0;
    laneSlots[key] = slot + 1;
    out[task.id] = {
      x: laneLabelWidth + mi * milestoneWidth + slot * sameLaneGap,
      y: topPad + lane * laneHeight
    };
  });
  return out;
}

function maxSameLaneStack(tasks: DagPayload["tasks"], groups: GraphGroups) {
  const counts = new Map<string, number>();
  tasks.forEach((task) => {
    const key = `${groupId(task, groups.mode)}:${task.role}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return Math.max(1, ...counts.values());
}

function groupId(task: DagPayload["tasks"][number] | undefined, mode: "milestone" | "slice") {
  if (!task) return "";
  return mode === "slice" ? task.task_slice_id || task.milestone_id : task.milestone_id;
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return <div className="mono cc-dim"><span>{k}</span> <span style={{ color: "var(--t1)" }}>{v}</span></div>;
}

function shortId(id: string) {
  return id.length > 18 ? `${id.slice(0, 8)}...${id.slice(-6)}` : id;
}

function statusClass(status: string) {
  if (["completed", "complete", "done", "passed", "pass"].includes(status)) return "pass";
  if (["failed", "fail"].includes(status)) return "fail";
  if (status === "blocked") return "block";
  if (["active", "running"].includes(status)) return "run";
  if (status === "paused") return "pause";
  return "queue";
}

function roleColor(role: string) {
  if (role === "planner") return "var(--r-planner)";
  if (role === "designer") return "var(--r-planner)";
  if (role === "reviewer") return "var(--r-review)";
  if (role.includes("usertest")) return "var(--r-qa-test)";
  if (role.includes("qa.verify")) return "var(--r-qa-scrut)";
  if (role.includes("qa")) return "var(--r-qa-author)";
  if (role === "recovery") return "var(--r-recovery)";
  return "var(--r-impl)";
}

function isMilestoneComplete(tasks: DagPayload["tasks"], milestoneId: string, mode: "milestone" | "slice") {
  const scoped = tasks.filter((task) => groupId(task, mode) === milestoneId);
  return scoped.length > 0 && scoped.every((task) => ["completed", "complete", "done", "passed", "pass"].includes(task.status));
}

function sliceLabel(id: string) {
  return id.replace(/^impl-/, "implement ").replace(/^qa-/, "qa ").replace(/-/g, " ");
}
