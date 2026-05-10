import type { ReactNode } from "react";
import { useApi, type RunRow, type TaskHeader } from "../api";
import { CostCell, Panel, RoleBadge, StatusPill, TaskLink, TokenCell, WallClockBar } from "../components/primitives";
import { formatSeconds } from "../lib/money";

type Row = Record<string, unknown>;
type Telemetry = Record<string, number>;

export function TaskView({ featureId, taskId }: { featureId: string; taskId: string }) {
  const base = `/api/features/${featureId}/tasks/${encodeURIComponent(taskId)}`;
  const { data: task } = useApi<TaskHeader>(base);
  const { data: runs } = useApi<RunRow[]>(`${base}/runs`);
  const { data: handoffs } = useApi<Row[]>(`${base}/handoffs`);
  const { data: qa } = useApi<Row[]>(`${base}/qa`);
  const { data: recovery } = useApi<Row[]>(`${base}/recovery`);
  const { data: interventions } = useApi<Row[]>(`${base}/interventions`);
  const { data: artifacts } = useApi<Row[]>(`${base}/artifacts`);
  const { data: telemetry } = useApi<Telemetry>(`${base}/telemetry`);
  const runRows = runs || [];
  const artifactRows = artifacts || [];
  const input = objectValue(task?.input_contract);
  const output = objectValue(task?.output_contract);

  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">TASK · {shortId(taskId)}</div>
          <h2 className="cc-v-title">{task?.task_slice_id || task?.task_type || task?.role || "Task contract"}</h2>
          <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>{taskId}</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <RoleBadge role={task?.role} full />
          <StatusPill status={task?.runtime_state || task?.status} />
          {task?.milestone_id && <span className="cc-chip is-on">milestone {task.milestone_id}</span>}
        </div>
      </div>

      {task?.terminal_reason && (
        <div className="cc-banner cc-banner-pause">
          <span className="mono cc-banner-tag">{task.terminal_reason}</span>
          <div style={{ flex: 1 }}>{task.terminal_detail || "Terminal task reason recorded."}</div>
        </div>
      )}

      <div className="cc-stat-row">
        <StatLite k="runs" v={String(runRows.length)} d="worker attempts" />
        <StatLite k="cost" v={<CostCell micros={telemetry?.cost_usd_micros} precision={2} />} d="task total" />
        <StatLite k="wall-clock" v={formatSeconds(telemetry?.running_seconds)} d="running_seconds" />
        <StatLite k="tokens in" v={<TokenCell value={telemetry?.input_tokens} />} d={`${telemetry?.cached_input_tokens || 0} cached`} />
        <StatLite k="tokens out" v={<TokenCell value={telemetry?.output_tokens} kind="output" />} d={`${telemetry?.reasoning_tokens || 0} reasoning`} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Panel kicker="CONTRACT" title="Inputs and output contract">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, padding: 14 }}>
            <pre className="cc-diff mono">{JSON.stringify(input, null, 2)}</pre>
            <pre className="cc-diff mono">{JSON.stringify(output, null, 2)}</pre>
          </div>
        </Panel>
        <Panel kicker="WALL CLOCK" title="Stacked worker-run timeline">
          <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
            {runRows.map((run) => (
              <div key={run.id} className="cc-lock is-held">
                <div className="cc-lock-hd">
                  <span className="mono">run #{run.id}</span>
                  <StatusPill status={run.status} />
                  {run.terminal_reason && <span className="cc-chip" title={run.terminal_detail || ""}>{run.terminal_reason}</span>}
                {run.council_run_id && <a className="cc-chip is-on" href={`/feature/${featureId}/councils/${run.council_run_id}`}>View council</a>}
                </div>
                <div style={{ paddingTop: 8 }}>
                  <WallClockBar split={{ queue: run.queued_seconds, lease: run.leased_seconds, model: run.model_seconds, verify: run.verification_seconds, blocked: run.blocked_seconds }} label />
                </div>
              </div>
            ))}
            {!runRows.length && <EmptyInline text="No worker runs for this task yet." />}
          </div>
        </Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
        <Panel kicker="HANDOFFS" title="In and out contracts"><MiniTable rows={handoffs || []} featureId={featureId} /></Panel>
        <Panel kicker="QA" title="Signoffs"><MiniTable rows={qa || []} featureId={featureId} /></Panel>
        <Panel kicker="RECOVERY" title="Corrective actions"><MiniTable rows={recovery || []} featureId={featureId} /></Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 14 }}>
        <Panel kicker="INTERVENTIONS" title="Task-scoped operator actions"><MiniTable rows={interventions || []} featureId={featureId} /></Panel>
        <Panel kicker="ARTIFACTS" title="Evidence gallery">
          <div className="cc-slots" style={{ padding: 14 }}>
            {artifactRows.map((artifact) => (
              <div key={String(artifact.id)} className="cc-slot is-idle">
                <div className="cc-slot-hd">
                  <span className="mono cc-slot-num">{String(artifact.name || artifact.id)}</span>
                  <span className={`cc-slot-state mono ${isPrimaryArtifact(artifact) ? "st-run" : "st-idle"}`}>{String(artifact.kind || "-")}</span>
                </div>
                <div className="cc-slot-row"><span className="cc-dim">path</span><span className="mono">{String(artifact.path || "-")}</span></div>
                <div className="cc-slot-row"><span className="cc-dim">sha</span><span className="mono">{shortId(String(artifact.sha256 || "-"))}</span></div>
                {isPrimaryArtifact(artifact) && <pre className="cc-diff mono">{JSON.stringify(artifact.metadata || {}, null, 2)}</pre>}
              </div>
            ))}
            {!artifactRows.length && <EmptyInline text="No artifacts recorded for this task." />}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function MiniTable({ rows, featureId }: { rows: Row[]; featureId: string }) {
  const keys = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 6);
  if (!rows.length) return <EmptyInline text="No rows yet." />;
  return (
    <table className="cc-table">
      <thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead>
      <tbody>{rows.map((row, idx) => <tr key={String(row.id || idx)}>{keys.map((key) => <td key={key} className="mono cc-dim">{renderCell(row[key], featureId)}</td>)}</tr>)}</tbody>
    </table>
  );
}

function renderCell(value: unknown, featureId: string) {
  if (value == null) return "-";
  if (typeof value === "string" && (value.startsWith("task-") || value === "t0" || value === "t1")) {
    return <TaskLink featureId={featureId} taskId={value} subtle />;
  }
  if (typeof value === "object") return JSON.stringify(value).slice(0, 90);
  return String(value);
}

function StatLite({ k, v, d }: { k: string; v: ReactNode; d?: ReactNode }) {
  return <div className="cc-stat"><div className="cc-stat-k">{k}</div><div className="cc-stat-v">{v}</div><div className="cc-stat-d">{d}</div></div>;
}

function EmptyInline({ text }: { text: string }) {
  return <div className="cc-state" style={{ margin: 14 }}><div className="cc-state-desc">{text}</div></div>;
}

function isPrimaryArtifact(row: Row) {
  return row.kind === "worktree_diff" || row.kind === "file_snapshots";
}

function objectValue(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function shortId(id: string) {
  return id.length > 22 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id;
}
