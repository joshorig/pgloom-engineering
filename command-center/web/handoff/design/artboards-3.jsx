// artboards-3.jsx — Additional artboards: Replan-from-milestone dialog, Live event firehose.

// ─────────────────────────────────────────────────────────
// 9. REPLAN-FROM-MILESTONE — confirm dialog
// ─────────────────────────────────────────────────────────
function ArtReplan({ paused, accent }) {
  const f = window.CC_FEATURE;
  const ms = window.CC_MILESTONES;
  const target = ms.find((m) => m.id === 'm2');
  const tasksFromTarget = window.CC_TASKS.filter((t) => ['m2', 'm3', 'm4'].includes(t.ms));
  const frozen = window.CC_TASKS.filter((t) => ['m0', 'm1'].includes(t.ms));
  const [reason, setReason] = React.useState('Fence-boundary semantics shifted; reader._merge needs new contract.');

  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane" style={{ position: 'relative', padding: 14, overflow: 'hidden' }}>
        {/* dimmed feature overview behind the dialog */}
        <div style={{ filter: 'blur(2px) brightness(0.62) saturate(0.85)', pointerEvents: 'none', height: '100%', overflow: 'hidden' }}>
          <ArtFeatureOverviewSilent />
        </div>

        {/* modal scrim */}
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(7,9,11,0.55)', backdropFilter: 'blur(2px)' }} />

        {/* dialog */}
        <div className="cc-replan-dialog">
          <div className="cc-replan-hd">
            <div>
              <div className="cc-kicker mono">REPLAN · from milestone</div>
              <h2 className="cc-replan-title">Replan from <span className="mono cc-accent-ink">m2 · Cross-shard reader</span>?</h2>
            </div>
            <button className="cc-btn cc-btn-ghost cc-replan-x" aria-label="close">✕</button>
          </div>

          <div className="cc-replan-body">
            <p className="cc-replan-desc">
              Planner will be invoked with the prior consolidated plan as <b>baseline</b>. Tasks before <span className="mono">m2</span> are <b>frozen</b> — the planner cannot mutate them, even by one byte. Tasks at or after <span className="mono">m2</span> may be rewritten, added, removed, or reordered. Existing worker runs stay attached to their original task rows; superseded tasks are kept queryable for audit.
            </p>

            <div className="cc-replan-grid">
              <div className="cc-replan-col">
                <div className="cc-replan-col-hd"><span className="mono cc-dim">FROZEN PREFIX</span><span className="mono">m0 · m1</span></div>
                <ul className="cc-replan-list cc-replan-list--frozen">
                  {frozen.map((t) => (
                    <li key={t.id}>
                      <RoleBadge role={t.role} compact />
                      <span className="mono">{t.id}</span>
                      <span className="cc-dim">{t.label}</span>
                      <span className="mono cc-dim cc-replan-pin">🔒 byte-identical</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="cc-replan-col">
                <div className="cc-replan-col-hd"><span className="mono cc-dim">SUPERSEDED · queryable</span><span className="mono">m2 → m4</span></div>
                <ul className="cc-replan-list cc-replan-list--super">
                  {tasksFromTarget.map((t) => (
                    <li key={t.id}>
                      <RoleBadge role={t.role} compact />
                      <span className="mono">{t.id}</span>
                      <span className="cc-dim">{t.label}</span>
                      <span className="mono cc-replan-old">→ superseded</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="cc-replan-reason">
              <label className="cc-kicker mono" htmlFor="rsn">REASON · stamped on the audit row</label>
              <textarea id="rsn" value={reason} onChange={(e) => setReason(e.target.value)} className="mono" rows={2} />
            </div>

            <div className="cc-replan-rules">
              <div className="cc-replan-rule"><span className="mono cc-replan-rule-ok">ok</span> planner brief notes <span className="mono">replan_from_milestone_id=m2</span></div>
              <div className="cc-replan-rule"><span className="mono cc-replan-rule-ok">ok</span> baseline plan attached as input contract</div>
              <div className="cc-replan-rule"><span className="mono cc-replan-rule-warn">warn</span> a planner output that mutates the frozen prefix is rejected</div>
              <div className="cc-replan-rule"><span className="mono cc-replan-rule-ok">ok</span> writes one row to <span className="mono">engineering_operator_interventions</span></div>
            </div>
          </div>

          <div className="cc-replan-ft">
            <span className="mono cc-dim">audit will read: <span className="cc-t1">replan · from_milestone=m2 · actor=op_josh@local</span></span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="cc-btn cc-btn-ghost">Cancel</button>
              <button className="cc-btn cc-btn-primary">Replan from m2</button>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

// silent version (no Pause callback) used as the dialog backdrop
function ArtFeatureOverviewSilent() {
  return (
    <div style={{ padding: 14, color: 'var(--t2)' }}>
      <div className="cc-hero">
        <div className="cc-hero-l">
          <div className="cc-kicker mono">FEATURE · wf_03418122</div>
          <h1 className="cc-hero-title">Range API · cross-shard reader (R)</h1>
        </div>
      </div>
      <div className="cc-stat-row">
        <Stat k="cumulative cost" v="$8.86" d={<span className="cc-dim">—</span>} />
        <Stat k="elapsed" v="2h 11m" d={<span className="cc-dim">—</span>} />
        <Stat k="runs" v="47" d={<span className="cc-dim">—</span>} />
        <Stat k="tokens" v="4.4M" d={<span className="cc-dim">—</span>} />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// 10. LIVE EVENT FIREHOSE — what the WS actually broadcasts
// ─────────────────────────────────────────────────────────
function ArtFirehose({ paused, accent, pulse }) {
  const events = [
    { ts: '12:34:56.789', kind: 'worker_run.update',  fid: 'wf_03418122', row: 9182, fields: ['status','running_seconds','cost_usd_micros'], bytes: 412 },
    { ts: '12:34:55.241', kind: 'worker_run.update',  fid: 'wf_034182cd', row: 9181, fields: ['running_seconds'], bytes: 188 },
    { ts: '12:34:53.012', kind: 'task.update',        fid: 'wf_03418122', row: 'task_44', fields: ['attempts','last_run_id'], bytes: 244 },
    { ts: '12:34:51.842', kind: 'qa.signoff',         fid: 'wf_03415a18', row: 'sig_88', fields: ['scrutiny','usertest','signed_at'], bytes: 312 },
    { ts: '12:34:48.118', kind: 'intervention.added', fid: 'wf_03418122', row: 1049, fields: ['actor','kind','payload'], bytes: 188 },
    { ts: '12:34:47.882', kind: 'feature.update',     fid: 'wf_03418122', row: 'wf', fields: ['paused','last_intervention_id'], bytes: 144 },
    { ts: '12:34:42.014', kind: 'recovery.update',    fid: 'wf_034176ab', row: 'rec_22', fields: ['status','reason'], bytes: 232 },
    { ts: '12:34:39.901', kind: 'handoff.update',     fid: 'wf_03418122', row: 'h_212', fields: ['accepted_at','from','to'], bytes: 268 },
    { ts: '12:34:38.412', kind: 'plan.update',        fid: 'wf_034128b3', row: 'plan_4', fields: ['status','milestones[]'], bytes: 422 },
    { ts: '12:34:37.001', kind: 'worker_run.update',  fid: 'wf_03418fa1', row: 9180, fields: ['model_seconds'], bytes: 152 },
    { ts: '12:34:34.118', kind: 'worker_run.update',  fid: 'wf_03418122', row: 9179, fields: ['status','tokens_in','tokens_cached'], bytes: 488 },
    { ts: '12:34:30.842', kind: '__hint',             fid: '*',           row: 'resync', fields: ['queue overflow on ws#3'], bytes: 96 },
  ];
  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">REALTIME · pg_notify('cc_events', …)</div>
            <h2 className="cc-v-title">Live event firehose</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>asyncpg LISTEN → 1 ws per client · payloads ≤ 7500B · drop-oldest on overflow → resync hint</p>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span className={'cc-fh-status ' + (pulse ? 'cc-pulse' : '')}><i /> connected · 4 subscribers</span>
            <span className="cc-chip is-on">all kinds</span>
            <span className="cc-chip">worker_run</span>
            <span className="cc-chip">qa</span>
            <span className="cc-chip">interv.</span>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 14, flex: 1, minHeight: 0 }}>
          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="STREAM" title="cc_events · ordered by NOTIFY ts" action={<span className="mono cc-dim">7,242 evts in last 60s</span>} />
            <div className="cc-fh-head mono">
              <span />
              <span>ts</span>
              <span>kind</span>
              <span>feature_id</span>
              <span title="Primary key of the row that mutated, in its source table. Clients refetch by this id when payloads exceed 7500B.">affected row · pk</span>
              <span>fields[]</span>
              <span style={{ textAlign: 'right' }}>bytes</span>
            </div>
            <div className="cc-fh-list cc-scroll">
              {events.map((e, i) => (
                <div key={i} className={'cc-fh-row kind-' + e.kind.replace(/_/g, '-').replace('.', '-')}>
                  <span className={'cc-fh-dot ' + (i < 3 && pulse ? 'cc-pulse' : '')} />
                  <span className="mono cc-fh-ts cc-dim">{e.ts}</span>
                  <span className={'cc-fh-kind mono kind-' + e.kind.split('.')[0]}>{e.kind}</span>
                  <span className="mono cc-fh-fid cc-dim">{e.fid}</span>
                  <span className="mono cc-fh-row-id">{e.row}</span>
                  <span className="cc-fh-fields mono cc-dim">{e.fields.join(' · ')}</span>
                  <span className="mono cc-fh-bytes cc-dim">{e.bytes}B</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
            <div className="cc-panel">
              <PanelHd kicker="SAMPLE PAYLOAD" title="worker_run.update · #9182" />
              <pre className="cc-fh-json mono">{`{
  "v": 1,
  "kind": "worker_run.update",
  "feature_id": "wf_03418122e944475492056b7264ce0772",
  "row_id": 9182,
  "fields": [
    "status",
    "running_seconds",
    "cost_usd_micros"
  ],
  "ts": "2026-05-10T12:34:56.789Z"
}`}</pre>
            </div>
            <div className="cc-panel">
              <PanelHd kicker="GUARDRAILS" title="trigger contract" />
              <ul className="cc-fh-rules mono">
                <li><span className="cc-replan-rule-ok">✓</span> diff OLD/NEW for fields[]</li>
                <li><span className="cc-replan-rule-ok">✓</span> never include &gt;1KB columns</li>
                <li><span className="cc-replan-rule-ok">✓</span> short-circuit on updated_at-only</li>
                <li><span className="cc-replan-rule-ok">✓</span> oversized → resync hint</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtReplan, ArtFirehose });
