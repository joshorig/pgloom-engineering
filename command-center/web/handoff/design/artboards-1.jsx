// artboards.jsx — all Command Center artboards.
// Each artboard returns a single full-bleed component sized to its frame.

// ─────────────────────────────────────────────────────────
// 1. FOUNDATIONS — design system spec sheet
// ─────────────────────────────────────────────────────────
function ArtFoundations() {
  const swatch = (label, hex, role) => (
    <div className="fnd-swatch" key={label}>
      <div className="fnd-swatch-chip" style={{ background: hex }} />
      <div className="fnd-swatch-meta">
        <div className="mono fnd-sw-l">{label}</div>
        <div className="mono fnd-sw-h">{hex}</div>
        {role && <div className="mono fnd-sw-r cc-dim">{role}</div>}
      </div>
    </div>
  );

  return (
    <div className="cc fnd cc-scroll">
      <div className="fnd-hd">
        <div className="fnd-hd-l">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <CCMark size={28} accent="var(--accent)" />
            <div>
              <div className="fnd-hd-title">Command Center · Design System</div>
              <div className="fnd-hd-sub mono cc-dim">v1 — engineering instrument</div>
            </div>
          </div>
        </div>
        <div className="fnd-hd-r mono cc-dim">
          <span>operator-control surface</span> ·
          <span>desktop only</span> ·
          <span>local 127.0.0.1</span>
        </div>
      </div>

      <div className="fnd-prologue">
        Command Center is the operator control surface for autonomous engineering. It is
        not a status page. The system below is built for density without noise: hairline
        chrome, monospace data, a single accent for human attention, and a constrained
        status palette that reads identically in dark, dim, and light.
      </div>

      <div className="fnd-grid">
        {/* Type ─────────── */}
        <section className="fnd-sect" style={{ gridColumn: 'span 12' }}>
          <h3 className="fnd-sect-h">01 · Type</h3>
          <div className="fnd-type">
            <div>
              <div className="cc-kicker mono">UI · Inter Tight</div>
              <div className="fnd-type-display">Range API foundation</div>
              <div className="fnd-type-h2">Cross-shard reader scrutiny</div>
              <div className="fnd-type-body">Operators audit autonomous engineering. Every numeric
                field is rendered from <span className="mono">usd_micros</span>; every timestamp is
                ISO-8601 with explicit Z; every cost is converted at the serializer boundary.</div>
            </div>
            <div>
              <div className="cc-kicker mono">DATA · JetBrains Mono</div>
              <div className="mono fnd-type-mono-l">wf_03418122e9444754</div>
              <div className="mono fnd-type-mono-m">2026-05-10T11:34:02Z</div>
              <div className="mono fnd-type-mono-s">$8.864220 · 4 412 000 tok · 4m 12s</div>
              <div className="mono fnd-type-mono-x cc-dim">// 11px chrome · 12px body · 22px headlines</div>
            </div>
          </div>
          <div className="fnd-scale mono">
            <span><b>32</b><i>display</i></span>
            <span><b>22</b><i>stat</i></span>
            <span><b>16</b><i>panel-h</i></span>
            <span><b>13</b><i>body</i></span>
            <span><b>12</b><i>data</i></span>
            <span><b>11</b><i>chrome</i></span>
            <span><b>10</b><i>kicker</i></span>
            <span><b>9.5</b><i>caption</i></span>
          </div>
        </section>

        {/* Color ─────────── */}
        <section className="fnd-sect" style={{ gridColumn: 'span 7' }}>
          <h3 className="fnd-sect-h">02 · Surfaces &amp; ink</h3>
          <div className="fnd-swrow">
            {swatch('bg',      '#0a0c0e', 'canvas')}
            {swatch('panel',   '#11151a', 'card')}
            {swatch('panel-2', '#161b21', 'sub-card')}
            {swatch('panel-3', '#1c222a', 'inset')}
            {swatch('inset',   '#07090b', 'wells')}
          </div>
          <div className="fnd-swrow">
            {swatch('t1', '#e6ebf2', 'primary')}
            {swatch('t2', '#aab2bc', 'secondary')}
            {swatch('t3', '#6e7782', 'muted')}
            {swatch('t4', '#495159', 'dim')}
            {swatch('line', 'rgba(255,255,255,.06)', 'hairline')}
          </div>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 5' }}>
          <h3 className="fnd-sect-h">03 · Accent</h3>
          <p className="fnd-p">One accent at a time. Used for active tab, primary action, running
            state, focus ring. Never chrome.</p>
          <div className="fnd-swrow">
            {swatch('cyan',    'oklch(0.82 0.13 196)', 'default')}
            {swatch('amber',   'oklch(0.82 0.13 70)',  'alt')}
            {swatch('emerald', 'oklch(0.80 0.16 152)', 'alt')}
            {swatch('violet',  'oklch(0.78 0.10 280)', 'alt')}
          </div>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 12' }}>
          <h3 className="fnd-sect-h">04 · Status palette</h3>
          <div className="fnd-pillrow">
            {['running','passed','failed','blocked','paused','queued','superseded'].map((s) =>
              <StatusPill status={s} key={s} />)}
          </div>
          <p className="fnd-p mono cc-dim" style={{ marginTop: 10 }}>
            Status reads from <span className="cc-mono">color + glyph + position</span>; never from
            color alone. Pulses (cyan) are reserved for live activity.
          </p>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 12' }}>
          <h3 className="fnd-sect-h">05 · Roles</h3>
          <div className="fnd-rolelist">
            {['planner','implementer','reviewer','qa.author','qa.verify.scrutiny','qa.verify.usertest','recovery'].map((r) =>
              <RoleBadge role={r} full key={r} />)}
          </div>
        </section>

        {/* Components ─────────── */}
        <section className="fnd-sect" style={{ gridColumn: 'span 6' }}>
          <h3 className="fnd-sect-h">06 · Buttons &amp; inputs</h3>
          <div className="fnd-btnrow">
            <button className="cc-btn cc-btn-primary">Resume feature</button>
            <button className="cc-btn">Run again</button>
            <button className="cc-btn cc-btn-ghost">Cancel</button>
            <button className="cc-btn cc-btn-danger">{ICONS.pause} Pause</button>
          </div>
          <div className="fnd-btnrow">
            <input className="cc-input" placeholder="filter feature_id, project, role…" style={{ width: 240 }} />
            <span className="cc-chip is-on">role:scrutiny<span className="cc-chip-x">×</span></span>
            <span className="cc-chip">project:shardquery</span>
            <span className="cc-chip">state:running</span>
          </div>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 6' }}>
          <h3 className="fnd-sect-h">07 · Wall-clock bar</h3>
          <p className="fnd-p">Five-segment stack: queue · lease · model · verify · blocked. Renders
            inline at row scale, full-bleed in detail panels.</p>
          <div style={{ width: 360, marginTop: 10 }}>
            <WallClockBar split={{ queue: 12, lease: 4, model: 188, verify: 22, blocked: 0 }} />
          </div>
          <div style={{ width: 360, marginTop: 12 }}>
            <WallClockBar split={{ queue: 28, lease: 7, model: 188, verify: 24, blocked: 14 }} />
          </div>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 12' }}>
          <h3 className="fnd-sect-h">08 · Money &amp; tokens — render-only</h3>
          <p className="fnd-p">Costs travel as integer <span className="mono">usd_micros</span> on the
            wire; render at 4 decimals per call, 2 in aggregate.</p>
          <table className="cc-table fnd-tbl">
            <thead><tr>
              <th>kind</th><th>wire</th><th>render</th><th>example</th>
            </tr></thead>
            <tbody>
              <tr><td>per-call cost</td><td className="mono cc-dim">312_000</td><td className="mono">$0.0312</td><td className="mono cc-dim">scrutiny attempt 2</td></tr>
              <tr><td>cumulative cost</td><td className="mono cc-dim">8_864_220</td><td className="mono">$8.86</td><td className="mono cc-dim">feature total · 47 runs</td></tr>
              <tr><td>tokens (cached)</td><td className="mono cc-dim">3_882_000</td><td className="mono cc-tok cc-tok-cached">3.88M</td><td className="mono cc-dim">88% of input</td></tr>
              <tr><td>tokens (output)</td><td className="mono cc-dim">184_400</td><td className="mono cc-tok cc-tok-out">184.4k</td><td className="mono cc-dim">across 47 runs</td></tr>
              <tr><td>tokens (reasoning)</td><td className="mono cc-dim">318_000</td><td className="mono cc-tok cc-tok-reason">318.0k</td><td className="mono cc-dim">o-class only</td></tr>
            </tbody>
          </table>
        </section>

        <section className="fnd-sect" style={{ gridColumn: 'span 12' }}>
          <h3 className="fnd-sect-h">09 · Principles</h3>
          <ol className="fnd-rules mono">
            <li>Hairlines, not shadows. One radius (4px). Chrome is structural.</li>
            <li>Monospace for any value an operator might paste. Sans for everything else.</li>
            <li>Status uses glyph + position; color is reinforcement, not encoding.</li>
            <li>Density is the default. Tweaks toggle compact for triage; never the other way.</li>
            <li>The accent points to <em>one</em> thing per screen. If two things glow, neither does.</li>
            <li>Every operator click writes one row. UI affordance ≠ data dedup.</li>
          </ol>
        </section>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// 2. FEATURES LIST — top-level table
// ─────────────────────────────────────────────────────────
function ArtFeaturesList({ accent }) {
  const fl = window.CC_FEATURES_LIST;
  const sums = fl.reduce((a, f) => ({ runs: a.runs + f.runs, cost: a.cost + f.cost }), { runs: 0, cost: 0 });
  return (
    <CCAppList accent={accent}>
      <div className="cc-pane">
        <div className="cc-flist-bar">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="cc-kicker mono">FEATURES</span>
            <span className="num cc-dim">{fl.length} active · 7 archived</span>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input className="cc-input" placeholder="filter feature_id, project, branch…" style={{ width: 320 }} />
            <span className="cc-chip is-on">state:any<span className="cc-chip-x">×</span></span>
            <span className="cc-chip">role:any</span>
            <button className="cc-btn cc-btn-ghost">{ICONS.search}<span className="mono cc-dim">⌘K</span></button>
            <button className="cc-btn cc-btn-primary">+ New feature</button>
          </div>
        </div>

        <div className="cc-flist-tbl">
          <table className="cc-table">
            <thead>
              <tr>
                <th style={{ width: 28 }}></th>
                <th>feature_id</th>
                <th>project</th>
                <th style={{ width: '32%' }}>title</th>
                <th>state</th>
                <th style={{ textAlign: 'right' }}>runs</th>
                <th style={{ textAlign: 'right' }}>cost</th>
                <th>roles</th>
                <th>blocker</th>
                <th style={{ textAlign: 'right' }}>created</th>
              </tr>
            </thead>
            <tbody>
              {fl.map((f, i) => (
                <tr key={f.short} className={i === 0 ? 'is-selected' : ''}>
                  <td className="cc-dim mono" style={{ textAlign: 'center' }}>
                    {i === 0 ? <span style={{ color: 'var(--accent)' }}>▸</span> : ''}
                  </td>
                  <td className="mono">{f.short}</td>
                  <td className="mono cc-dim">{f.project}</td>
                  <td>{f.title}</td>
                  <td><StatusPill status={f.state} /></td>
                  <td className="num" style={{ textAlign: 'right' }}>{f.runs}</td>
                  <td className="num" style={{ textAlign: 'right' }}><CostCell micros={f.cost} precision={2} /></td>
                  <td>
                    <span style={{ display: 'inline-flex', gap: 2 }}>
                      {Array.from({ length: 6 }).map((_, k) => (
                        <i key={k} style={{ width: 4, height: 12, background: k < f.roles ? 'var(--t2)' : 'var(--line-2)' }} />
                      ))}
                    </span>
                  </td>
                  <td className="cc-dim mono" style={{ fontSize: 11 }}>{f.blocker}</td>
                  <td className="cc-dim mono" style={{ textAlign: 'right', fontSize: 11 }}>{f.created}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="cc-flist-foot mono cc-dim">
          <span>Σ {fl.length} rows</span>
          <span>·</span>
          <span>{sums.runs} total runs</span>
          <span>·</span>
          <span>{fmtUSD(sums.cost, 2)} accumulated</span>
          <span style={{ marginLeft: 'auto' }}>last NOTIFY 0.42s ago · cc_events · {sums.runs} pkts/min</span>
        </div>
      </div>
    </CCAppList>
  );
}

// ─────────────────────────────────────────────────────────
// 3. FEATURE OVERVIEW
// ─────────────────────────────────────────────────────────
function ArtFeatureOverview({ paused, accent, onTogglePause, pulse }) {
  const f = window.CC_FEATURE;
  const ms = window.CC_MILESTONES;
  const ints = window.CC_INTERVENTIONS.slice(-4).reverse();
  const tel = window.CC_TELEMETRY.totals;
  const slot = window.CC_TELEMETRY.user_test_slot;
  const dispatch = window.CC_TASKS.find((t) => t.id === f.next_task);

  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, gap: 14, display: 'flex', flexDirection: 'column' }}>

        {/* paused banner */}
        {paused && (
          <div className="cc-banner cc-banner-pause">
            <span className="mono cc-banner-tag">PAUSED</span>
            <div style={{ flex: 1 }}>
              <div>Feature is paused. New dispatch is blocked at the worker pre-gate. In-flight runs continue to completion.</div>
              <div className="mono cc-dim" style={{ fontSize: 11 }}>by operator:josh@local · 41m ago · audit row #1043</div>
            </div>
            <button className="cc-btn cc-btn-primary" onClick={onTogglePause}>{ICONS.play} Resume</button>
          </div>
        )}

        {/* hero strip */}
        <div className="cc-hero">
          <div className="cc-hero-l">
            <div className="cc-kicker mono">FEATURE · {f.id_short}</div>
            <h1 className="cc-hero-title">{f.title}</h1>
            <div className="cc-hero-meta mono">
              <span><span className="cc-dim">project</span> {f.project}</span>
              <span><span className="cc-dim">branch</span> {f.branch}</span>
              <span><span className="cc-dim">plan</span> {f.hash}</span>
              <span><span className="cc-dim">created</span> {f.created_at}</span>
            </div>
          </div>
          <div className="cc-hero-r">
            <button className={'cc-btn ' + (paused ? 'cc-btn-primary' : 'cc-btn-danger')} onClick={onTogglePause}>
              {paused ? <>{ICONS.play} Resume feature</> : <>{ICONS.pause} Pause feature</>}
            </button>
            <button className="cc-btn cc-btn-ghost">Replan from m2…</button>
            <button className="cc-btn cc-btn-ghost"><span style={{ display: 'inline-flex', gap: 6 }}>{ICONS.kebab}</span></button>
          </div>
        </div>

        {/* stat row */}
        <div className="cc-stat-row">
          <Stat k="cumulative cost" v={fmtUSD(tel.cost_micros, 2)} d={<><span className="cc-stat-up">▼ 14%</span><span>vs prior R</span></>} />
          <Stat k="elapsed wall-clock" v={fmtSecs(f.elapsed_seconds)} d={<><span className="cc-dim">model 65% · verify 21% · queue 9%</span></>} />
          <Stat k="runs · attempts" v={`${tel.runs} · ${tel.runs + tel.repairs}`} d={<><span className="cc-dim">{tel.repairs} repairs across 47</span></>} />
          <Stat k="tokens (in / cached)" v={fmtTokens(tel.tok_in)} d={<><span className="cc-tok-cached">{Math.round(tel.tok_cached / tel.tok_in * 100)}% cached</span><span className="cc-dim">savior −{fmtTokens(tel.savior_saved)}</span></>} />
          <Stat k="next claimable" v={dispatch?.id || '—'} d={<><RoleBadge role={dispatch?.role} /></>} />
          <Stat k="user-test slot" v={`${slot.queued + 1} / ${slot.max}`} d={<><span className="cc-dim">holding {fmtSecs(slot.holding_seconds)}</span></>} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 14, flex: 1, minHeight: 0 }}>
          {/* milestones */}
          <div className="cc-panel" style={{ minHeight: 0 }}>
            <PanelHd kicker="MILESTONES" title="Plan progression" count={`${ms.filter((m) => m.signed).length} / ${ms.length} signed`} />
            <div className="cc-ms-track">
              {ms.map((m, i) => {
                const tasks = window.CC_TASKS.filter((t) => t.ms === m.id && t.status !== 'superseded');
                const passed = tasks.filter((t) => t.status === 'passed').length;
                const running = tasks.filter((t) => t.status === 'running').length;
                const cur = m.id === f.current_milestone;
                return (
                  <div className={'cc-ms ' + (m.signed ? 'is-signed' : cur ? 'is-current' : '')} key={m.id}>
                    <div className="cc-ms-hd">
                      <span className="mono cc-ms-id">{m.id.toUpperCase()}</span>
                      <StatusPill status={m.signed ? 'signed' : cur ? 'running' : 'queued'} />
                    </div>
                    <div className="cc-ms-label">{m.label}</div>
                    <div className="cc-ms-bar">
                      {tasks.map((t) => (
                        <i key={t.id} className={`cc-ms-dot st-${STATUS[t.status]?.c || 'queue'}`} title={`${t.id} · ${t.role}`} />
                      ))}
                    </div>
                    <div className="cc-ms-meta mono cc-dim">
                      {passed}/{tasks.length} done{running ? ` · ${running} running` : ''}
                      {m.signed ? ' · signed' : cur ? ' · in progress' : ' · locked'}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* recent interventions */}
          <div className="cc-panel" style={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <PanelHd kicker="INTERVENTIONS" title="Recent operator actions" action={<span className="mono cc-dim">audit log →</span>} />
            <div className="cc-ints cc-scroll">
              {ints.map((it) => (
                <div className="cc-int" key={it.id}>
                  <div className="cc-int-hd">
                    <span className={'cc-int-kind mono kind-' + it.kind.replace(/_/g, '-')}>{it.kind}</span>
                    <span className="mono cc-dim cc-int-id">#{it.id}</span>
                    <span className="mono cc-int-ts cc-dim">{it.ts.slice(11, 19)}</span>
                  </div>
                  <div className="mono cc-int-actor cc-dim">{it.actor}</div>
                  {it.note && <div className="cc-int-note">{it.note}</div>}
                  {it.payload?.note && <div className="cc-int-note">"{it.payload.note}"</div>}
                  {it.payload?.task_id && <div className="cc-int-note mono cc-dim">target: {it.payload.task_id} · reason: {it.payload.reason}</div>}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* live event strip */}
        <div className="cc-evt">
          <span className="cc-kicker mono">cc_events ws ·</span>
          {[
            { k: 'worker_run.update', t: 'task_44',  s: 'running'   },
            { k: 'task.update',       t: 'task_44',  s: 'attempt 2' },
            { k: 'worker_run.update', t: 'task_44',  s: 'tok 184k → 192k' },
            { k: 'handoff.update',    t: 'h_804',    s: 'open' },
          ].map((e, i) => (
            <span key={i} className="cc-evt-pkt mono">
              <i className={pulse !== false ? 'cc-pulse' : ''} />
              {e.k} <span className="cc-dim">{e.t}</span> {e.s}
            </span>
          ))}
          <span className="mono cc-dim" style={{ marginLeft: 'auto' }}>Δ 0.42s · 17 pkts/min · drop 0</span>
        </div>
      </div>
    </CCApp>
  );
}

function Stat({ k, v, d }) {
  return (
    <div className="cc-stat">
      <div className="cc-stat-k">{k}</div>
      <div className="cc-stat-v">{v}</div>
      <div className="cc-stat-d">{d}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// 4. DAG VIEW
// ─────────────────────────────────────────────────────────
function ArtDAG({ paused, accent, pulse }) {
  // Layout the DAG manually: columns per milestone, rows per role lane.
  const ms = window.CC_MILESTONES;
  const tasks = window.CC_TASKS.filter((t) => t.status !== 'superseded');
  const roleLanes = ['planner', 'qa.author', 'implementer', 'reviewer', 'qa.verify.scrutiny', 'qa.verify.usertest', 'recovery'];
  const colW = 152;
  const rowH = 70;
  const padX = 24;
  const padY = 32;
  const colGap = 28;
  // map task -> position
  const tpos = {};
  ms.forEach((m, mi) => {
    const inMs = tasks.filter((t) => t.ms === m.id);
    const lanesUsed = {};
    inMs.forEach((t) => {
      const lane = roleLanes.indexOf(t.role);
      lanesUsed[lane] = (lanesUsed[lane] || 0) + 1;
      const slot = lanesUsed[lane] - 1;
      tpos[t.id] = {
        x: padX + mi * (colW + colGap) + slot * 6,
        y: padY + lane * rowH,
      };
    });
  });

  const sel = 'task_44';
  const selT = tasks.find((t) => t.id === sel);
  const selRun = window.CC_RUNS.find((r) => r.task === sel && r.status === 'running');
  const selRuns = window.CC_RUNS.filter((r) => r.task === sel);

  return (
    <CCApp tab="dag" paused={paused} accent={accent}>
      <div className="cc-dag-pane">
        <div className="cc-dag-canvas-wrap cc-grid-bg">
          {/* DAG toolbar */}
          <div className="cc-dag-toolbar">
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span className="cc-kicker mono">LAYOUT</span>
              <span className="cc-chip is-on">dagre · LR</span>
              <span className="cc-chip">klay</span>
              <span className="cc-chip">force</span>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span className="cc-kicker mono">FILTER</span>
              <span className="cc-chip is-on">all roles</span>
              <span className="cc-chip">running</span>
              <span className="cc-chip">blocked</span>
              <span className="cc-chip">show superseded</span>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="mono cc-dim">87% zoom · fit</span>
              <button className="cc-btn cc-btn-ghost">−</button>
              <button className="cc-btn cc-btn-ghost">+</button>
              <button className="cc-btn cc-btn-ghost">fit</button>
            </div>
          </div>

          <svg className="cc-dag-svg" viewBox={`0 0 ${padX + ms.length * (colW + colGap) + 60} ${padY + roleLanes.length * rowH + 60}`} xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="dagArrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0 0L8 4L0 8z" fill="rgba(170,178,188,0.5)" />
              </marker>
              <marker id="dagArrowL" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0 0L8 4L0 8z" fill="oklch(0.82 0.13 70 / 0.7)" />
              </marker>
            </defs>

            {/* milestone columns */}
            {ms.map((m, mi) => {
              const x = padX + mi * (colW + colGap) - 8;
              const w = colW + 16;
              const h = roleLanes.length * rowH + 16;
              return (
                <g key={m.id}>
                  <rect x={x} y={padY - 12} width={w} height={h} rx="3"
                    fill={m.id === 'm2' ? 'oklch(0.82 0.13 196 / 0.05)' : 'transparent'}
                    stroke={m.signed ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.10)'}
                    strokeDasharray={m.signed ? '0' : '3 3'} />
                  <text x={x + w / 2} y={padY - 18} textAnchor="middle" fill="rgba(170,178,188,0.7)" style={{ font: '500 9.5px var(--f-mono)', letterSpacing: '0.1em' }}>
                    {m.id.toUpperCase()} · {m.label.toUpperCase()}
                  </text>
                </g>
              );
            })}

            {/* role lane labels */}
            {roleLanes.map((r, i) => {
              const y = padY + i * rowH + 18;
              return (
                <text key={r} x={4} y={y} fill="rgba(110,119,130,0.85)" style={{ font: '500 9px var(--f-mono)', letterSpacing: '0.1em' }}>
                  {r.toUpperCase()}
                </text>
              );
            })}

            {/* edges */}
            {tasks.map((t) => t.deps.map((d) => {
              const a = tpos[d]; const b = tpos[t.id];
              if (!a || !b) return null;
              const x1 = a.x + 96; const y1 = a.y + 18;
              const x2 = b.x;       const y2 = b.y + 18;
              const mid = (x1 + x2) / 2;
              const isLock = (window.CC_TASKS.find((x) => x.id === d)?.ms !== t.ms);
              return (
                <path key={d + '->' + t.id}
                  d={`M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={isLock ? 'oklch(0.82 0.13 70 / 0.55)' : 'rgba(170,178,188,0.30)'}
                  strokeWidth={isLock ? 1.4 : 1}
                  strokeDasharray={isLock ? '4 3' : '0'}
                  markerEnd={isLock ? 'url(#dagArrowL)' : 'url(#dagArrow)'} />
              );
            }))}

            {/* nodes */}
            {tasks.map((t) => {
              const p = tpos[t.id];
              const ring = `var(--r-${ROLE[t.role].c})`;
              const stColor = STATUS[t.status]?.c || 'queue';
              const isSel = t.id === sel;
              return (
                <g key={t.id} transform={`translate(${p.x},${p.y})`} className="cc-dag-node">
                  <rect x="0" y="0" width="96" height="36" rx="3"
                    fill="var(--panel)"
                    stroke={isSel ? 'var(--accent)' : ring}
                    strokeWidth={isSel ? 1.5 : 1} />
                  <rect x="0" y="0" width="3" height="36" fill={ring} />
                  <text x="8" y="13" fill="var(--t1)" style={{ font: '500 10.5px var(--f-mono)' }}>{t.id}</text>
                  <text x="8" y="25" fill="rgba(170,178,188,0.7)" style={{ font: '400 9.5px var(--f-sans)' }}>{t.label.length > 16 ? t.label.slice(0, 15) + '…' : t.label}</text>
                  <circle cx="86" cy="9" r="3"
                    fill={`var(--st-${stColor === 'pass' ? 'pass' : stColor === 'fail' ? 'fail' : stColor === 'block' ? 'block' : stColor === 'run' ? 'run' : stColor === 'pause' ? 'pause' : 'queue'})`}
                    className={t.status === 'running' && pulse !== false ? 'cc-pulse' : ''} />
                  {t.attempts > 1 && (
                    <text x="86" y="30" fill="rgba(170,178,188,0.7)" textAnchor="middle" style={{ font: '500 8px var(--f-mono)' }}>×{t.attempts}</text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        {/* side panel */}
        <aside className="cc-dag-side cc-scroll">
          <div className="cc-dag-side-hd">
            <div className="cc-kicker mono">SELECTED · TASK</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 4 }}>
              <span className="mono" style={{ fontSize: 16, fontWeight: 500 }}>{selT.id}</span>
              <RoleBadge role={selT.role} full />
            </div>
            <div className="cc-dag-side-title">{selT.label}</div>
            <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <StatusPill status={selT.status} />
              <span className="mono cc-dim" style={{ fontSize: 11 }}>milestone {selT.ms.toUpperCase()} · attempt {selT.attempts}</span>
            </div>
          </div>

          <div className="cc-dag-side-sect">
            <PanelHd kicker="CURRENT RUN" title={`#${selRun.id}`} action={<span className="mono cc-dim">{selRun.model}</span>} />
            <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <KV k="provider" v={selRun.provider} />
              <KV k="cost so far" v={<CostCell micros={selRun.cost} />} />
              <KV k="tokens (in)"     v={<><TokenCell value={selRun.tok_in} /> · <TokenCell value={selRun.tok_cached} kind="cached" /> cached</>} />
              <KV k="tokens (out)"    v={<><TokenCell value={selRun.tok_out} kind="output" /> · <TokenCell value={selRun.tok_reason} kind="reasoning" /> reasoning</>} />
              <div style={{ marginTop: 4 }}><WallClockBar split={selRun.wall} /></div>
            </div>
          </div>

          <div className="cc-dag-side-sect">
            <PanelHd kicker="RUN HISTORY" count={`${selRuns.length} attempts`} />
            <div className="cc-dag-runs">
              {selRuns.map((r) => (
                <div className="cc-dag-run" key={r.id}>
                  <span className="mono cc-dim" style={{ width: 30 }}>#{String(r.id).slice(-3)}</span>
                  <span className="mono" style={{ width: 36 }}>×{r.attempt}</span>
                  <StatusPill status={r.status} />
                  <CostCell micros={r.cost} />
                  <span style={{ flex: 1, minWidth: 50 }}>
                    <WallClockBar split={r.wall} label={false} height={4} />
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="cc-dag-side-sect">
            <PanelHd kicker="HANDOFFS · IN/OUT" />
            <div style={{ padding: '8px 12px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {window.CC_HANDOFFS.filter((h) => h.from === sel || h.to === sel).map((h) => (
                <div className="cc-dag-handoff mono" key={h.id}>
                  <span className="cc-dim">{h.from}</span>
                  <span style={{ color: 'var(--accent)' }}>→</span>
                  <span className="cc-dim">{h.to}</span>
                  <span style={{ flex: 1 }} />
                  <span className="cc-dim" style={{ fontSize: 10.5 }}>{h.kind}</span>
                  <StatusPill status={h.status === 'open' ? 'running' : h.status === 'pending' ? 'pending' : 'passed'} />
                </div>
              ))}
            </div>
          </div>

          <div className="cc-dag-side-sect">
            <PanelHd kicker="ARTIFACTS" />
            <div className="cc-dag-arts">
              {[
                { kind: 'prompt',  name: 'scrutiny.attempt2.prompt.md',  size: '24kb' },
                { kind: 'response',name: 'scrutiny.attempt2.response.md',size: '6kb'  },
                { kind: 'log',     name: 'pytest -q · break1.log',       size: '88kb' },
                { kind: 'log',     name: 'pytest -q · break2.log',       size: '92kb' },
                { kind: 'diff',    name: 'reader.py · 4 hunks',          size: '12kb' },
              ].map((a) => (
                <a key={a.name} className="cc-dag-art mono" href="#">
                  <span className="cc-dag-art-kind">{a.kind}</span>
                  <span className="cc-dag-art-name">{a.name}</span>
                  <span className="cc-dim" style={{ marginLeft: 'auto' }}>{a.size}</span>
                  {ICONS.ext}
                </a>
              ))}
            </div>
          </div>

          <div className="cc-dag-side-sect" style={{ padding: 12 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="cc-btn cc-btn-ghost">Skip slice…</button>
              <button className="cc-btn cc-btn-ghost">Drop slice…</button>
              <button className="cc-btn cc-btn-ghost" style={{ marginLeft: 'auto' }}>Replan from m2…</button>
            </div>
          </div>
        </aside>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtFoundations, ArtFeaturesList, ArtFeatureOverview, ArtDAG });
