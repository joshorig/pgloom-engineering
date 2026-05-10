import { useApi, type CouncilRow } from "../api";
import type { ReactNode } from "react";
import { CostCell, Panel, RoleBadge, StatusPill, TaskLink, TokenCell } from "../components/primitives";

type Row = Record<string, unknown>;

export function CouncilsList({ featureId }: { featureId: string }) {
  const { data } = useApi<CouncilRow[]>(`/api/features/${featureId}/councils`);
  const rows = data || [];
  if (!rows.length) return <EmptyCouncil title="No council runs yet" desc="Planner and reviewer councils appear here after council persistence or legacy plan reports are present." />;
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">COUNCILS · {rows.length}</div>
          <h2 className="cc-v-title">Planner and reviewer deliberation runs</h2>
        </div>
      </div>
      <Panel kicker="COUNCIL RUNS" title="Normalised and legacy reports">
        <table className="cc-table">
          <thead><tr><th>council</th><th>role</th><th>purpose</th><th>status</th><th>critic</th><th style={{ textAlign: "right" }}>cost</th><th style={{ textAlign: "right" }}>tokens</th></tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.id} onClick={() => { window.location.href = `/feature/${featureId}/councils/${row.id}`; }}>
              <td className="mono"><a href={`/feature/${featureId}/councils/${row.id}`}>{shortId(row.id)}</a>{row.legacy && <span className="cc-chip" style={{ marginLeft: 6 }}>legacy</span>}</td>
              <td><RoleBadge role={row.role} /></td>
              <td className="mono cc-dim">{row.purpose || "-"}</td>
              <td><StatusPill status={row.status} /></td>
              <td className="mono cc-dim">{row.critic_verdict || unavailable(row)}</td>
              <td style={{ textAlign: "right" }}>{row.legacy ? unavailable(row) : <CostCell micros={row.cost_usd_micros} precision={2} />}</td>
              <td style={{ textAlign: "right" }}>{row.legacy ? unavailable(row) : <TokenCell value={row.total_tokens} />}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>
    </div>
  );
}

export function CouncilView({ featureId, councilId }: { featureId: string; councilId: string }) {
  const { data } = useApi<CouncilRow>(`/api/features/${featureId}/councils/${encodeURIComponent(councilId)}`);
  const panelists = (data?.panelists || []) as Row[];
  const runs = data?.worker_runs || [];
  if (!data) return <EmptyCouncil title="Loading council" desc="Waiting for council detail." />;
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">COUNCIL · {shortId(data.id)}</div>
          <h2 className="cc-v-title">{data.purpose || "Council run"}</h2>
          <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>{data.id}</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          {data.legacy && <span className="cc-chip">legacy</span>}
          <RoleBadge role={data.role} full />
          <StatusPill status={data.status} />
        </div>
      </div>

      <div className="cc-stat-row">
        <StatLite k="iterations" v={`${data.iterations_used ?? unavailable(data)} / ${data.iteration_max ?? unavailable(data)}`} d="critic loop" />
        <StatLite k="critic verdict" v={data.critic_verdict || unavailable(data)} d="final decision" />
        <StatLite k="cost" v={data.legacy ? unavailable(data) : <CostCell micros={data.cost_usd_micros} precision={2} />} d="council roll-up" />
        <StatLite k="tokens" v={data.legacy ? unavailable(data) : <TokenCell value={data.total_tokens} />} d="panelists + critic" />
        <StatLite k="task" v={<TaskLink featureId={featureId} taskId={data.task_id} subtle />} d="scoped council" />
      </div>

      {data.legacy && (
        <div className="cc-banner cc-banner-pause">
          <span className="mono cc-banner-tag">LEGACY</span>
          <div style={{ flex: 1 }}>This council was projected from plan-contract JSON. Per-panelist metrics are unavailable (legacy).</div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Panel kicker="ITERATIONS" title="Panelists and critic lane">
          {panelists.length ? <MiniTable rows={panelists} /> : <EmptyInline text={data.legacy ? "Panelist rows unavailable (legacy)." : "No panelist rows persisted yet."} />}
        </Panel>
        <Panel kicker="WORKER RUNS" title="Joined execution">
          {runs.length ? <MiniTable rows={runs as unknown as Row[]} /> : <EmptyInline text={data.legacy ? "Worker runs unavailable (legacy)." : "No worker runs joined to this council."} />}
        </Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Panel kicker="DIFF LANE" title={data.legacy ? "unavailable (legacy)" : "Consensus deltas"}>
          <pre className="cc-diff mono">{JSON.stringify(data.report || objectValue(data.metadata), null, 2)}</pre>
        </Panel>
        <Panel kicker="OUTCOME" title="Decision and telemetry">
          <pre className="cc-diff mono">{JSON.stringify(data, null, 2)}</pre>
        </Panel>
      </div>
    </div>
  );
}

function MiniTable({ rows }: { rows: Row[] }) {
  const keys = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8);
  return (
    <table className="cc-table">
      <thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead>
      <tbody>{rows.map((row, idx) => <tr key={String(row.id || idx)}>{keys.map((key) => <td key={key} className="mono cc-dim">{renderValue(row[key])}</td>)}</tr>)}</tbody>
    </table>
  );
}

function StatLite({ k, v, d }: { k: string; v: ReactNode; d?: ReactNode }) {
  return <div className="cc-stat"><div className="cc-stat-k">{k}</div><div className="cc-stat-v">{v}</div><div className="cc-stat-d">{d}</div></div>;
}

function EmptyCouncil({ title, desc }: { title: string; desc: string }) {
  return <div className="cc-pane cc-scroll" style={{ padding: 14 }}><div className="cc-state"><div className="cc-kicker mono">COUNCILS</div><div className="cc-state-title">{title}</div><div className="cc-state-desc">{desc}</div></div></div>;
}

function EmptyInline({ text }: { text: string }) {
  return <div className="cc-state" style={{ margin: 14 }}><div className="cc-state-desc">{text}</div></div>;
}

function renderValue(value: unknown) {
  if (value == null) return "-";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 100);
  return String(value);
}

function unavailable(row: { legacy?: boolean }) {
  return row.legacy ? "unavailable (legacy)" : "-";
}

function objectValue(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function shortId(id: string) {
  return id.length > 22 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id;
}
