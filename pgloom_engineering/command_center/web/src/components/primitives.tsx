import { Activity, Command, GitBranch, LayoutDashboard, Network, Pause, Play, Radio, Search, ShieldCheck, WalletCards } from "lucide-react";
import type { ReactNode } from "react";
import { formatMicros, formatSeconds, formatTokens } from "../lib/money";

export const STATUS: Record<string, { c: string; label: string }> = {
  running: { c: "run", label: "RUNNING" },
  passed: { c: "pass", label: "PASSED" },
  pass: { c: "pass", label: "PASS" },
  failed: { c: "fail", label: "FAILED" },
  fail: { c: "fail", label: "FAIL" },
  blocked: { c: "block", label: "BLOCKED" },
  paused: { c: "pause", label: "PAUSED" },
  queued: { c: "queue", label: "QUEUED" },
  open: { c: "queue", label: "OPEN" },
  superseded: { c: "super", label: "SUPERSEDED" },
  active: { c: "run", label: "ACTIVE" },
  complete: { c: "pass", label: "COMPLETE" },
  completed: { c: "pass", label: "COMPLETED" },
  done: { c: "pass", label: "DONE" },
  signed: { c: "pass", label: "SIGNED" }
};

const ROLE: Record<string, { c: string; short: string; label: string }> = {
  planner: { c: "planner", short: "PLN", label: "planner" },
  designer: { c: "planner", short: "DSN", label: "designer" },
  implementer: { c: "impl", short: "IMP", label: "implementer" },
  reviewer: { c: "review", short: "REV", label: "reviewer" },
  qa: { c: "qa-author", short: "QA", label: "qa" },
  "qa.author": { c: "qa-author", short: "QAA", label: "qa.author" },
  "qa.verify.scrutiny": { c: "qa-scrut", short: "QSC", label: "qa.verify.scrutiny" },
  "qa.verify.usertest": { c: "qa-test", short: "QUT", label: "qa.verify.usertest" },
  recovery: { c: "recovery", short: "REC", label: "recovery" }
};

export function StatusPill({ status, label, dot = true }: { status?: string | null; label?: string; dot?: boolean }) {
  const s = STATUS[status || ""] || { c: "queue", label: (label || status || "-").toUpperCase() };
  return (
    <span className={`cc-pill cc-pill-${s.c}`}>
      {dot && <i className={status === "running" ? "cc-pulse" : ""} />}
      <span className="mono">{label || s.label}</span>
    </span>
  );
}

export function RoleBadge({ role, full = false }: { role?: string | null; full?: boolean }) {
  const r = ROLE[role || ""] || { c: "planner", short: "ROL", label: role || "-" };
  return (
    <span className={`cc-rolebadge r-${r.c}`}>
      <span className="cc-rolebadge-tag mono">{r.short}</span>
      {full && <span className="mono cc-rolebadge-full">{r.label}</span>}
    </span>
  );
}

export function CostCell({ micros, precision }: { micros?: number | null; precision?: number }) {
  return <span className="num">{formatMicros(micros, precision)}</span>;
}

export function TokenCell({ value, kind }: { value?: number | null; kind?: "cached" | "output" | "reasoning" }) {
  const cls = kind === "cached" ? "cc-tok cc-tok-cached" : kind === "output" ? "cc-tok cc-tok-out" : kind === "reasoning" ? "cc-tok cc-tok-reason" : "cc-tok";
  return <span className={`${cls} num`}>{formatTokens(value)}</span>;
}

export function WallClockBar({ split, label = false }: { split: Record<string, number | null | undefined>; label?: boolean }) {
  const order = ["queue", "lease", "model", "verify", "blocked"];
  const total = order.reduce((sum, key) => sum + Number(split[key] || 0), 0) || 1;
  return (
    <div className="cc-wc">
      <div className="cc-wc-bar" style={{ height: 6 }}>
        {order.map((key) => Number(split[key] || 0) > 0 && (
          <span key={key} className={`cc-wc-seg cc-wc-${key}`} style={{ flex: Number(split[key]) }} title={`${key}: ${formatSeconds(Number(split[key]))}`} />
        ))}
      </div>
      {label && <div className="cc-wc-legend mono"><span>total {formatSeconds(total)}</span></div>}
    </div>
  );
}

export function Panel({ kicker, title, action, children }: { kicker?: string; title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="cc-panel">
      <div className="cc-panelhd">
        <div className="cc-panelhd-l">
          {kicker && <span className="cc-kicker mono">{kicker}</span>}
          {title && <span className="cc-panelhd-title">{title}</span>}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Stat({ k, v, d }: { k: string; v: ReactNode; d?: ReactNode }) {
  return <div className="cc-stat"><div className="cc-stat-k">{k}</div><div className="cc-stat-v">{v}</div><div className="cc-stat-d">{d}</div></div>;
}

export const routeTabs = [
  { id: "", label: "Overview", icon: LayoutDashboard },
  { id: "dag", label: "DAG", icon: Network },
  { id: "handoffs", label: "Handoffs", icon: GitBranch },
  { id: "validation", label: "Validation", icon: ShieldCheck },
  { id: "telemetry", label: "Telemetry", icon: Activity },
  { id: "interventions", label: "Interventions", icon: WalletCards }
];

export function TopBar({ featureId, paused }: { featureId?: string; paused?: boolean }) {
  const liveHost = typeof window === "undefined" ? "0.0.0.0:8765" : window.location.host;
  return (
    <div className="cc-topbar">
      <div className="cc-topbar-l">
        <a className="cc-brand" href="/features" title="Back to features">
          <span className="cc-mark" aria-hidden="true"><Command size={13} /></span>
          <span className="cc-brand-name mono">COMMAND CENTER</span>
          <span className="cc-brand-sep">/</span>
          <span className="cc-brand-host mono cc-dim">pgloom-engineering</span>
        </a>
        {featureId && <div className="cc-bcrumb"><span className="mono cc-dim">feature</span><span className="mono">{featureId.slice(0, 11)}</span>{paused && <span className="cc-paused-tag mono">PAUSED</span>}</div>}
      </div>
      <div className="cc-topbar-r">
        <button className="cc-btn cc-btn-ghost cc-top-search" title="Search command center">
          <Search size={13} />
          <span className="mono cc-dim">⌘K</span>
        </button>
        <div className="cc-live"><i className="cc-pulse" /><span className="mono">LIVE</span><span className="mono cc-dim">{liveHost}</span></div>
        <div className="cc-actor mono"><span className="cc-dim">operator:</span><span>local</span></div>
      </div>
    </div>
  );
}

export function Tabs({ featureId, active }: { featureId: string; active: string }) {
  return (
    <div className="cc-tabs">
      {routeTabs.map((tab) => {
        const Icon = tab.icon;
        const href = `/feature/${featureId}${tab.id ? `/${tab.id}` : ""}`;
        return (
          <a className={`cc-tab ${active === tab.id ? "is-active" : ""}`} href={href} key={tab.id}>
            <span className="cc-tab-ico"><Icon size={14} /></span>
            <span className="cc-tab-lbl">{tab.label}</span>
          </a>
        );
      })}
    </div>
  );
}

export function PauseButton({ paused, onClick }: { paused?: boolean; onClick: () => void }) {
  return (
    <button className={`cc-btn ${paused ? "cc-btn-primary" : "cc-btn-danger"}`} onClick={onClick}>
      {paused ? <Play size={13} /> : <Pause size={13} />}
      {paused ? "Resume feature" : "Pause feature"}
    </button>
  );
}

export function LiveEventStrip({ events }: { events: Array<{ kind: string; row_id?: string | number; reason?: string }> }) {
  return (
    <div className="cc-evt">
      <span className="cc-kicker mono">cc_events ws ·</span>
      {events.slice(0, 5).map((event, idx) => (
        <span key={idx} className="cc-evt-pkt mono"><Radio size={10} className="cc-pulse" />{event.kind}<span className="cc-dim">{event.row_id ?? event.reason ?? ""}</span></span>
      ))}
    </div>
  );
}
