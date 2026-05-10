// artboards-2.jsx — Handoff, Validation, Telemetry, Interventions

// ─────────────────────────────────────────────────────────
// 5. HANDOFF VIEW — list + diff
// ─────────────────────────────────────────────────────────
function ArtHandoff({ paused, accent, pulse }) {
  const hs = window.CC_HANDOFFS;
  const sel = hs[0]; // h_804 implementer→reviewer

  return (
    <CCApp tab="handoffs" paused={paused} accent={accent}>
      <div className="cc-h-pane">
        {/* list */}
        <div className="cc-h-list cc-scroll">
          <div className="cc-h-list-bar">
            <span className="cc-kicker mono">HANDOFFS · {hs.length}</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <span className="cc-chip is-on">open</span>
              <span className="cc-chip">pending</span>
              <span className="cc-chip">closed</span>
            </span>
          </div>
          {hs.map((h, i) => (
            <div key={h.id} className={'cc-h-row ' + (i === 0 ? 'is-selected' : '')}>
              <div className="cc-h-row-l">
                <div className="mono cc-h-row-id">{h.id}</div>
                <div className="cc-h-row-pair mono">
                  <span className="cc-dim">{h.from}</span>
                  <span style={{ color: 'var(--accent)' }}>→</span>
                  <span className="cc-dim">{h.to}</span>
                </div>
                <div className="cc-h-row-kind mono">{h.kind}</div>
                <div className="cc-h-row-roles">
                  <RoleBadge role={h.from_role} />
                  <span className="cc-dim mono">→</span>
                  <RoleBadge role={h.to_role} />
                </div>
              </div>
              <div className="cc-h-row-r">
                <span className="mono cc-dim cc-h-row-files">{h.files}f · {h.diff_loc} loc</span>
                <StatusPill status={h.status === 'open' ? 'running' : h.status === 'pending' ? 'pending' : 'passed'} />
              </div>
            </div>
          ))}
        </div>

        {/* detail */}
        <div className="cc-h-detail cc-scroll">
          <div className="cc-h-d-hd">
            <div>
              <div className="cc-kicker mono">HANDOFF · {sel.id}</div>
              <h2 className="cc-h-d-title">{sel.title}</h2>
              <div className="mono cc-dim" style={{ fontSize: 11 }}>{sel.kind} · created {sel.created} · waiting {sel.waiting}</div>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="cc-btn cc-btn-ghost">Open in editor</button>
              <button className="cc-btn">Request changes…</button>
              <button className="cc-btn cc-btn-primary">Approve &amp; advance</button>
            </div>
          </div>

          <div className="cc-h-d-bar">
            <div className="cc-h-d-pair">
              <RoleBadge role={sel.from_role} full />
              <span className="mono cc-dim">{sel.from}</span>
              <span className="cc-h-arrow">→</span>
              <RoleBadge role={sel.to_role} full />
              <span className="mono cc-dim">{sel.to}</span>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 18 }}>
              <KV k="files" v={<span className="mono">{sel.files}</span>} inline />
              <KV k="±loc"  v={<span className="mono"><b style={{ color: 'var(--st-pass)' }}>+{sel.add}</b> / <b style={{ color: 'var(--st-fail)' }}>−{sel.del}</b></span>} inline />
              <KV k="cost"  v={<CostCell micros={sel.cost} />} inline />
              <KV k="tokens" v={<span className="mono"><TokenCell value={sel.tok_in} /> in · <TokenCell value={sel.tok_out} kind="output" /> out</span>} inline />
            </div>
          </div>

          {/* checks panel */}
          <div className="cc-panel" style={{ margin: '0 14px' }}>
            <PanelHd kicker="GATES" title="Pre-handoff checks" count="6 / 7 passed" />
            <div className="cc-h-checks">
              {sel.gates.map((g) => (
                <div className={'cc-h-chk st-' + g.status} key={g.label}>
                  <span className="cc-h-chk-glyph">{g.status === 'pass' ? '✓' : g.status === 'fail' ? '×' : g.status === 'warn' ? '!' : '–'}</span>
                  <span className="cc-h-chk-label">{g.label}</span>
                  <span className="mono cc-dim cc-h-chk-meta">{g.meta}</span>
                </div>
              ))}
            </div>
          </div>

          {/* diff */}
          <div className="cc-panel" style={{ margin: 14, flex: 1 }}>
            <PanelHd kicker="DIFF" title="src/range/reader.py" action={<span className="mono cc-dim">3 of {sel.files} files · scoped view</span>} />
            <pre className="cc-diff mono">{`@@ -114,7 +114,11 @@  class CrossShardReader
   def fetch(self, ranges: List[Range]) -> Iterator[Row]:
-      shard = self._route(ranges[0].lo)
-      cur   = shard.cursor()
-      cur.execute(self.SQL, ranges[0].as_tuple())
+      grouped = group_by_shard(ranges)
+      for shard_id, sub in grouped.items():
+          cur = self._pool.checkout(shard_id, lease_ms=80)
+          try:
+              cur.execute(self.SQL_BATCH, sub.as_tuples())
+          finally:
+              self._pool.release(cur)
       yield from self._merge(cur)
`}</pre>
          </div>

          <div className="cc-panel" style={{ margin: '0 14px 14px' }}>
            <PanelHd kicker="MESSAGE" title="From implementer · attempt 2" />
            <div className="cc-h-msg">
              <p>Reworked routing into a <span className="mono">group_by_shard</span> step so ranges
                that span boundaries can be merged from a single cursor pool. Added bounded-lease
                checkouts (80 ms) so a slow shard cannot starve the request — covered in
                <span className="mono"> test_pool_lease_eviction</span>.</p>
              <p className="cc-dim">Open question for reviewer: should the merge layer surface
                <span className="mono"> partial_shard</span> as a soft warning, or fail the call?
                Spec says fail; user-test feedback says warn. Deferring to qa.verify.usertest
                slot 2/3.</p>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 6. VALIDATION — scrutiny + user-test
// ─────────────────────────────────────────────────────────
function ArtValidation({ paused, accent }) {
  const v = window.CC_VALIDATION;
  return (
    <CCApp tab="validate" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: 'flex', flexDirection: 'column' }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">VALIDATION · TASK_44 · cross-shard reader</div>
            <h2 className="cc-v-title">Two-evidence verdict</h2>
          </div>
          <div className="cc-v-verdict">
            <div className="cc-v-verdict-row">
              <span className="mono cc-dim">scrutiny</span>
              <StatusPill status="passed" /><span className="mono cc-dim">attempt 2 · 0.412 confidence Δ</span>
            </div>
            <div className="cc-v-verdict-row">
              <span className="mono cc-dim">user-test</span>
              <StatusPill status="running" /><span className="mono cc-dim">slot 2/3 · 6m 12s holding</span>
            </div>
            <div className="cc-v-verdict-row" style={{ borderTop: '1px solid var(--line)', paddingTop: 8 }}>
              <span className="mono">verdict</span>
              <span className="cc-v-pending mono">PENDING — second evidence required</span>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          {/* scrutiny */}
          <div className="cc-panel">
            <PanelHd kicker="EVIDENCE 1" title="Scrutiny breaks · attempt 2" count={`${v.scrutiny.breaks.length} attempts attempted`} />
            <div className="cc-v-attempts">
              {v.scrutiny.breaks.map((b, i) => (
                <div className={'cc-v-attempt is-' + b.outcome} key={i}>
                  <div className="cc-v-attempt-hd">
                    <span className="mono">attempt {b.attempt}</span>
                    <StatusPill status={b.outcome === 'caught' ? 'passed' : b.outcome === 'survived' ? 'failed' : 'pending'} />
                    <span className="mono cc-dim" style={{ marginLeft: 'auto' }}>{b.tag}</span>
                  </div>
                  <div className="cc-v-attempt-body mono">{b.summary}</div>
                  <div className="cc-v-attempt-foot mono cc-dim">
                    <span>broke: <span className="cc-mono">{b.broke}</span></span>
                    <span>tests: {b.tests_total} · failed: {b.tests_failed}</span>
                    <span>{b.elapsed}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* user-test slot */}
          <div className="cc-panel">
            <PanelHd kicker="EVIDENCE 2" title="User-test slot · holding" action={<span className="mono cc-dim">2/3 of feature slots</span>} />
            <div className="cc-v-ut">
              <div className="cc-v-ut-bar">
                <div className="cc-v-ut-bar-fill" style={{ width: '67%' }} />
                <div className="cc-v-ut-bar-marks">
                  <span style={{ left: '0%' }}>slot 1<br /><b>passed</b></span>
                  <span style={{ left: '34%' }}>slot 2<br /><b>holding 6m</b></span>
                  <span style={{ left: '68%' }}>slot 3<br /><b className="cc-dim">queued</b></span>
                </div>
              </div>
              <div className="cc-v-ut-tester">
                <div className="cc-v-ut-tester-hd">
                  <span className="mono">tester · op_yliao</span>
                  <span className="mono cc-dim">claimed 11:42:03Z · holding 6m 12s</span>
                </div>
                <div className="cc-v-ut-tester-body">
                  <div className="cc-v-ut-step is-done"><span className="mono">1</span> Read intent & success criteria</div>
                  <div className="cc-v-ut-step is-done"><span className="mono">2</span> Drive happy-path flow on staging</div>
                  <div className="cc-v-ut-step is-active"><span className="mono">3</span> Cross-shard query: 4 ranges, 2 shards <span className="cc-dim">in progress</span></div>
                  <div className="cc-v-ut-step"><span className="mono">4</span> Adversarial: lease-timeout shard</div>
                  <div className="cc-v-ut-step"><span className="mono">5</span> Submit verdict + evidence</div>
                </div>
              </div>
              <div className="cc-v-ut-actions">
                <button className="cc-btn cc-btn-ghost">View claim …</button>
                <button className="cc-btn cc-btn-ghost">Reassign tester</button>
                <button className="cc-btn cc-btn-danger" style={{ marginLeft: 'auto' }}>Force release slot</button>
              </div>
            </div>
          </div>
        </div>

        {/* spec & coverage */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14 }}>
          <div className="cc-panel">
            <PanelHd kicker="SPEC" title="Success criteria · authored by qa.author" count="signed by op_josh · 12h ago" />
            <ol className="cc-v-spec">
              {v.spec.map((s, i) => (
                <li key={i} className={'is-' + s.status}>
                  <span className="mono cc-v-spec-i">SC{i + 1}</span>
                  <div>
                    <div className="cc-v-spec-l">{s.label}</div>
                    <div className="mono cc-dim cc-v-spec-m">{s.method} · {s.evidence}</div>
                  </div>
                  <StatusPill status={s.status === 'pass' ? 'passed' : s.status === 'fail' ? 'failed' : s.status === 'pending' ? 'pending' : 'queued'} />
                </li>
              ))}
            </ol>
          </div>

          <div className="cc-panel">
            <PanelHd kicker="COVERAGE" title="Test surface" />
            <div className="cc-v-cov">
              {v.coverage.map((c) => (
                <div className="cc-v-cov-row" key={c.file}>
                  <span className="mono cc-v-cov-file">{c.file}</span>
                  <span className="cc-v-cov-bar">
                    <span className="cc-v-cov-bar-fill" style={{ width: c.pct + '%' }} />
                  </span>
                  <span className="mono cc-v-cov-pct">{c.pct}%</span>
                  <span className="mono cc-dim cc-v-cov-meta">{c.lines} lines</span>
                </div>
              ))}
              <div className="cc-v-cov-foot mono cc-dim">
                Σ 4 files · 612 lines · branch 87% · property checks via hypothesis (n=200)
              </div>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 7. TELEMETRY — cost / tokens / wall-clock
// ─────────────────────────────────────────────────────────
function ArtTelemetry({ paused, accent }) {
  const t = window.CC_TELEMETRY;
  // cost timeline
  const tl = t.timeline;
  const w = 760, h = 132, pad = 20;
  const maxC = Math.max(...tl.map((p) => p.cum));
  const path = tl.map((p, i) => {
    const x = pad + (i / (tl.length - 1)) * (w - 2 * pad);
    const y = h - pad - (p.cum / maxC) * (h - 2 * pad);
    return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1);
  }).join(' ');
  const fillPath = path + ` L${w - pad} ${h - pad} L${pad} ${h - pad} Z`;

  return (
    <CCApp tab="telemetry" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: 'flex', flexDirection: 'column' }}>

        {/* big stat row */}
        <div className="cc-stat-row">
          <Stat k="cumulative cost" v={fmtUSD(t.totals.cost_micros, 2)} d={<><span className="cc-stat-up">▼ 14%</span><span className="cc-dim">vs prior R</span></>} />
          <Stat k="cost per merged feature (90d)" v="$11.40" d={<span className="cc-dim">μ across 41 features</span>} />
          <Stat k="tokens (in)"  v={fmtTokens(t.totals.tok_in)}  d={<><span className="cc-tok-cached">88% cached</span></>} />
          <Stat k="tokens (out)" v={fmtTokens(t.totals.tok_out)} d={<><span className="cc-tok-out">{fmtTokens(t.totals.tok_out)}</span><span className="cc-tok-reason"> · {fmtTokens(t.totals.tok_reason)} reason</span></>} />
          <Stat k="wall-clock"   v={fmtSecs(7892)}               d={<span className="cc-dim">model 65% · verify 21%</span>} />
          <Stat k="savior cache" v={`−${fmtTokens(t.totals.savior_saved)}`} d={<span className="cc-dim">tokens not re-sent · 41 hits</span>} />
        </div>

        {/* timeline */}
        <div className="cc-panel">
          <PanelHd kicker="TIMELINE" title="Cumulative cost · 24h"
            action={<div style={{ display: 'flex', gap: 6 }}>
              <span className="cc-chip">1h</span>
              <span className="cc-chip is-on">24h</span>
              <span className="cc-chip">7d</span>
            </div>} />
          <div className="cc-tl">
            <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="cc-tl-svg">
              <defs>
                <linearGradient id="tlg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="var(--accent)" stopOpacity="0.32" />
                  <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
                </linearGradient>
              </defs>
              {[0.25, 0.5, 0.75].map((g) => (
                <line key={g} x1={pad} x2={w - pad} y1={h - pad - g * (h - 2 * pad)} y2={h - pad - g * (h - 2 * pad)} stroke="rgba(255,255,255,0.05)" />
              ))}
              <path d={fillPath} fill="url(#tlg)" />
              <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.6" />
              {tl.map((p, i) => {
                if (!p.event) return null;
                const x = pad + (i / (tl.length - 1)) * (w - 2 * pad);
                const y = h - pad - (p.cum / maxC) * (h - 2 * pad);
                return (
                  <g key={i}>
                    <circle cx={x} cy={y} r="2.5" fill="var(--accent)" />
                    <line x1={x} x2={x} y1={y} y2={h - pad + 4} stroke="rgba(170,178,188,0.3)" strokeDasharray="2 2" />
                    <text x={x} y={h - 4} textAnchor="middle" fill="rgba(170,178,188,0.7)" style={{ font: '500 9px var(--f-mono)', letterSpacing: '0.05em' }}>{p.event}</text>
                  </g>
                );
              })}
              <text x={pad} y={pad - 4} fill="rgba(110,119,130,0.85)" style={{ font: '500 9px var(--f-mono)' }}>{fmtUSD(maxC, 2)}</text>
              <text x={pad} y={h - pad + 4} fill="rgba(110,119,130,0.85)" style={{ font: '500 9px var(--f-mono)' }}>$0.00</text>
            </svg>
          </div>
        </div>

        {/* model mix + role mix */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
          <div className="cc-panel">
            <PanelHd kicker="BY MODEL" title="Cost share" />
            <div className="cc-bymodel">
              {t.by_model.map((m) => {
                const pct = m.cost / t.by_model.reduce((a, x) => a + x.cost, 0) * 100;
                return (
                  <div className="cc-bymodel-row" key={m.id}>
                    <span className="mono cc-bymodel-id">{m.id}</span>
                    <span className="cc-bymodel-bar"><span style={{ width: pct + '%' }} /></span>
                    <span className="mono cc-bymodel-c">{fmtUSD(m.cost, 2)}</span>
                    <span className="mono cc-dim cc-bymodel-r">{m.runs} runs</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="cc-panel">
            <PanelHd kicker="BY ROLE" title="Wall-clock share" />
            <div className="cc-byrole">
              {t.by_role.map((r) => {
                const pct = r.wall / t.by_role.reduce((a, x) => a + x.wall, 0) * 100;
                return (
                  <div className="cc-byrole-row" key={r.role}>
                    <RoleBadge role={r.role} full />
                    <span className="cc-bymodel-bar" style={{ flex: 1 }}>
                      <span style={{ width: pct + '%', background: `var(--r-${ROLE[r.role].c})` }} />
                    </span>
                    <span className="mono">{fmtSecs(r.wall)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="cc-panel">
            <PanelHd kicker="WALL-CLOCK MIX" title="Where time goes" />
            <div style={{ padding: 14 }}>
              <WallClockBar height={14} split={t.wall_mix} />
              <div className="cc-wc-legend">
                {[
                  { k: 'queue',   l: 'queue & dispatch' },
                  { k: 'lease',   l: 'lease & claim' },
                  { k: 'model',   l: 'model inference' },
                  { k: 'verify',  l: 'verify & test' },
                  { k: 'blocked', l: 'blocked / waiting' },
                ].map((x) => (
                  <div className="cc-wc-leg-row" key={x.k}>
                    <i className={'cc-wc-leg-dot wc-' + x.k} />
                    <span>{x.l}</span>
                    <span className="mono cc-dim" style={{ marginLeft: 'auto' }}>{fmtSecs(t.wall_mix[x.k])}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* recent runs table */}
        <div className="cc-panel" style={{ flex: 1, minHeight: 240 }}>
          <PanelHd kicker="RUNS" title="Last 12 worker runs" action={<span className="mono cc-dim">live · cc_events</span>} />
          <table className="cc-table cc-runs-tbl">
            <thead><tr>
              <th>run_id</th><th>task</th><th>role</th><th>model</th><th>state</th>
              <th style={{ textAlign: 'right' }}>cost</th>
              <th>tokens (in / out)</th>
              <th>wall</th>
              <th style={{ textAlign: 'right' }}>started</th>
            </tr></thead>
            <tbody>
              {window.CC_RUNS.slice(-12).reverse().map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td className="mono cc-dim">{r.task}</td>
                  <td><RoleBadge role={r.role} /></td>
                  <td className="mono cc-dim">{r.model}</td>
                  <td><StatusPill status={r.status} /></td>
                  <td style={{ textAlign: 'right' }}><CostCell micros={r.cost} /></td>
                  <td><TokenCell value={r.tok_in} /> · <TokenCell value={r.tok_out} kind="output" /></td>
                  <td style={{ minWidth: 120 }}><WallClockBar split={r.wall} label={false} height={4} /></td>
                  <td className="mono cc-dim" style={{ textAlign: 'right', fontSize: 11 }}>{r.started}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 8. INTERVENTIONS — audit log
// ─────────────────────────────────────────────────────────
function ArtInterventions({ paused, accent }) {
  const log = window.CC_INTERVENTIONS;
  return (
    <CCApp tab="audit" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">INTERVENTIONS · audit-grade log</div>
            <h2 className="cc-v-title">Every operator click writes one row</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>append-only · sha-anchored · {log.length} rows on this feature · global view →</p>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <span className="cc-chip is-on">all kinds</span>
            <span className="cc-chip">pause/resume</span>
            <span className="cc-chip">drop slice</span>
            <span className="cc-chip">replan</span>
          </div>
        </div>

        <div className="cc-panel" style={{ flex: 1 }}>
          <table className="cc-table cc-audit-tbl">
            <thead>
              <tr>
                <th>id</th><th>ts</th><th>actor</th><th>kind</th><th>target</th><th>note</th><th>checksum</th>
              </tr>
            </thead>
            <tbody>
              {[...log].reverse().map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td className="mono cc-dim">{r.ts.replace('T', ' ').replace('Z', 'Z')}</td>
                  <td className="mono">{r.actor}</td>
                  <td><span className={'cc-int-kind mono kind-' + r.kind.replace(/_/g, '-')}>{r.kind}</span></td>
                  <td className="mono cc-dim">{r.payload?.task_id || r.payload?.from_milestone || '—'}</td>
                  <td className="cc-audit-note">{r.note || (r.payload?.note ? '"' + r.payload.note + '"' : (r.payload?.reason || '—'))}</td>
                  <td className="mono cc-dim" style={{ fontSize: 10.5 }}>sha:{(r.id * 982451653).toString(16).slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtHandoff, ArtValidation, ArtTelemetry, ArtInterventions });
