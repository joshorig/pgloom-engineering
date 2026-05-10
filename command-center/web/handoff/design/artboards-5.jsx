// artboards-5.jsx — Recovery flow, Token economy detail, Empty/error states

// ─────────────────────────────────────────────────────────
// 11. RECOVERY & CORRECTIVE SLICES
// ─────────────────────────────────────────────────────────
function ArtRecovery({ paused, accent }) {
  const events = [
    { t: '14:02:18', kind: 'intervention', text: 'operator: skip_slice', payload: 'task=task_38 · reason=upstream API renamed; doc-only delta', actor: 'op_josh@local' },
    { t: '14:02:18', kind: 'recovery',     text: 'recovery_action.created', payload: 'kind=skip_resolution · for=task_38 · status=open' },
    { t: '14:02:23', kind: 'handoff',      text: 'handoff: recovery → implementer', payload: 'h_408 · type=skip_resolution · skip_id=task_38' },
    { t: '14:02:48', kind: 'worker',       text: 'task_39 · implementer started', payload: 'attempt=1 · model=sonnet-4.5 · cost=$0.00' },
    { t: '14:04:11', kind: 'worker',       text: 'task_39 · implementer · running', payload: 'tokens_in=12,442 · cached=8,128 · model_seconds=82' },
    { t: '14:06:02', kind: 'worker',       text: 'task_39 · implementer · pass', payload: 'cost=$0.31 · attempts=1 · superseded_by=task_38' },
    { t: '14:06:02', kind: 'recovery',     text: 'recovery_action.resolved', payload: 'kind=skip_resolution · for=task_38 · resolved_by=task_39' },
  ];

  const corrective = [
    { id: 'cs_004', from: 'qa.verify.scrutiny', reason: 'planner missed milestone-lock for task_44 deps', emitted: '12m ago', tasks: 2, status: 'merged' },
    { id: 'cs_007', from: 'qa.verify.usertest', reason: 'cross-shard latency budget violated', emitted: '06m ago', tasks: 3, status: 'merged' },
    { id: 'cs_011', from: 'qa.verify.usertest', reason: 'fence-boundary regression on overlapping reads', emitted: '02m ago', tasks: 4, status: 'pending' },
  ];

  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">RECOVERY · corrective slices</div>
            <h2 className="cc-v-title">Closing the loop on intervention</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>skip / drop / replan write a row → recovery worker emits a handoff → downstream consumes uniformly</p>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <Stat k="open recoveries" v="1" />
            <Stat k="resolved · 24h" v="14" d={<span className="mono cc-dim">+3 vs prev</span>} />
            <Stat k="corrective slices" v="3" d={<span className="mono cc-dim">2 merged · 1 pending</span>} />
          </div>
        </div>

        {/* FLOW DIAGRAM */}
        <div className="cc-panel">
          <PanelHd kicker="FLOW" title="how a skip becomes a green task" />
          <div className="cc-rec-flow">
            <div className="cc-rec-node is-interv">
              <div className="cc-kicker mono">1 · INTERVENTION</div>
              <div className="cc-rec-node-body">
                <div className="mono cc-rec-row"><span>action</span><span className="cc-accent-ink">skip_slice</span></div>
                <div className="mono cc-rec-row"><span>target</span><span>task_38</span></div>
                <div className="mono cc-rec-row"><span>reason</span><span className="cc-dim">upstream API renamed…</span></div>
              </div>
              <div className="cc-rec-foot mono cc-dim">writes engineering_operator_interventions</div>
            </div>
            <div className="cc-rec-arrow"><span /><i>NOTIFY 'intervention.added'</i></div>
            <div className="cc-rec-node is-recov">
              <div className="cc-kicker mono">2 · RECOVERY</div>
              <div className="cc-rec-node-body">
                <div className="mono cc-rec-row"><span>kind</span><span>skip_resolution</span></div>
                <div className="mono cc-rec-row"><span>for</span><span>task_38</span></div>
                <div className="mono cc-rec-row"><span>status</span><span className="st-run">open</span></div>
              </div>
              <div className="cc-rec-foot mono cc-dim">recovery worker · gates downstream</div>
            </div>
            <div className="cc-rec-arrow"><span /><i>handoff h_408 · skip_resolution</i></div>
            <div className="cc-rec-node is-task">
              <div className="cc-kicker mono">3 · CORRECTIVE TASK</div>
              <div className="cc-rec-node-body">
                <div className="mono cc-rec-row"><span>id</span><span>task_39</span></div>
                <div className="mono cc-rec-row"><span>role</span><span>implementer</span></div>
                <div className="mono cc-rec-row"><span>supersedes</span><span>task_38</span></div>
              </div>
              <div className="cc-rec-foot mono cc-dim">runs through normal dispatch</div>
            </div>
            <div className="cc-rec-arrow"><span /><i>green</i></div>
            <div className="cc-rec-node is-resolved">
              <div className="cc-kicker mono">4 · RESOLVED</div>
              <div className="cc-rec-node-body">
                <div className="mono cc-rec-row"><span>recovery</span><span className="st-pass">closed</span></div>
                <div className="mono cc-rec-row"><span>cost</span><span>$0.31</span></div>
                <div className="mono cc-rec-row"><span>attempts</span><span>1</span></div>
              </div>
              <div className="cc-rec-foot mono cc-dim">milestone gate unblocked</div>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14, flex: 1, minHeight: 0 }}>
          {/* CORRECTIVE SLICES */}
          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="CORRECTIVE SLICES · validator-emitted" title="when scrutiny / usertest tells the planner something" />
            <div className="cc-rec-slices cc-scroll">
              {corrective.map((c) => (
                <div key={c.id} className={'cc-rec-slice is-' + c.status}>
                  <div className="cc-rec-slice-hd">
                    <span className="mono cc-rec-id">{c.id}</span>
                    <span className={'cc-pill mono is-' + c.status}>{c.status}</span>
                    <span className="mono cc-dim cc-rec-time">{c.emitted}</span>
                  </div>
                  <div className="cc-rec-slice-body">
                    <div className="mono cc-rec-row"><span>from</span><RoleBadge role={c.from} compact /></div>
                    <div className="cc-rec-reason">{c.reason}</div>
                    <div className="mono cc-rec-row"><span>net tasks</span><span>+{c.tasks}</span></div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* TIMELINE */}
          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="EVENT TRACE" title="task_38 · skip → resolution" />
            <div className="cc-rec-trace cc-scroll">
              {events.map((e, i) => (
                <div key={i} className={'cc-rec-evt kind-' + e.kind}>
                  <span className="cc-rec-evt-dot" />
                  <div className="cc-rec-evt-body">
                    <div className="cc-rec-evt-hd">
                      <span className="mono cc-dim">{e.t}</span>
                      <span className={'cc-rec-evt-kind mono kind-' + e.kind}>{e.kind}</span>
                      <span className="mono">{e.text}</span>
                    </div>
                    <div className="mono cc-dim cc-rec-evt-payload">{e.payload}</div>
                    {e.actor && <div className="mono cc-dim cc-rec-evt-actor">actor · {e.actor}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 12. TOKEN ECONOMY DETAIL
// ─────────────────────────────────────────────────────────
function ArtTokenEconomy({ paused, accent }) {
  const tokenTypes = [
    { k: 'input',          v: 1842311, share: 0.42, color: 'var(--r-impl)' },
    { k: 'cached',         v: 1521884, share: 0.34, color: 'var(--r-review)' },
    { k: 'cache-creation', v:  308142, share: 0.07, color: 'var(--accent)' },
    { k: 'output',         v:  611204, share: 0.14, color: 'var(--st-pass)' },
    { k: 'reasoning',      v:  124012, share: 0.03, color: 'var(--st-block)' },
  ];

  const savings = [
    { k: 'Token Savior · context packing',  saved: 942188, before: 4368199, after: 3426011, ratio: 0.216, color: 'var(--accent)' },
    { k: 'Prefix cache hits',                saved: 1521884, before: 3426011, after: 1904127, ratio: 0.444, color: 'var(--r-review)' },
    { k: 'RTK · log filter',                 saved: 218442, before: 1904127, after: 1685685, ratio: 0.115, color: 'var(--r-qa-author)' },
    { k: 'Capsule reuse',                    saved:  92211, before: 1685685, after: 1593474, ratio: 0.055, color: 'var(--st-pass)' },
  ];

  const perCall = [
    { ts: '12:31:04', role: 'implementer', model: 'sonnet-4.5',   in: 18204,  cached: 14118, output: 4022, reasoning: 0,    saved: 12042, cost: '$0.013' },
    { ts: '12:32:11', role: 'reviewer',    model: 'sonnet-4.5',   in: 24112,  cached: 19884, output: 1822, reasoning: 0,    saved: 18221, cost: '$0.009' },
    { ts: '12:33:02', role: 'qa.scrutiny', model: 'sonnet-4.5',   in: 31204,  cached: 22118, output: 3422, reasoning: 0,    saved: 21001, cost: '$0.014' },
    { ts: '12:33:48', role: 'planner',     model: 'opus-4.5',     in: 48211,  cached: 21042, output: 8222, reasoning: 4422, saved: 18482, cost: '$0.062' },
    { ts: '12:34:31', role: 'implementer', model: 'codex-gpt5.4', in: 21008,  cached: 16004, output: 2188, reasoning: 1212, saved: 14882, cost: '$0.011' },
    { ts: '12:35:12', role: 'qa.usertest', model: 'sonnet-4.5',   in: 92142,  cached: 81204, output: 1042, reasoning: 0,    saved: 78211, cost: '$0.022' },
    { ts: '12:36:01', role: 'implementer', model: 'sonnet-4.5',   in: 14112,  cached:  8842, output: 3211, reasoning: 0,    saved:  8124, cost: '$0.011' },
  ];

  const fmt = (n) => n.toLocaleString();

  return (
    <CCApp tab="telemetry" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">TELEMETRY · token economy</div>
            <h2 className="cc-v-title">Where the tokens went, where they didn't</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>five token classes · four savings layers · per-call accounting</p>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <Stat k="tokens · gross" v="4.41M" d={<span className="mono cc-dim">all roles</span>} />
            <Stat k="tokens · net" v="1.59M" d={<span className="cc-accent-ink mono">−64.0%</span>} />
            <Stat k="cost · net" v="$8.86" d={<span className="cc-dim mono">$11.42 nominal</span>} />
          </div>
        </div>

        {/* TOKEN CLASS BREAKDOWN */}
        <div className="cc-panel">
          <PanelHd kicker="TOKEN CLASSES" title="net usage · stacked" action={<span className="mono cc-dim">net = post-savings</span>} />
          <div className="cc-te-bar">
            {tokenTypes.map((t, i) => (
              <span key={i} className="cc-te-seg" style={{ width: (t.share * 100) + '%', background: t.color }} title={t.k + ' · ' + fmt(t.v)} />
            ))}
          </div>
          <div className="cc-te-legend">
            {tokenTypes.map((t, i) => (
              <div key={i} className="cc-te-legend-item">
                <span className="cc-te-swatch" style={{ background: t.color }} />
                <span className="mono cc-te-legend-k">{t.k}</span>
                <span className="mono cc-te-legend-v">{fmt(t.v)}</span>
                <span className="mono cc-dim">{(t.share * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>

        {/* SAVINGS WATERFALL */}
        <div className="cc-panel">
          <PanelHd kicker="SAVINGS" title="waterfall · gross → net" action={<span className="mono cc-dim">−64.0% · 2.82M tokens saved</span>} />
          <div className="cc-te-water">
            <div className="cc-te-water-row cc-te-water-base">
              <span className="mono cc-te-water-k">gross</span>
              <div className="cc-te-water-track"><span className="cc-te-water-fill" style={{ width: '100%', background: 'var(--panel-3)' }} /></div>
              <span className="mono cc-te-water-v">4,368,199</span>
            </div>
            {savings.map((s, i) => {
              const pre  = (s.before / 4368199) * 100;
              const post = (s.after  / 4368199) * 100;
              return (
                <div key={i} className="cc-te-water-row">
                  <span className="mono cc-te-water-k">{s.k}</span>
                  <div className="cc-te-water-track">
                    <span className="cc-te-water-fill" style={{ width: post + '%', background: 'var(--panel-3)' }} />
                    <span className="cc-te-water-cut" style={{ left: post + '%', width: (pre - post) + '%', background: s.color, opacity: 0.65 }} />
                  </div>
                  <span className="mono cc-te-water-v">−{fmt(s.saved)} <span className="cc-dim">({(s.ratio * 100).toFixed(1)}%)</span></span>
                </div>
              );
            })}
            <div className="cc-te-water-row cc-te-water-net">
              <span className="mono cc-te-water-k">net</span>
              <div className="cc-te-water-track"><span className="cc-te-water-fill" style={{ width: '36%', background: 'var(--accent)' }} /></div>
              <span className="mono cc-te-water-v cc-accent-ink">1,593,474</span>
            </div>
          </div>
        </div>

        {/* PER-CALL TABLE */}
        <div className="cc-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 280 }}>
          <PanelHd kicker="PER-CALL" title="last 7 model calls" action={<span className="mono cc-dim">cached · output · reasoning · saved</span>} />
          <table className="cc-table cc-te-table">
            <thead>
              <tr>
                <th>ts</th><th>role</th><th>model</th>
                <th style={{ textAlign: 'right' }}>input</th>
                <th style={{ textAlign: 'right' }}>cached</th>
                <th style={{ textAlign: 'right' }}>output</th>
                <th style={{ textAlign: 'right' }}>reasoning</th>
                <th style={{ textAlign: 'right' }}>saved</th>
                <th style={{ textAlign: 'right' }}>cost</th>
              </tr>
            </thead>
            <tbody>
              {perCall.map((r, i) => (
                <tr key={i}>
                  <td className="mono cc-dim">{r.ts}</td>
                  <td><RoleBadge role={r.role} compact /></td>
                  <td className="mono cc-dim">{r.model}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>{fmt(r.in)}</td>
                  <td className="mono cc-dim" style={{ textAlign: 'right' }}>{fmt(r.cached)}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>{fmt(r.output)}</td>
                  <td className="mono cc-dim" style={{ textAlign: 'right' }}>{r.reasoning ? fmt(r.reasoning) : '—'}</td>
                  <td className="mono cc-accent-ink" style={{ textAlign: 'right' }}>−{fmt(r.saved)}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>{r.cost}</td>
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
// 13. EMPTY / ERROR STATES
// ─────────────────────────────────────────────────────────
function ArtStates({ paused, accent }) {
  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">EMPTY · DISCONNECTED · ERROR</div>
            <h2 className="cc-v-title">States the operator console must show plainly</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>fail closed · be loud · never mask the realtime substrate state</p>
          </div>
        </div>

        <div className="cc-state-grid">
          {/* No features */}
          <div className="cc-state">
            <div className="cc-kicker mono">EMPTY · no features</div>
            <div className="cc-state-stage">
              <div className="cc-state-illus"><div className="cc-state-cell" /><div className="cc-state-cell" /><div className="cc-state-cell" /><div className="cc-state-cell" /></div>
              <div className="cc-state-title">No features yet</div>
              <div className="cc-state-desc">Register a project and create a feature goal to start an autonomous run.</div>
              <div className="cc-state-cmd mono">$ pgloom-engineering feature create --project lvc</div>
            </div>
          </div>

          {/* WS reconnecting */}
          <div className="cc-state cc-state-warn">
            <div className="cc-kicker mono">REALTIME · reconnecting</div>
            <div className="cc-state-stage">
              <div className="cc-state-pulse cc-pulse" />
              <div className="cc-state-title">WebSocket dropped — reconnecting</div>
              <div className="cc-state-desc">Live updates paused. The next handshake replays missed events; a <span className="mono">resync</span> hint forces a refetch if the queue overflowed.</div>
              <div className="cc-state-meta mono">attempt 2 · backoff 2.0s · last event 00:00:08 ago</div>
            </div>
          </div>

          {/* Resync hint */}
          <div className="cc-state cc-state-info">
            <div className="cc-kicker mono">REALTIME · resync hint</div>
            <div className="cc-state-stage">
              <div className="cc-state-banner mono"><span>{'{ "kind": "resync", "reason": "queue overflow on ws#3" }'}</span></div>
              <div className="cc-state-title">Refetched the open feature</div>
              <div className="cc-state-desc">SWR caches mutated from canonical REST. No data was lost — NOTIFY fan-out drops oldest before stalling the bridge.</div>
            </div>
          </div>

          {/* Loopback rejection */}
          <div className="cc-state cc-state-fail">
            <div className="cc-kicker mono">AUTH · 403 non-loopback</div>
            <div className="cc-state-stage">
              <div className="cc-state-code mono">HTTP/1.1 403 Forbidden</div>
              <div className="cc-state-title">Refused: peer is not 127.0.0.1 or ::1</div>
              <div className="cc-state-desc">v1 binds loopback only. The middleware re-checks <span className="mono">request.client.host</span> in case a reverse proxy is misconfigured.</div>
              <div className="cc-state-cmd mono">denied · 10.0.4.218 · 2026-05-10T14:08:22Z</div>
            </div>
          </div>

          {/* No DAG yet */}
          <div className="cc-state">
            <div className="cc-kicker mono">DAG · plan not consolidated</div>
            <div className="cc-state-stage">
              <div className="cc-state-skeleton">
                <span /><span /><span /><span /><span /><span />
              </div>
              <div className="cc-state-title">Planner council still deliberating</div>
              <div className="cc-state-desc">No PlanContract yet; the DAG renders as soon as <span className="mono">plan.update</span> arrives with status=consolidated.</div>
            </div>
          </div>

          {/* Paused */}
          <div className="cc-state cc-state-pause">
            <div className="cc-kicker mono">FEATURE · paused</div>
            <div className="cc-state-stage">
              <div className="cc-state-pause-mark mono">‖‖</div>
              <div className="cc-state-title">Dispatch frozen</div>
              <div className="cc-state-desc">Pre-gate refuses new claims. In-flight runs continue to natural completion or failure — pause does not hard-kill.</div>
              <div className="cc-state-meta mono">last intervention · 14:02:18 · op_josh@local</div>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtRecovery, ArtTokenEconomy, ArtStates });
