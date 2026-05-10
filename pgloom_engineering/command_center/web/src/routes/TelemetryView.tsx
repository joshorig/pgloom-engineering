import { useApi, type ModelUsageRow, type RunRow } from "../api";
import { CostCell, Panel, RoleBadge, Stat, StatusPill, TokenCell, WallClockBar } from "../components/primitives";
import { formatMicros, formatSeconds, formatTokens } from "../lib/money";

export function TelemetryView({ featureId }: { featureId: string }) {
  const { data: runs } = useApi<RunRow[]>(`/api/features/${featureId}/runs`);
  const { data: modelUsage } = useApi<ModelUsageRow[]>(`/api/features/${featureId}/model-usage`);
  const rows = runs || [];
  const usage = modelUsage || [];
  const total = rows.reduce((a, r) => ({
    cost: a.cost + (r.cost_usd_micros || 0),
    input: a.input + (r.input_tokens || 0),
    cached: a.cached + (r.cached_input_tokens || 0),
    output: a.output + (r.output_tokens || 0),
    reasoning: a.reasoning + (r.reasoning_tokens || 0),
    wall: a.wall + (r.running_seconds || 0),
    queue: a.queue + (r.queued_seconds || 0),
    lease: a.lease + (r.leased_seconds || 0),
    model: a.model + (r.model_seconds || 0),
    verify: a.verify + (r.verification_seconds || 0),
    blocked: a.blocked + (r.blocked_seconds || 0),
    saved: a.saved + (r.token_savior_saved_tokens || 0) + (r.rtk_saved_tokens || 0)
  }), { cost: 0, input: 0, cached: 0, output: 0, reasoning: 0, wall: 0, queue: 0, lease: 0, model: 0, verify: 0, blocked: 0, saved: 0 });
  const modelCost = usage.reduce((sum, row) => sum + (row.cost_usd_micros || 0), 0);
  const displayCost = Math.max(total.cost, modelCost);
  const timeline = cumulative(rows);
  const roleRows = byRole(rows);
  const modelRows = usage.length ? usage : byRunModel(rows);

  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: "flex", flexDirection: "column" }}>
      <div className="cc-stat-row">
        <Stat k="cumulative cost" v={<CostCell micros={displayCost} precision={2} />} d={<span className="cc-dim">{costSource(total.cost, modelCost)}</span>} />
        <Stat k="cost source" v={displayCost > 0 ? modelCost > total.cost ? "model_usage" : "worker_runs" : "not persisted"} d={<span className="cc-dim">max(worker, model)</span>} />
        <Stat k="tokens (in)" v={formatTokens(total.input)} d={<span className="cc-tok-cached">{formatTokens(total.cached)} cached</span>} />
        <Stat k="tokens (out)" v={formatTokens(total.output)} d={<><span className="cc-tok-out">{formatTokens(total.output)}</span><span className="cc-tok-reason"> · {formatTokens(total.reasoning)} reason</span></>} />
        <Stat k="wall-clock" v={formatSeconds(total.wall)} d={<span className="cc-dim">{wallSummary(total)}</span>} />
        <Stat k="savior cache" v={`-${formatTokens(total.saved)}`} d={<span className="cc-dim">Token Savior + RTK</span>} />
      </div>

      <div className="cc-panel">
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">TIMELINE</span><span className="cc-panelhd-title">Cumulative cost · persisted runs</span></div><span className="mono cc-dim">{timeline.length} points</span></div>
        <div className="cc-tl"><CostTimeline points={timeline} /></div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">BY MODEL</span><span className="cc-panelhd-title">Cost share</span></div></div>
          <div className="cc-bymodel">
            {modelRows.map((row) => {
              const pct = pctOf(row.cost_usd_micros || 0, Math.max(modelCost, total.cost));
              return <div className="cc-bymodel-row" key={`${row.profile_name}-${row.models}`}><span className="mono cc-bymodel-id">{row.models || row.profile_name}</span><span className="cc-bymodel-bar"><span style={{ width: `${pct}%` }} /></span><span className="mono cc-bymodel-c">{formatMicros(row.cost_usd_micros, 2)}</span><span className="mono cc-dim cc-bymodel-r">{row.calls} calls</span></div>;
            })}
          </div>
        </div>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">BY ROLE</span><span className="cc-panelhd-title">Wall-clock share</span></div></div>
          <div className="cc-byrole">
            {roleRows.map((row) => <div className="cc-byrole-row" key={row.role}><RoleBadge role={row.role} full /><span className="cc-bymodel-bar" style={{ flex: 1 }}><span style={{ width: `${pctOf(row.wall, total.wall)}%` }} /></span><span className="mono">{formatSeconds(row.wall)}</span></div>)}
          </div>
        </div>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">WALL-CLOCK MIX</span><span className="cc-panelhd-title">Where time goes</span></div></div>
          <div style={{ padding: 14 }}>
            <WallClockBar split={{ queue: total.queue, lease: total.lease, model: total.model, verify: total.verify, blocked: total.blocked }} label />
            <div className="cc-wc-legend">
              {[
                ["queue", "queue & dispatch", total.queue],
                ["lease", "lease & claim", total.lease],
                ["model", "model inference", total.model],
                ["verify", "verify & test", total.verify],
                ["blocked", "blocked / waiting", total.blocked]
              ].map(([key, label, value]) => <div className="cc-wc-leg-row" key={String(key)}><i className={`cc-wc-leg-dot wc-${key}`} /><span>{label}</span><span className="mono cc-dim" style={{ marginLeft: "auto" }}>{formatSeconds(Number(value))}</span></div>)}
            </div>
          </div>
        </div>
      </div>

      <Panel kicker="RUNS" title="Last worker runs" action={<span className="mono cc-dim">live · cc_events</span>}>
        <table className="cc-table cc-runs-tbl">
          <thead><tr><th>run_id</th><th>task</th><th>role</th><th>model</th><th>state</th><th style={{ textAlign: "right" }}>cost</th><th>tokens (in / out)</th><th>wall</th><th>started</th></tr></thead>
          <tbody>
            {rows.slice().reverse().map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td className="mono cc-dim">{r.task_id || "-"}</td>
                <td><RoleBadge role={r.role} /></td>
                <td className="mono cc-dim">{r.model || r.model_provider || "-"}</td>
                <td><StatusPill status={r.status} /></td>
                <td style={{ textAlign: "right" }}><CostCell micros={r.cost_usd_micros} /></td>
                <td><TokenCell value={r.input_tokens} /> · <TokenCell value={r.output_tokens} kind="output" /></td>
                <td style={{ minWidth: 120 }}><WallClockBar split={{ queue: r.queued_seconds, lease: r.leased_seconds, model: r.model_seconds, verify: r.verification_seconds, blocked: r.blocked_seconds }} /></td>
                <td className="mono cc-dim">{r.started_at || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function CostTimeline({ points }: { points: Array<{ x: number; cost: number; label: string }> }) {
  const w = 760;
  const h = 132;
  const pad = 20;
  const max = Math.max(...points.map((p) => p.cost), 1);
  const path = points.map((p, idx) => {
    const x = pad + (idx / Math.max(points.length - 1, 1)) * (w - 2 * pad);
    const y = h - pad - (p.cost / max) * (h - 2 * pad);
    return `${idx === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  const fillPath = `${path} L${w - pad} ${h - pad} L${pad} ${h - pad} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="cc-tl-svg">
      <defs><linearGradient id="ccCostTimeline" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--accent)" stopOpacity="0.32" /><stop offset="1" stopColor="var(--accent)" stopOpacity="0" /></linearGradient></defs>
      {[0.25, 0.5, 0.75].map((g) => <line key={g} x1={pad} x2={w - pad} y1={h - pad - g * (h - 2 * pad)} y2={h - pad - g * (h - 2 * pad)} stroke="rgba(255,255,255,0.05)" />)}
      <path d={fillPath} fill="url(#ccCostTimeline)" />
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.6" />
      <text x={pad} y={pad - 4} fill="rgba(110,119,130,0.85)" style={{ font: "500 9px var(--f-mono)" }}>{formatMicros(max, 2)}</text>
      <text x={pad} y={h - pad + 4} fill="rgba(110,119,130,0.85)" style={{ font: "500 9px var(--f-mono)" }}>$0.00</text>
    </svg>
  );
}

function cumulative(rows: RunRow[]) {
  let cost = 0;
  return rows.slice().sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || ""))).map((row, idx) => {
    cost += row.cost_usd_micros || 0;
    return { x: idx, cost, label: String(row.id) };
  });
}

function byRole(rows: RunRow[]) {
  const map = new Map<string, { role: string; wall: number }>();
  rows.forEach((row) => {
    const current = map.get(row.role) || { role: row.role, wall: 0 };
    current.wall += row.running_seconds || 0;
    map.set(row.role, current);
  });
  return [...map.values()].sort((a, b) => b.wall - a.wall);
}

function byRunModel(rows: RunRow[]): ModelUsageRow[] {
  const map = new Map<string, ModelUsageRow>();
  rows.forEach((row) => {
    const key = row.model || row.model_provider || "unknown";
    const current = map.get(key) || { profile_name: key, models: key, calls: 0, input_tokens: 0, output_tokens: 0, cost_usd_micros: 0 };
    current.calls += 1;
    current.input_tokens += row.input_tokens || 0;
    current.output_tokens += row.output_tokens || 0;
    current.cost_usd_micros += row.cost_usd_micros || 0;
    map.set(key, current);
  });
  return [...map.values()];
}

function pctOf(value: number, total: number) {
  if (!total) return 0;
  return Math.max(2, Math.round((value / total) * 100));
}

function costSource(workerCost: number, modelCost: number) {
  if (modelCost > workerCost) return "model_usage exceeds worker_runs";
  if (workerCost > 0) return "worker_runs cost";
  return "no persisted cost";
}

function wallSummary(total: { queue: number; lease: number; model: number; verify: number; blocked: number }) {
  const denom = total.queue + total.lease + total.model + total.verify + total.blocked;
  if (!denom) return "split unavailable";
  return `model ${Math.round(total.model / denom * 100)}% · verify ${Math.round(total.verify / denom * 100)}%`;
}
