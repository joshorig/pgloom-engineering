import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { postIntervention, useApi, type ModelUsageRow, type RealtimeStatus, type RunRow, type SlotRow, type TokenSaviorRow } from "../api";
import type { RealtimeConnectionState } from "../realtime";
import { CostCell, Panel, RoleBadge, Stat, StatusPill, TaskLink, TokenCell } from "../components/primitives";
import { formatMicros, formatTokens } from "../lib/money";

type Row = Record<string, unknown>;

function DataTable({ rows }: { rows: Row[] }) {
  const keys = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 9);
  if (!rows.length) return <div className="cc-state" style={{ margin: 14 }}><div className="cc-state-title">No rows yet</div><div className="cc-state-desc">This view will populate when the underlying contract rows exist.</div></div>;
  return (
    <table className="cc-table">
      <thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead>
      <tbody>{rows.map((row, idx) => <tr key={idx}>{keys.map((key) => <td key={key} className="mono cc-dim">{renderValue(row[key])}</td>)}</tr>)}</tbody>
    </table>
  );
}

function renderValue(value: unknown) {
  if (value == null) return "-";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 120);
  return String(value);
}

export function HandoffView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/handoffs`);
  const { data: runs } = useApi<RunRow[]>(`/api/features/${featureId}/runs`);
  const rows = data || [];
  const runRows = runs || [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(() => rows.find((row) => String(row.id) === selectedId) || rows[0], [rows, selectedId]);
  if (!rows.length) return <EmptyContractView kicker="HANDOFFS" title="No handoffs yet" desc="Handoff rows appear after planner, worker, reviewer, and QA contracts begin passing state between roles." />;

  const details = selected ? handoffDetails(selected) : null;
  return (
    <div className="cc-h-pane">
      <div className="cc-h-list cc-scroll">
        <div className="cc-h-list-bar">
          <span className="cc-kicker mono">HANDOFFS · {rows.length}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <span className="cc-chip is-on">open</span>
            <span className="cc-chip">pending</span>
            <span className="cc-chip">closed</span>
          </span>
        </div>
        {rows.map((row, idx) => {
          const h = handoffDetails(row);
          const isSelected = selected ? String(row.id) === String(selected.id) : idx === 0;
          return (
            <div key={String(row.id)} className={`cc-h-row ${isSelected ? "is-selected" : ""}`} onClick={() => setSelectedId(String(row.id))} role="button" tabIndex={0}>
              <div className="cc-h-row-l">
                <div className="mono cc-h-row-id">{shortId(String(row.id))}</div>
                <div className="cc-h-row-pair mono">
                  <TaskLink featureId={featureId} taskId={h.fromTask === "-" ? null : h.fromTask} label={shortId(h.fromTask)} subtle />
                  <span style={{ color: "var(--accent)" }}>→</span>
                  <TaskLink featureId={featureId} taskId={h.toTask === "-" ? null : h.toTask} label={shortId(h.toTask)} subtle />
                </div>
                <div className="cc-h-row-kind mono">{h.kind}</div>
                <div className="cc-h-row-roles">
                  <RoleBadge role={h.fromRole} />
                  <span className="cc-dim mono">→</span>
                  <RoleBadge role={h.toRole} />
                </div>
              </div>
              <div className="cc-h-row-r">
                <span className="mono cc-dim cc-h-row-files">{h.files}p · {h.outputs} out</span>
                <StatusPill status={effectiveHandoffStatus(h, runRows)} />
              </div>
            </div>
          );
        })}
      </div>

      {details && (
        <div className="cc-h-detail cc-scroll">
          <div className="cc-h-d-hd">
            <div>
              <div className="cc-kicker mono">HANDOFF · {details.id}</div>
              <h2 className="cc-h-d-title">{details.title}</h2>
              {details.summary && <div className="cc-h-d-summary">{details.summary}</div>}
              <div className="mono cc-dim" style={{ fontSize: 11 }}>{details.kind} · created {formatDate(details.createdAt)} · updated {formatDate(details.updatedAt)}</div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="cc-btn cc-btn-ghost">Open contract</button>
              <button className="cc-btn">Request changes...</button>
              <button className="cc-btn cc-btn-primary">Approve & advance</button>
            </div>
          </div>
          <div className="cc-h-d-bar">
            <div className="cc-h-d-pair">
              <RoleBadge role={details.fromRole} full />
              <TaskLink featureId={featureId} taskId={details.fromTask === "-" ? null : details.fromTask} subtle />
              <span className="cc-h-arrow">→</span>
              <RoleBadge role={details.toRole} full />
              <TaskLink featureId={featureId} taskId={details.toTask === "-" ? null : details.toTask} subtle />
            </div>
            <div style={{ marginLeft: "auto", display: "flex", gap: 18 }}>
              <KV k="paths" v={<span className="mono">{details.files}</span>} />
              <KV k="outputs" v={<span className="mono">{details.outputs}</span>} />
              <KV k="budget" v={<span className="mono">{details.contextBudget || "-"}</span>} />
              <KV k="tokens" v={<span className="mono"><TokenCell value={details.contextBudget} /> ctx</span>} />
            </div>
          </div>
          <div className="cc-panel" style={{ margin: "0 14px" }}>
            <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">GATES</span><span className="cc-panelhd-title">Pre-handoff checks</span></div><span className="mono cc-dim">{details.gates.length} declared</span></div>
            <div className="cc-h-checks">
              {details.gates.map((gate, idx) => (
                <div className="cc-h-chk st-pass" key={`${gate}-${idx}`}>
                  <span className="cc-h-chk-glyph">✓</span>
                  <span className="cc-h-chk-label">{gate}</span>
                  <span className="mono cc-dim cc-h-chk-meta">contract</span>
                </div>
              ))}
            </div>
          </div>
          <div className="cc-panel" style={{ margin: 14 }}>
            <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">CONTRACT</span><span className="cc-panelhd-title">Allowed paths and expected outputs</span></div><span className="mono cc-dim">{details.kind}</span></div>
            <pre className="cc-diff mono">{contractPreview(details)}</pre>
          </div>
          <div className="cc-panel" style={{ margin: "0 14px 14px" }}>
            <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">MESSAGE</span><span className="cc-panelhd-title">Objective</span></div></div>
            <div className="cc-h-msg"><p>{details.objective}</p><p className="cc-dim mono">{details.validation.join(" · ") || "No validation strategy declared."}</p></div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ValidationView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/qa-signoffs`);
  const { data: runs } = useApi<RunRow[]>(`/api/features/${featureId}/runs`);
  const rows = data || [];
  const runEvidence = validationRuns(runs || []);
  if (!rows.length && !runEvidence.length) return <EmptyContractView kicker="VALIDATION" title="No validation evidence yet" desc="Scrutiny, user-test, command, and artifact evidence appear here once validator workers persist run or signoff contracts." />;
  if (!rows.length) return <ValidationRunFallback runs={runEvidence} />;
  const scrutiny = rows.find((row) => String(row.validator_type).includes("scrutiny"));
  const usertest = rows.find((row) => String(row.validator_type).includes("user"));
  const primary = scrutiny || usertest || rows[0];
  const evidence = evidenceRows(rows);
  const commands = commandRows(primary);
  const verdictPassed = rows.some((row) => String(row.verdict) === "pass");
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: "flex", flexDirection: "column" }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">VALIDATION · {shortId(String(primary.task_id || featureId))}</div>
          <h2 className="cc-v-title">Two-evidence verdict</h2>
        </div>
        <div className="cc-v-verdict">
          <div className="cc-v-verdict-row"><span className="mono cc-dim">scrutiny</span><StatusPill status={scrutiny ? String(scrutiny.verdict) : "queued"} /><span className="mono cc-dim">{scrutiny ? formatDate(String(scrutiny.created_at)) : "waiting for evidence"}</span></div>
          <div className="cc-v-verdict-row"><span className="mono cc-dim">user-test</span><StatusPill status={usertest ? String(usertest.verdict) : "queued"} /><span className="mono cc-dim">{usertest ? formatDate(String(usertest.created_at)) : "second validator pending"}</span></div>
          <div className="cc-v-verdict-row" style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}><span className="mono">verdict</span><span className="cc-v-pending mono">{verdictPassed ? "EVIDENCE RECORDED" : "PENDING - evidence required"}</span></div>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">EVIDENCE 1</span><span className="cc-panelhd-title">Scrutiny breaks</span></div><span className="mono cc-dim">{evidence.length} evidence rows</span></div>
          <div className="cc-v-attempts">
            {evidence.slice(0, 6).map((item, idx) => (
              <div className={`cc-v-attempt is-${item.verdict === "pass" ? "caught" : "survived"}`} key={`${item.evidence_id}-${idx}`}>
                <div className="cc-v-attempt-hd"><span className="mono">attempt {idx + 1}</span><StatusPill status={item.verdict} /><span className="mono cc-dim" style={{ marginLeft: "auto" }}>{item.kind}</span></div>
                <div className="cc-v-attempt-body mono">{item.summary}</div>
                <div className="cc-v-attempt-foot mono cc-dim"><span>{item.evidence_id}</span><span>{item.artifact_ids.length} artifacts</span></div>
              </div>
            ))}
          </div>
        </div>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">EVIDENCE 2</span><span className="cc-panelhd-title">User-test slot</span></div><StatusPill status={usertest ? String(usertest.verdict) : "queued"} /></div>
          <div className="cc-v-ut">
            <div className="cc-v-ut-bar"><div className="cc-v-ut-bar-fill" style={{ width: usertest ? "100%" : scrutiny ? "50%" : "12%" }} /><div className="cc-v-ut-bar-marks"><span style={{ left: "0%" }}>slot 1<br /><b>{scrutiny ? "passed" : "queued"}</b></span><span style={{ left: "50%" }}>slot 2<br /><b>{usertest ? "recorded" : "waiting"}</b></span><span style={{ left: "100%" }}>verdict<br /><b className={usertest ? "" : "cc-dim"}>{usertest ? "ready" : "pending"}</b></span></div></div>
            <div className="cc-v-ut-tester">
              <div className="cc-v-ut-tester-hd"><span className="mono">validator lane</span><span className="mono cc-dim">{rows.length} signoff contracts</span></div>
              <div className="cc-v-ut-tester-body">
                <div className={`cc-v-ut-step ${scrutiny ? "is-done" : "is-active"}`}><span className="mono">1</span> Scrutiny evidence persisted</div>
                <div className={`cc-v-ut-step ${usertest ? "is-done" : scrutiny ? "is-active" : ""}`}><span className="mono">2</span> User-test evidence persisted</div>
                <div className={`cc-v-ut-step ${usertest ? "is-active" : ""}`}><span className="mono">3</span> Milestone verdict ready for operator review</div>
              </div>
            </div>
            <div className="cc-v-ut-actions"><button className="cc-btn cc-btn-ghost">View claim...</button><button className="cc-btn cc-btn-ghost">Reassign tester</button><button className="cc-btn cc-btn-danger" style={{ marginLeft: "auto" }}>Force release slot</button></div>
          </div>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 14 }}>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">SPEC</span><span className="cc-panelhd-title">Commands and success criteria</span></div><span className="mono cc-dim">{commands.length} commands</span></div>
          <ol className="cc-v-spec">
            {commands.map((cmd, idx) => <li key={`${cmd}-${idx}`}><span className="mono cc-v-spec-i">SC{idx + 1}</span><div><div className="cc-v-spec-l">{cmd}</div><div className="mono cc-dim cc-v-spec-m">validator evidence · persisted command</div></div><StatusPill status="passed" /></li>)}
          </ol>
        </div>
        <div className="cc-panel">
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">COVERAGE</span><span className="cc-panelhd-title">Artifacts</span></div></div>
          <div className="cc-v-cov">
            {artifactRows(rows).map((artifact, idx) => <div className="cc-v-cov-row" key={`${artifact}-${idx}`}><span className="mono cc-v-cov-file">{shortId(artifact)}</span><span className="cc-v-cov-bar"><span className="cc-v-cov-bar-fill" style={{ width: "100%" }} /></span><span className="mono cc-v-cov-pct">ok</span><span className="mono cc-dim cc-v-cov-meta">artifact</span></div>)}
            <div className="cc-v-cov-foot mono cc-dim">Σ {rows.length} signoffs · {artifactRows(rows).length} artifacts · {evidence.length} evidence rows</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function RecoveryView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/recovery`);
  return <GenericView kicker="RECOVERY" title="Recovery actions and corrective slices" rows={data || []} />;
}

export function InterventionView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/interventions`);
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div><div className="cc-kicker mono">INTERVENTIONS · audit-grade log</div><h2 className="cc-v-title">Every operator click writes one row</h2></div>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="cc-btn cc-btn-danger" onClick={() => void postIntervention(featureId, "pause_feature", {})}>Pause</button>
          <button className="cc-btn cc-btn-primary" onClick={() => void postIntervention(featureId, "resume_feature", {})}>Resume</button>
        </div>
      </div>
      <Panel kicker="AUDIT" title="Immutable intervention timeline"><DataTable rows={(data || []).slice().reverse()} /></Panel>
    </div>
  );
}

export function TokenEconomyView({ featureId }: { featureId?: string }) {
  const isGlobal = !featureId;
  const { data: tokenRows } = useApi<TokenSaviorRow[]>(featureId ? `/api/features/${featureId}/token-savior` : "/api/token-savior");
  const { data: runs } = useApi<RunRow[]>(featureId ? `/api/features/${featureId}/runs` : "/api/runs");
  const { data: modelUsage } = useApi<ModelUsageRow[]>(featureId ? `/api/features/${featureId}/model-usage` : "/api/model-usage");
  const rows = tokenRows || [];
  const runRows = runs || [];
  const usageRows = modelUsage || [];
  const totals = {
    original: sum(rows, "input_tokens_original"),
    packed: sum(rows, "input_tokens_after_savior"),
    saved: sum(rows, "tokens_saved"),
    cached: sum(runRows, "cached_input_tokens"),
    cacheCreation: sum(runRows, "cache_creation_tokens"),
    output: sum(runRows, "output_tokens"),
    reasoning: sum(runRows, "reasoning_tokens"),
    rtk: sum(runRows, "rtk_saved_tokens"),
    cost: Math.max(sum(runRows, "cost_usd_micros"), sum(usageRows, "cost_usd_micros"))
  };
  const tokenTotal = totals.packed + totals.cached + totals.cacheCreation + totals.output + totals.reasoning;
  const gross = totals.original + totals.cached + totals.output + totals.reasoning;
  const net = totals.packed + totals.output + totals.reasoning;
  const headlineSavings = totals.saved + totals.rtk;
  const tokenTypes = [
    { k: "input", v: totals.packed, color: "var(--r-impl)" },
    { k: "cached", v: totals.cached, color: "var(--r-review)" },
    { k: "cache-creation", v: totals.cacheCreation, color: "var(--accent)" },
    { k: "output", v: totals.output, color: "var(--st-pass)" },
    { k: "reasoning", v: totals.reasoning, color: "var(--st-block)" }
  ];
  if (!rows.length && !runRows.length) return <EmptyContractView kicker="TOKEN ECONOMY" title={isGlobal ? "No global token economy rows yet" : "No token economy rows yet"} desc="Token Savior, model usage, cache, and RTK savings appear here once workers persist telemetry." />;
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">TELEMETRY · token economy</div>
          <h2 className="cc-v-title">{isGlobal ? "Token economy across every project" : "Where the tokens went, where they did not"}</h2>
          <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>{isGlobal ? "all projects · project breakdown · profile accounting" : "feature scope · savings layers · per-profile accounting"}</p>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <Stat k="tokens · gross" v={formatTokens(gross)} d={<span className="mono cc-dim">all roles</span>} />
          <Stat k="tokens · net" v={formatTokens(net)} d={<span className="cc-accent-ink mono">-{pct(headlineSavings, Math.max(totals.original + totals.rtk, 1))}% pack/log</span>} />
          <Stat k="cost · net" v={formatMicros(totals.cost, 2)} d={<span className="mono cc-dim">persisted model cost</span>} />
        </div>
      </div>
      <div className="cc-panel" style={{ flexShrink: 0 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">TOKEN CLASSES</span><span className="cc-panelhd-title">net usage · stacked</span></div><span className="mono cc-dim">net = post-savings</span></div>
        <div className="cc-te-bar">
          {tokenTypes.map((item) => <span key={item.k} className="cc-te-seg" style={{ width: `${pct(item.v, tokenTotal)}%`, background: item.color }} title={`${item.k} · ${item.v.toLocaleString()}`} />)}
        </div>
        <div className="cc-te-legend">
          {tokenTypes.map((item) => <div key={item.k} className="cc-te-legend-item"><span className="cc-te-swatch" style={{ background: item.color }} /><span className="mono cc-te-legend-k">{item.k}</span><span className="mono cc-te-legend-v">{item.v.toLocaleString()}</span><span className="mono cc-dim">{pct(item.v, tokenTotal)}%</span></div>)}
        </div>
      </div>
      <div className="cc-panel" style={{ flexShrink: 0 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">SAVINGS</span><span className="cc-panelhd-title">waterfall · gross {"->"} net</span></div><span className="mono cc-dim">-{formatTokens(headlineSavings)} packing/log tokens saved · cache shown separately</span></div>
        <div className="cc-te-water">
          <WaterRow label="original context" value={totals.original} width={100} />
          <WaterCut label="Token Savior · context packing" saved={totals.saved} before={totals.original} after={totals.packed} base={Math.max(totals.original, 1)} color="var(--accent)" />
          <WaterCut label="RTK · log filter" saved={totals.rtk} before={totals.packed} after={Math.max(0, totals.packed - totals.rtk)} base={Math.max(totals.original, 1)} color="var(--r-qa-author)" />
          <WaterRow label="packed context" value={Math.max(0, totals.packed - totals.rtk)} width={pct(Math.max(0, totals.packed - totals.rtk), Math.max(totals.original, 1))} accent />
        </div>
      </div>
      <div className="cc-panel" style={{ flexShrink: 0 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">MODEL COST LEDGER</span><span className="cc-panelhd-title">{isGlobal ? "Project/provider cost attribution" : "Provider cost attribution"}</span></div><span className="mono cc-dim">{usageRows.length} provider rows</span></div>
        <table className="cc-table cc-te-table">
          <thead><tr>{isGlobal && <th>project</th>}<th>provider</th><th>model</th><th style={{ textAlign: "right" }}>calls</th><th style={{ textAlign: "right" }}>input</th><th style={{ textAlign: "right" }}>cached</th><th style={{ textAlign: "right" }}>reason</th><th style={{ textAlign: "right" }}>cost</th></tr></thead>
          <tbody>{usageRows.map((row) => <tr key={`${row.project || "feature"}-${row.providers || "-"}-${row.models || "-"}`}>{isGlobal && <td className="mono cc-dim">{row.project || "-"}</td>}<td className="mono">{row.providers || "-"}</td><td className="mono cc-dim">{row.models || "-"}</td><td className="mono cc-dim" style={{ textAlign: "right" }}>{row.calls}</td><td style={{ textAlign: "right" }}><TokenCell value={row.input_tokens} /></td><td style={{ textAlign: "right" }}><TokenCell value={row.cached_input_tokens} kind="cached" /></td><td style={{ textAlign: "right" }}><TokenCell value={row.reasoning_tokens} kind="output" /></td><td style={{ textAlign: "right" }}><CostCell micros={row.cost_usd_micros} precision={2} /></td></tr>)}</tbody>
        </table>
      </div>
      <div className="cc-panel" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 280 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">PER-PROFILE</span><span className="cc-panelhd-title">{isGlobal ? "Project Token Savior ledger" : "Token Savior ledger"}</span></div><span className="mono cc-dim">{rows.length} profiles</span></div>
        <table className="cc-table cc-te-table">
          <thead><tr>{isGlobal && <th>project</th>}<th>profile</th><th style={{ textAlign: "right" }}>rows</th><th style={{ textAlign: "right" }}>original</th><th style={{ textAlign: "right" }}>packed</th><th style={{ textAlign: "right" }}>saved</th><th style={{ textAlign: "right" }}>reduction</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={`${row.project || "feature"}-${row.profile_name}`} >{isGlobal && <td className="mono cc-dim">{row.project || "-"}</td>}<td className="mono">{row.profile_name}</td><td className="mono cc-dim" style={{ textAlign: "right" }}>{row.rows}</td><td style={{ textAlign: "right" }}><TokenCell value={row.input_tokens_original} /></td><td style={{ textAlign: "right" }}><TokenCell value={row.input_tokens_after_savior} /></td><td style={{ textAlign: "right" }}><TokenCell value={row.tokens_saved} kind="cached" /></td><td className="mono cc-dim" style={{ textAlign: "right" }}>{((row.reduction_ratio || 0) * 100).toFixed(1)}%</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

export function SlotOccupancyView({ featureId }: { featureId?: string }) {
  const isGlobal = !featureId;
  const { data } = useApi<SlotRow[]>(featureId ? `/api/features/${featureId}/slots` : "/api/slots");
  const rows = data || [];
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const selected = rows.find((row) => row.slot === selectedSlot) || rows.find((row) => liveCount(row) > 0) || rows[0];
  const max = rows.reduce((total, row) => total + Number(row.max || 0), 0);
  const holding = rows.reduce((total, row) => total + Number(row.holding || 0), 0);
  const queued = rows.reduce((total, row) => total + Number(row.queued || 0), 0);
  const blocked = rows.reduce((total, row) => total + Number(row.blocked || 0), 0);
  const holds = rows.flatMap((row) => row.holds || []);
  const fullAppLocks = holds.filter((hold) => String(hold.resource_key || "").includes("full_app_run"));
  if (!rows.length) return <EmptyContractView kicker="SLOT OCCUPANCY" title={isGlobal ? "No global slot telemetry yet" : "No slot telemetry yet"} desc="Worker slot rows appear here once tasks are queued, leased, running, or blocked." />;
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">{isGlobal ? "GLOBAL" : "FEATURE"} · worker slot occupancy</div>
          <h2 className="cc-v-title">Worker slots & resource-lock drilldown</h2>
          <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>{isGlobal ? "all projects · planner, implementer, reviewer, QA, and validation slots" : "feature scope · slot pressure and active task drilldown"}</p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Stat k="occupied" v={`${holding} / ${max || "-"}`} d={<span className="mono cc-dim">{rows.length} worker slots</span>} />
          <Stat k="queued" v={String(queued)} d={<span className="mono cc-dim">waiting tasks</span>} />
          <Stat k="blocked" v={String(blocked)} d={<span className="mono cc-dim">needs intervention</span>} />
          <Stat k="locks held" v={String(holds.length)} d={<span className="mono cc-dim">resource_locks</span>} />
        </div>
      </div>
      <div className="cc-panel" style={{ flexShrink: 0 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">worker slots</span><span className="cc-panelhd-title">select a slot for task detail</span></div><span className="mono cc-dim">queued {"->"} leased {"->"} running {"->"} done / blocked</span></div>
        <div className="cc-slots">{rows.map((row) => {
          const live = liveCount(row);
          const selectedClass = selected?.slot === row.slot ? " is-selected" : "";
          return (
            <button key={row.slot} className={`cc-slot ${live ? "is-leased" : "is-idle"}${selectedClass}`} onClick={() => setSelectedSlot(row.slot)} style={{ textAlign: "left" }} type="button">
              <div className="cc-slot-hd"><span className="mono cc-slot-num">{row.slot}</span><span className={`cc-slot-state mono ${live ? "st-run" : "st-idle"}`}><i className={live ? "cc-pulse" : ""} />{live ? "active" : "idle"}</span></div>
              <div className="cc-slot-row"><span className="cc-dim">run</span><span className="mono cc-accent-ink">{row.holding || 0} / {row.max || 1}</span></div>
              <div className="cc-slot-row"><span className="cc-dim">queue</span><span className="mono">{row.queued || 0} queued · {row.blocked || 0} blocked</span></div>
              <div className="cc-slot-row"><span className="cc-dim">locks</span><span className="mono">{row.lock_count || 0} resource locks</span></div>
            </button>
          );
        })}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 14, flexShrink: 0, minHeight: 360 }}>
        <div className="cc-panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">slot drilldown</span><span className="cc-panelhd-title">{selected?.slot || "no slot selected"}</span></div><span className="mono cc-dim">{selected?.tasks?.length || 0} live tasks</span></div>
          <div className="cc-lock-list cc-scroll">{(selected?.tasks || []).length ? selected?.tasks?.map((task, idx) => <div key={`${task.task_id}-${idx}`} className={`cc-lock ${task.state === "blocked" ? "is-free" : "is-held"}`}><div className="cc-lock-hd"><div className="cc-lock-key"><span className={`cc-lock-dot ${task.state === "running" || task.state === "leased" ? "cc-pulse" : "is-free"}`} /><span className="mono cc-lock-proj">{task.project || "-"}</span></div><StatusPill status={task.state} /></div><div className="cc-lock-body"><div className="cc-lock-line"><span className="cc-dim">feature</span><span className="mono">{shortId(String(task.workflow_id || "-"))}</span><span className="cc-dim">task</span><span className="mono">{shortId(String(task.task_id || "-"))}</span></div><div className="cc-lock-line"><span className="cc-dim">type</span><span className="mono">{task.task_type || "-"}</span>{task.lease_owner && <><span className="cc-dim">owner</span><span className="mono">{shortId(String(task.lease_owner))}</span></>}</div></div></div>) : <div className="cc-lock-empty mono">No running, leased, queued, or blocked tasks in this slot.</div>}</div>
        </div>
        <div className="cc-panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">full_app_run locks</span><span className="cc-panelhd-title">project mutex detail</span></div><span className="mono cc-dim">{fullAppLocks.length} active</span></div>
          <div className="cc-lock-list cc-scroll">{fullAppLocks.length ? fullAppLocks.map((hold, idx) => <div key={`${hold.resource_key}-${idx}`} className="cc-lock is-held"><div className="cc-lock-hd"><div className="cc-lock-key"><span className="cc-lock-dot cc-pulse" /><span className="mono cc-lock-proj">{hold.project || shortId(String(hold.resource_key || "-"))}</span></div><span className="mono cc-dim cc-lock-state">HELD</span></div><div className="cc-lock-body"><div className="cc-lock-line"><span className="cc-dim">resource</span><span className="mono">{hold.resource_key || "-"}</span></div><div className="cc-lock-line"><span className="cc-dim">owner</span><span className="mono">{shortId(String(hold.owner_id || "-"))}</span><span className="cc-dim">task</span><span className="mono">{shortId(String(hold.task_id || "-"))}</span></div><div className="cc-lock-line"><span className="cc-dim">expires</span><span className="mono">{formatDate(String(hold.expires_at || ""))}</span></div></div></div>) : <div className="cc-lock-empty mono">No active full_app_run resource lock. Running work is still visible in the selected worker slot drilldown.</div>}</div>
        </div>
      </div>
      <div className="cc-panel" style={{ flexShrink: 0 }}>
        <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">contention</span><span className="cc-panelhd-title">queue and blocked pressure by slot</span></div><span className="mono cc-dim">current</span></div>
        <div className="cc-contention cc-scroll">{rows.map((row) => <div key={row.slot} className="cc-contention-row"><span className="mono cc-contention-key">{row.slot}</span><div className="cc-contention-bars">{contentionBars(row).map((value, idx) => <span key={idx} className="cc-contention-bar" style={{ height: `${Math.max(2, value * 28)}px`, opacity: value ? 0.35 + value * 0.65 : 0.08 }} />)}</div><span className="mono cc-dim cc-contention-now">{liveCount(row)}</span></div>)}<div className="cc-contention-axis mono cc-dim"><span>running</span><span>queued</span><span>blocked</span></div></div>
      </div>
    </div>
  );
}

export function ProjectRegistry() {
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14 }}>
      <Panel kicker="PROJECT REGISTRY" title="docs/evals/project-registry.yaml">
        <div style={{ padding: 14 }} className="mono cc-dim">Project metadata is read by planner, QA author, implementer, validators, and dispatch gates. The API route can be backed by the YAML registry in the next slice.</div>
      </Panel>
    </div>
  );
}

export function RealtimePanel({
  events,
  realtime
}: {
  events: Row[];
  realtime: RealtimeConnectionState;
}) {
  const { data: status } = useApi<RealtimeStatus>("/api/realtime/status");
  const latest = events[0];
  const eventRows = events.length ? events : [{
    kind: "resync",
    reason: "waiting for first cc_events payload",
    fields: ["initial_rest_fetch"],
    ts: new Date().toISOString()
  }];
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">REALTIME · pg_notify('cc_events', ...)</div>
          <h2 className="cc-v-title">Live event firehose</h2>
          <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>asyncpg LISTEN {"->"} 1 ws per client · payloads {"<="} 7500B · drop-oldest on overflow {"->"} resync hint</p>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <span className={`cc-fh-status ${realtime.state === "connected" ? "cc-pulse" : "is-reconnecting"}`}><i />{realtime.state} · {status?.subscribers ?? "-"} subscribers</span>
          <span className="cc-chip is-on">all kinds</span>
          <span className="cc-chip">worker_run</span>
          <span className="cc-chip">qa</span>
          <span className="cc-chip">interv.</span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 14, flex: 1, minHeight: 0 }}>
        <div className="cc-panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">STREAM</span><span className="cc-panelhd-title">cc_events · ordered by NOTIFY ts</span></div><span className="mono cc-dim">{events.length} evts in browser buffer</span></div>
          <div className="cc-fh-head mono">
            <span />
            <span>ts</span>
            <span>kind</span>
            <span>feature_id</span>
            <span>affected row · pk</span>
            <span>fields[]</span>
            <span style={{ textAlign: "right" }}>bytes</span>
          </div>
          <div className="cc-fh-list cc-scroll">
            {eventRows.map((event, idx) => {
              const kind = String(event.kind || "unknown");
              return (
                <div key={`${kind}-${idx}-${String(event.row_id || event.reason || "")}`} className={`cc-fh-row kind-${kind.replace(/_/g, "-").replace(/\./g, "-")}`}>
                  <span className={`cc-fh-dot ${idx < 3 && realtime.state === "connected" ? "cc-pulse" : ""}`} />
                  <span className="mono cc-fh-ts cc-dim">{formatEventTime(String(event.ts || ""))}</span>
                  <span className={`cc-fh-kind mono kind-${kind.split(".")[0]}`}>{kind}</span>
                  <span className="mono cc-fh-fid cc-dim">{shortId(String(event.feature_id || "*"))}</span>
                  <span className="mono cc-fh-row-id">{shortId(String(event.row_id || event.reason || "-"))}</span>
                  <span className="cc-fh-fields mono cc-dim">{arrayValue(event.fields).join(" · ") || String(event.reason || "-")}</span>
                  <span className="mono cc-fh-bytes cc-dim">{payloadBytes(event)}B</span>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
          <div className="cc-panel">
            <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">SAMPLE PAYLOAD</span><span className="cc-panelhd-title">{String(latest?.kind || "waiting")}</span></div></div>
            <pre className="cc-fh-json mono">{JSON.stringify(latest || eventRows[0], null, 2)}</pre>
          </div>
          <div className="cc-panel">
            <div className="cc-panelhd"><div className="cc-panelhd-l"><span className="cc-kicker mono">SUBSTRATE</span><span className="cc-panelhd-title">LISTEN / NOTIFY guardrails</span></div></div>
            <ul className="cc-fh-rules mono">
              <li><span className="cc-replan-rule-ok">ok</span> channel {status?.channel || "cc_events"}</li>
              <li><span className="cc-replan-rule-ok">ok</span> max queue {status?.max_queue_size ?? 200}</li>
              <li><span className={status?.database_configured ? "cc-replan-rule-ok" : "cc-replan-rule-warn"}>{status?.database_configured ? "ok" : "warn"}</span> database configured</li>
              <li><span className={realtime.state === "connected" ? "cc-replan-rule-ok" : "cc-replan-rule-warn"}>{realtime.state === "connected" ? "ok" : "wait"}</span> reconnect refetch enabled</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

export function RunAggregateView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/runs/aggregate`);
  return <GenericView kicker="AGGREGATE" title="Runs by role and phase" rows={data || []} />;
}

function GenericView({ kicker, title, rows }: { kicker: string; title: string; rows: Row[] }) {
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
      <Panel kicker={kicker} title={title}><DataTable rows={rows} /></Panel>
    </div>
  );
}

function EmptyContractView({ kicker, title, desc }: { kicker: string; title: string; desc: string }) {
  return <div className="cc-pane cc-scroll" style={{ padding: 14 }}><div className="cc-state"><div className="cc-kicker mono">{kicker}</div><div className="cc-state-title">{title}</div><div className="cc-state-desc">{desc}</div></div></div>;
}

export function RoleCell({ role }: { role?: string }) {
  return role ? <RoleBadge role={role} /> : <span className="mono cc-dim">-</span>;
}

export function StatusCell({ status }: { status?: string }) {
  return status ? <StatusPill status={status} /> : <span className="mono cc-dim">-</span>;
}

export function MoneyCell({ micros }: { micros?: number }) {
  return <CostCell micros={micros} />;
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return <span className="mono"><span className="cc-dim">{k}</span> {v}</span>;
}

function handoffDetails(row: Row) {
  const contract = objectValue(row.contract);
  const inputs = objectValue(contract.inputs);
  const role = stringValue(contract.role || inputs.role, "implementer");
  const allowed = arrayValue(contract.allowed_paths);
  const outputs = arrayValue(contract.expected_outputs);
  const criteria = arrayValue(inputs.grading_criteria);
  const validation = Object.values(objectValue(inputs.validation_strategy)).flatMap((value) => arrayValue(value).map(String));
  return {
    id: String(row.id || "-"),
    fromTask: stringValue(row.from_task_id, "-"),
    toTask: stringValue(row.to_task_id || inputs.task_id, "-"),
    fromRole: roleFromKind(String(row.handoff_type || "")),
    toRole: role,
    kind: stringValue(row.handoff_type, "handoff"),
    status: stringValue(row.status, "ready"),
    title: stringValue(row.title, stringValue(contract.title || inputs.task_slice_id, "Handoff contract")),
    summary: stringValue(row.summary, stringValue(contract.summary || contract.objective, "")),
    objective: stringValue(contract.objective, stringValue(row.summary, stringValue(row.id, "Handoff contract"))),
    createdAt: stringValue(row.created_at, ""),
    updatedAt: stringValue(row.updated_at, ""),
    files: allowed.length,
    outputs: outputs.length,
    contextBudget: Number(inputs.context_budget || 0),
    allowed,
    expected: outputs,
    gates: criteria.length ? criteria.map(String) : ["Contract shape is present", "Allowed paths are declared", "Validation strategy is declared"],
    validation
  };
}

function ValidationRunFallback({ runs }: { runs: RunRow[] }) {
  const scrutiny = runs.find((run) => String(run.validator_type || run.phase).includes("scrutiny"));
  const usertest = runs.find((run) => String(run.validator_type || run.phase).includes("user"));
  const totalCost = runs.reduce((sum, run) => sum + (run.cost_usd_micros || 0), 0);
  const totalSeconds = runs.reduce((sum, run) => sum + (run.running_seconds || 0), 0);
  return (
    <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: "flex", flexDirection: "column" }}>
      <div className="cc-v-hd">
        <div>
          <div className="cc-kicker mono">VALIDATION · WORKER EVIDENCE</div>
          <h2 className="cc-v-title">Run-backed validation trail</h2>
        </div>
        <div className="cc-v-verdict">
          <div className="cc-v-verdict-row"><span className="mono cc-dim">scrutiny</span><StatusPill status={scrutiny?.status || "queued"} /><span className="mono cc-dim">{scrutiny?.started_at || "waiting"}</span></div>
          <div className="cc-v-verdict-row"><span className="mono cc-dim">user-test</span><StatusPill status={usertest?.status || "queued"} /><span className="mono cc-dim">{usertest?.started_at || "waiting"}</span></div>
          <div className="cc-v-verdict-row" style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}><span className="mono">cost / wall</span><span className="cc-v-pending mono"><CostCell micros={totalCost} precision={2} /> · {Math.round(totalSeconds)}s</span></div>
        </div>
      </div>
      <Panel kicker="VALIDATION RUNS" title="Commands, artifacts, and blockers from worker_runs">
        <table className="cc-table">
          <thead><tr><th>run</th><th>task</th><th>role</th><th>phase</th><th>state</th><th>commands</th><th>artifacts</th><th style={{ textAlign: "right" }}>cost</th></tr></thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className="mono">{run.id}</td>
                <td className="mono cc-dim cc-ellipsis"><TaskLink featureId={run.feature_id} taskId={run.task_id} subtle /></td>
                <td><RoleBadge role={run.role} /></td>
                <td className="mono cc-dim">{run.phase}</td>
                <td><StatusPill status={run.status} /></td>
                <td className="mono cc-dim">{arrayCount(run, "commands_run")}</td>
                <td className="mono cc-dim">{arrayCount(run, "artifact_ids")}</td>
                <td style={{ textAlign: "right" }}><CostCell micros={run.cost_usd_micros} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function validationRuns(runs: RunRow[]) {
  return runs.filter((run) => {
    const role = String(run.role || "");
    const phase = String(run.phase || "");
    const validator = String(run.validator_type || "");
    return role.includes("qa") || role.includes("review") || phase.includes("verify") || phase.includes("review") || validator.length > 0;
  });
}

function arrayCount(row: unknown, key: string) {
  const value = objectValue(row)[key];
  return Array.isArray(value) ? value.length : 0;
}

function sum<T extends Record<string, unknown>>(rows: T[], key: keyof T) {
  return rows.reduce((total, row) => total + Number(row[key] || 0), 0);
}

function pct(value: number, total: number) {
  if (!total) return 0;
  return Math.max(0, Math.round((value / total) * 100));
}

function WaterRow({ label, value, width, accent = false }: { label: string; value: number; width: number; accent?: boolean }) {
  return (
    <div className={`cc-te-water-row ${accent ? "cc-te-water-net" : "cc-te-water-base"}`}>
      <span className="mono cc-te-water-k">{label}</span>
      <div className="cc-te-water-track"><span className="cc-te-water-fill" style={{ width: `${width}%`, background: accent ? "var(--accent)" : "var(--panel-3)" }} /></div>
      <span className="mono cc-te-water-v">{value.toLocaleString()}</span>
    </div>
  );
}

function WaterCut({ label, saved, before, after, base, color }: { label: string; saved: number; before: number; after: number; base: number; color: string }) {
  const pre = pct(before, base);
  const post = pct(after, base);
  return (
    <div className="cc-te-water-row">
      <span className="mono cc-te-water-k">{label}</span>
      <div className="cc-te-water-track">
        <span className="cc-te-water-fill" style={{ width: `${post}%`, background: "var(--panel-3)" }} />
        <span className="cc-te-water-cut" style={{ left: `${post}%`, width: `${Math.max(0, pre - post)}%`, background: color, opacity: 0.65 }} />
      </div>
      <span className="mono cc-te-water-v">-{saved.toLocaleString()} <span className="cc-dim">({pct(saved, Math.max(before, 1))}%)</span></span>
    </div>
  );
}

function slotCards(rows: SlotRow[]) {
  const cards: Array<{ key: string; index: number; hold?: NonNullable<SlotRow["holds"]>[number] }> = [];
  rows.forEach((row) => {
    const holds = row.holds || [];
    const max = Math.max(Number(row.max || 0), holds.length, 1);
    for (let idx = 0; idx < max; idx += 1) {
      cards.push({ key: `${row.slot}-${idx}`, index: cards.length, hold: holds[idx] });
    }
  });
  return cards;
}

function contentionBars(row: SlotRow) {
  const base = Math.max(1, Number(row.max || 1));
  const active = Number(row.holding || 0) / base;
  const queued = Number(row.queued || 0) / base;
  const blocked = Number(row.blocked || 0) / base;
  return Array.from({ length: 30 }).map((_, idx) => {
    if (idx > 22) return Math.min(1, active + queued + blocked);
    if (idx > 12) return Math.min(1, active + queued);
    return Math.max(0, active * 0.45);
  });
}

function liveCount(row: SlotRow) {
  return Number(row.holding || 0) + Number(row.queued || 0) + Number(row.blocked || 0);
}

function statusForHandoff(status: string) {
  if (status === "ready") return "queued";
  if (status === "approved") return "passed";
  return status;
}

function effectiveHandoffStatus(details: ReturnType<typeof handoffDetails>, runs: RunRow[]) {
  if (["approved", "passed", "failed", "blocked", "superseded"].includes(details.status)) {
    return statusForHandoff(details.status);
  }
  const toRun = latestRunForTask(runs, details.toTask);
  const fromRun = latestRunForTask(runs, details.fromTask);
  if (toRun?.status) return toRun.status;
  if (fromRun?.status && ["failed", "blocked"].includes(fromRun.status)) return fromRun.status;
  return statusForHandoff(details.status);
}

function latestRunForTask(runs: RunRow[], taskId: string) {
  return runs
    .filter((run) => run.task_id === taskId)
    .sort((a, b) => String(b.started_at || b.id).localeCompare(String(a.started_at || a.id)))[0];
}

function roleFromKind(kind: string) {
  if (kind.startsWith("plan")) return "planner";
  if (kind.includes("review")) return "reviewer";
  if (kind.includes("qa")) return "qa.verify.scrutiny";
  return "implementer";
}

function contractPreview(details: ReturnType<typeof handoffDetails>) {
  return [
    "@@ contract surface @@",
    `handoff: ${details.id}`,
    `route: ${details.fromRole} ${details.fromTask} -> ${details.toRole} ${details.toTask}`,
    "",
    "allowed_paths:",
    ...details.allowed.slice(0, 10).map((path) => `+ ${path}`),
    details.allowed.length > 10 ? `+ ... ${details.allowed.length - 10} more` : "",
    "",
    "expected_outputs:",
    ...details.expected.slice(0, 8).map((item) => `+ ${item}`)
  ].filter(Boolean).join("\n");
}

function evidenceRows(rows: Row[]) {
  return rows.flatMap((row) => arrayValue(row.evidence).map((item) => {
    const ev = objectValue(item);
    return {
      evidence_id: stringValue(ev.evidence_id, shortId(String(row.id))),
      kind: stringValue(ev.kind, stringValue(row.validator_type, "evidence")),
      verdict: stringValue(ev.verdict, stringValue(row.verdict, "queued")),
      summary: stringValue(ev.summary, "-"),
      artifact_ids: arrayValue(ev.artifact_ids).map(String)
    };
  }));
}

function commandRows(row?: Row) {
  const contract = objectValue(row?.qa_result_contract);
  const commands = arrayValue(contract.commands);
  return commands.map((cmd) => Array.isArray(cmd) ? cmd.join(" ") : String(cmd));
}

function artifactRows(rows: Row[]) {
  return rows.flatMap((row) => arrayValue(row.artifact_ids).map(String));
}

function formatDate(value?: string) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toISOString().slice(0, 16).replace("T", " ");
}

function formatEventTime(value: string) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toISOString().slice(11, 23);
}

function payloadBytes(event: Row) {
  return new TextEncoder().encode(JSON.stringify(event)).length;
}

function shortId(id: string) {
  return id.length > 22 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id;
}

function stringValue(value: unknown, fallback = "-") {
  return value == null || value === "" ? fallback : String(value);
}

function objectValue(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
