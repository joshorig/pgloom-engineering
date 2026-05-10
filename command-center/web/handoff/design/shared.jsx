// shared.jsx — Command Center common UI primitives
// Status pills, role badges, cost/token cells, wall-clock bars, app chrome.

const ICONS = {
  features:  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><rect x="1.5" y="2"  width="11" height="2.5"/><rect x="1.5" y="5.75" width="11" height="2.5"/><rect x="1.5" y="9.5" width="11" height="2.5"/></svg>,
  dag:       <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><circle cx="3" cy="3" r="1.6"/><circle cx="11" cy="7" r="1.6"/><circle cx="3" cy="11" r="1.6"/><path d="M4.4 3.5L9.6 6.5M4.4 10.5L9.6 7.5"/></svg>,
  handoff:   <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M1.5 5h7M5.5 2L8.5 5l-3 3M12.5 9h-7M8.5 12l-3-3 3-3" /></svg>,
  validate:  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M2 7l3 3 7-7"/><path d="M7.5 11h4"/></svg>,
  telemetry: <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M1.5 12V8M5 12V5M8.5 12V9M12 12V3"/></svg>,
  audit:     <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4"><rect x="2" y="1.5" width="9" height="11"/><path d="M4 5h5M4 7.5h5M4 10h3"/></svg>,
  pause:     <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><rect x="2" y="1.5" width="2" height="7"/><rect x="6" y="1.5" width="2" height="7"/></svg>,
  play:      <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><path d="M2.5 1.5L8.5 5l-6 3.5z"/></svg>,
  search:    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3"><circle cx="5" cy="5" r="3.2"/><path d="M7.5 7.5l3 3"/></svg>,
  chev:      <svg width="9" height="9" viewBox="0 0 9 9" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M2.5 3.5L4.5 5.5L6.5 3.5"/></svg>,
  ext:       <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M3 1.5h5.5V7M8.5 1.5L4 6M2 4v4.5h4.5"/></svg>,
  copy:      <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.2"><rect x="3.5" y="1.5" width="6" height="6"/><path d="M1.5 3.5v6h6"/></svg>,
  warn:      <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="M5.5 1L10 9.5H1zM5.5 4.5v2.5M5.5 8.5v.4"/></svg>,
  dot:       <svg width="6" height="6" viewBox="0 0 6 6" fill="currentColor"><circle cx="3" cy="3" r="2.4"/></svg>,
  kebab:     <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><circle cx="6" cy="2" r="1.1"/><circle cx="6" cy="6" r="1.1"/><circle cx="6" cy="10" r="1.1"/></svg>,
  cmd:       <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M3 1.5a1.5 1.5 0 100 3h5a1.5 1.5 0 100-3 1.5 1.5 0 100 3v3a1.5 1.5 0 100 3 1.5 1.5 0 100-3h-5a1.5 1.5 0 100 3"/></svg>,
};

// ── Wordmark ────────────────────────────────────────────
function CCMark({ size = 16, accent }) {
  // Square reticle: outer hairline + inset filled corner mark.
  const ink = accent || 'currentColor';
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
      <rect x="1.5" y="1.5" width="13" height="13" stroke={ink} strokeWidth="1" />
      <rect x="1.5" y="1.5" width="5" height="5" fill={ink} />
      <path d="M9.5 11.5L11.5 9.5M9.5 9.5h2v2" stroke={ink} strokeWidth="1" />
    </svg>
  );
}

// ── Status pill ─────────────────────────────────────────
const STATUS = {
  running:    { c: 'run',   label: 'RUNNING'    },
  passed:     { c: 'pass',  label: 'PASSED'     },
  failed:     { c: 'fail',  label: 'FAILED'     },
  blocked:    { c: 'block', label: 'BLOCKED'    },
  paused:     { c: 'pause', label: 'PAUSED'     },
  queued:     { c: 'queue', label: 'QUEUED'     },
  ready:      { c: 'queue', label: 'READY'      },
  superseded: { c: 'super', label: 'SUPERSEDED' },
  signed:     { c: 'pass',  label: 'SIGNED'     },
  pending:    { c: 'queue', label: 'PENDING'    },
  repair:     { c: 'block', label: 'REPAIR'     },
};

function StatusPill({ status, label, dim, dot = true }) {
  const s = STATUS[status] || { c: 'queue', label: (label || status || '').toUpperCase() };
  const txt = label || s.label;
  return (
    <span className={`cc-pill cc-pill-${s.c}${dim ? ' cc-pill-dim' : ''}`}>
      {dot && <i className={status === 'running' ? 'cc-pulse' : ''} />}
      <span className="mono">{txt}</span>
    </span>
  );
}

// ── Role badge ──────────────────────────────────────────
const ROLE = {
  planner:            { c: 'planner',   short: 'PLN', label: 'planner'            },
  implementer:        { c: 'impl',      short: 'IMP', label: 'implementer'        },
  reviewer:           { c: 'review',    short: 'REV', label: 'reviewer'           },
  'qa.author':        { c: 'qa-author', short: 'QAA', label: 'qa.author'          },
  'qa.verify.scrutiny': { c: 'qa-scrut',short: 'QSC', label: 'qa.verify.scrutiny'},
  'qa.verify.usertest': { c: 'qa-test', short: 'QUT', label: 'qa.verify.usertest'},
  recovery:           { c: 'recovery',  short: 'REC', label: 'recovery'           },
  designer:           { c: 'planner',   short: 'DSG', label: 'designer'           },
};

function RoleBadge({ role, full }) {
  const r = ROLE[role] || { c: 'planner', short: 'ROL', label: role || '—' };
  return (
    <span className={`cc-rolebadge r-${r.c}`}>
      <span className="cc-rolebadge-tag mono">{r.short}</span>
      {full && <span className="mono cc-rolebadge-full">{r.label}</span>}
    </span>
  );
}

// ── Cost / token cells ──────────────────────────────────
function fmtUSD(micros, precision) {
  if (micros == null) return '—';
  const n = micros / 1_000_000;
  const p = precision != null ? precision : (n < 0.1 ? 4 : n < 10 ? 3 : 2);
  return '$' + n.toFixed(p);
}
function fmtTokens(n) {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}
function fmtSecs(s) {
  if (s == null) return '—';
  if (s >= 3600) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  if (s >= 60)   return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
  return s.toFixed(1) + 's';
}

function CostCell({ micros, precision, dim }) {
  return <span className={'num' + (dim ? ' cc-dim' : '')}>{fmtUSD(micros, precision)}</span>;
}

function TokenCell({ value, kind }) {
  const cls = kind === 'cached' ? 'cc-tok cc-tok-cached'
    : kind === 'reasoning' ? 'cc-tok cc-tok-reason'
    : kind === 'output'    ? 'cc-tok cc-tok-out'
    : 'cc-tok';
  return <span className={cls + ' num'}>{fmtTokens(value)}</span>;
}

// ── Wall-clock stacked bar ──────────────────────────────
// Splits queue / lease / model / verify / blocked seconds into a hairline bar.
function WallClockBar({ split, total, height = 6, label = true }) {
  const t = total || Object.values(split).reduce((a, b) => a + b, 0) || 1;
  const order = ['queue', 'lease', 'model', 'verify', 'blocked'];
  return (
    <div className="cc-wc">
      <div className="cc-wc-bar" style={{ height }}>
        {order.map((k) => {
          const v = split[k] || 0;
          if (!v) return null;
          return <span key={k} className={`cc-wc-seg cc-wc-${k}`} style={{ flex: v }} title={`${k}: ${fmtSecs(v)}`} />;
        })}
      </div>
      {label && (
        <div className="cc-wc-legend mono">
          <span>q {fmtSecs(split.queue)}</span>
          <span>l {fmtSecs(split.lease)}</span>
          <span>m {fmtSecs(split.model)}</span>
          <span>v {fmtSecs(split.verify)}</span>
          {split.blocked ? <span className="cc-wc-blocked">b {fmtSecs(split.blocked)}</span> : null}
          <span className="cc-wc-total">Σ {fmtSecs(t)}</span>
        </div>
      )}
    </div>
  );
}

// ── Top bar ─────────────────────────────────────────────
function TopBar({ feature, paused, accent, onTogglePause, host = '127.0.0.1:8765' }) {
  return (
    <div className="cc-topbar">
      <div className="cc-topbar-l">
        <div className="cc-brand">
          <CCMark size={14} accent={accent || 'var(--accent)'} />
          <span className="cc-brand-name mono">COMMAND&nbsp;CENTER</span>
          <span className="cc-brand-sep">/</span>
          <span className="cc-brand-host mono cc-dim">pgloom-engineering</span>
        </div>
        <div className="cc-bcrumb">
          {feature && (
            <>
              <span className="mono cc-dim">feature</span>
              <span className="mono">{feature.id_short}</span>
              <span className="cc-bcrumb-sep">›</span>
              <span className="cc-feature-name">{feature.title}</span>
              {paused && <span className="cc-paused-tag mono">PAUSED</span>}
            </>
          )}
        </div>
      </div>
      <div className="cc-topbar-r">
        <button className="cc-iconbtn" title="Search (⌘K)">
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>{ICONS.search}<span className="mono cc-dim">⌘K</span></span>
        </button>
        <div className="cc-live">
          <i className="cc-pulse" />
          <span className="mono">LIVE</span>
          <span className="mono cc-dim">{host}</span>
        </div>
        <div className="cc-actor mono">
          <span className="cc-dim">operator:</span>
          <span>josh@local</span>
        </div>
      </div>
    </div>
  );
}

// ── Tab strip ───────────────────────────────────────────
function Tabs({ items, active, onChange }) {
  return (
    <div className="cc-tabs">
      {items.map((t) => (
        <button key={t.id}
          className={'cc-tab ' + (t.id === active ? 'is-active' : '')}
          onClick={() => onChange && onChange(t.id)}>
          <span className="cc-tab-ico">{ICONS[t.icon]}</span>
          <span className="cc-tab-lbl">{t.label}</span>
          {t.count != null && <span className="cc-tab-count mono">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ── Section header (data panel) ─────────────────────────
function PanelHd({ kicker, title, action, count }) {
  return (
    <div className="cc-panelhd">
      <div className="cc-panelhd-l">
        {kicker && <span className="cc-kicker mono">{kicker}</span>}
        {title && <span className="cc-panelhd-title">{title}</span>}
        {count != null && <span className="cc-panelhd-count mono cc-dim">{count}</span>}
      </div>
      {action}
    </div>
  );
}

// ── Key/Value row ───────────────────────────────────────
function KV({ k, v, mono = true, full }) {
  return (
    <div className={'cc-kv' + (full ? ' cc-kv-full' : '')}>
      <span className="cc-kv-k">{k}</span>
      <span className={'cc-kv-v ' + (mono ? 'mono' : '')}>{v}</span>
    </div>
  );
}

// ── Spark: tiny inline distribution bar (e.g. token mix) ─
function SparkStack({ parts, height = 4, width = 96 }) {
  const t = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <span className="cc-spark" style={{ width, height }}>
      {parts.map((p, i) => (
        <i key={i} style={{ flex: p.value / t, background: p.color }} title={`${p.label}: ${p.value}`} />
      ))}
    </span>
  );
}

// expose
Object.assign(window, {
  ICONS, CCMark, StatusPill, RoleBadge, CostCell, TokenCell, WallClockBar,
  TopBar, Tabs, PanelHd, KV, SparkStack, fmtUSD, fmtTokens, fmtSecs, STATUS, ROLE,
});
