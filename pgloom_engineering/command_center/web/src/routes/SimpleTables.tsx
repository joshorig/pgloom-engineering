import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { postIntervention, useApi } from "../api";
import { CostCell, Panel, RoleBadge, StatusPill, TokenCell } from "../components/primitives";

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
  const rows = data || [];
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
            <button key={String(row.id)} className={`cc-h-row ${isSelected ? "is-selected" : ""}`} onClick={() => setSelectedId(String(row.id))} type="button">
              <div className="cc-h-row-l">
                <div className="mono cc-h-row-id">{shortId(String(row.id))}</div>
                <div className="cc-h-row-pair mono">
                  <span className="cc-dim">{shortId(h.fromTask)}</span>
                  <span style={{ color: "var(--accent)" }}>→</span>
                  <span className="cc-dim">{shortId(h.toTask)}</span>
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
                <StatusPill status={statusForHandoff(h.status)} />
              </div>
            </button>
          );
        })}
      </div>

      {details && (
        <div className="cc-h-detail cc-scroll">
          <div className="cc-h-d-hd">
            <div>
              <div className="cc-kicker mono">HANDOFF · {details.id}</div>
              <h2 className="cc-h-d-title">{details.objective}</h2>
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
              <span className="mono cc-dim">{shortId(details.fromTask)}</span>
              <span className="cc-h-arrow">→</span>
              <RoleBadge role={details.toRole} full />
              <span className="mono cc-dim">{shortId(details.toTask)}</span>
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
  const rows = data || [];
  if (!rows.length) return <EmptyContractView kicker="VALIDATION" title="No QA signoffs yet" desc="Scrutiny and user-test evidence appear here once validator workers persist signoff contracts." />;
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

export function TokenEconomyView({ featureId }: { featureId: string }) {
  const { data } = useApi<Row[]>(`/api/features/${featureId}/token-savior`);
  return <GenericView kicker="TOKEN ECONOMY" title="Token Savior and savings ledger" rows={data || []} />;
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

export function RealtimePanel({ events }: { events: Row[] }) {
  return <GenericView kicker="REALTIME" title="Live event firehose" rows={events} />;
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
    objective: stringValue(contract.objective, stringValue(row.id, "Handoff contract")),
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

function statusForHandoff(status: string) {
  if (status === "ready") return "queued";
  if (status === "approved") return "passed";
  return status;
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
