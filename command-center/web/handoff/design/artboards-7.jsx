// artboards-7.jsx — Tasks views.
// (a) ArtTasksList — per-feature tasks browser, grouped by milestone.
// (b) ArtTaskView  — canonical task detail screen (§8a of brief).

// ─────────────────────────────────────────────────────────
// Helpers — derive run rollups + handoffs touching a task.
// ─────────────────────────────────────────────────────────
function _runsForTask(taskId) { return window.CC_RUNS.filter((r) => r.task === taskId); }
function _handoffsForTask(taskId) {
  return window.CC_HANDOFFS.filter((h) => h.from === taskId || h.to === taskId);
}
function _rollupTask(taskId) {
  const runs = _runsForTask(taskId);
  const sum = (k) => runs.reduce((a, r) => a + (r[k] || 0), 0);
  const wall = runs.reduce((a, r) => {
    Object.keys(r.wall || {}).forEach((k) => { a[k] = (a[k] || 0) + r.wall[k]; });
    return a;
  }, {});
  return {
    runs: runs.length,
    cost: sum('cost'),
    tok_in: sum('tok_in'),
    tok_cached: sum('tok_cached'),
    tok_out: sum('tok_out'),
    tok_reason: sum('tok_reason'),
    wall,
    last_started: runs[0]?.started || null,
  };
}

// ─────────────────────────────────────────────────────────
// 9. TASKS LIST — per-feature browser, grouped by milestone
// ─────────────────────────────────────────────────────────
function ArtTasksList({ paused, accent, pulse }) {
  const tasks = window.CC_TASKS;
  const meta = window.CC_TASK_META;
  const ms = window.CC_MILESTONES;

  const counts = tasks.reduce((a, t) => { a[t.status] = (a[t.status] || 0) + 1; a.all++; return a; }, { all: 0 });
  const grouped = ms.map((m) => ({ ...m, tasks: tasks.filter((t) => t.ms === m.id) }));
  const supseded = tasks.filter((t) => t.status === 'superseded');

  return (
    <CCApp tab="tasks" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll cc-tl-pane">
        {/* filter bar */}
        <div className="cc-tl-bar">
          <div className="cc-tl-search">
            <span className="cc-tl-search-ico">{ICONS.search}</span>
            <input className="mono" placeholder="filter task_id, role, label, blocker_code…" defaultValue="" />
            <span className="mono cc-dim cc-tl-search-kbd">/</span>
          </div>
          <div className="cc-tl-chips">
            <span className="cc-kicker mono cc-dim" style={{ marginRight: 6 }}>STATUS</span>
            <span className="cc-chip is-on mono">all <b>{counts.all}</b></span>
            <span className="cc-chip mono"><i className="cc-dot st-run cc-pulse" /> running <b>{counts.running || 0}</b></span>
            <span className="cc-chip mono"><i className="cc-dot st-pass" /> passed <b>{counts.passed || 0}</b></span>
            <span className="cc-chip mono"><i className="cc-dot st-fail" /> failed <b>{counts.failed || 0}</b></span>
            <span className="cc-chip mono"><i className="cc-dot st-block" /> blocked <b>{counts.blocked || 0}</b></span>
            <span className="cc-chip mono"><i className="cc-dot st-queue" /> queued <b>{counts.queued || 0}</b></span>
          </div>
          <div className="cc-tl-chips" style={{ marginLeft: 'auto' }}>
            <span className="cc-kicker mono cc-dim" style={{ marginRight: 6 }}>ROLE</span>
            {['planner','implementer','reviewer','qa.author','qa.verify.scrutiny','qa.verify.usertest','recovery'].map((r) => (
              <span key={r} className="cc-tl-rolechip"><RoleBadge role={r} /></span>
            ))}
          </div>
        </div>

        {/* table */}
        <div className="cc-tl-tbl-wrap">
          <table className="cc-table cc-tl-tbl">
            <thead>
              <tr>
                <th style={{ width: 24 }}></th>
                <th style={{ width: 96 }}>task_id</th>
                <th style={{ width: 56 }}>role</th>
                <th>label</th>
                <th style={{ width: 92 }}>status</th>
                <th style={{ width: 72, textAlign: 'right' }}>attempts</th>
                <th style={{ width: 64, textAlign: 'right' }}>repairs</th>
                <th style={{ width: 100, textAlign: 'right' }}>±loc</th>
                <th style={{ width: 84, textAlign: 'right' }}>cost</th>
                <th style={{ width: 200 }}>last blocker</th>
                <th style={{ width: 24 }}></th>
              </tr>
            </thead>
            <tbody>
              {grouped.map((m) => (
                <React.Fragment key={m.id}>
                  <tr className="cc-tl-grp">
                    <td colSpan={11}>
                      <span className="cc-tl-grp-chev">▾</span>
                      <span className="mono cc-tl-grp-id">{m.id.toUpperCase()}</span>
                      <span className="cc-tl-grp-label">{m.label}</span>
                      {m.signed
                        ? <StatusPill status="signed" />
                        : (m.id === 'm2' ? <StatusPill status="running" label="IN PROGRESS" /> : <StatusPill status="queued" />)}
                      <span className="cc-tl-grp-counts mono cc-dim">{m.tasks.length} tasks · {m.tasks.filter((t) => t.status === 'passed').length} passed · {m.tasks.filter((t) => t.status === 'failed' || t.status === 'blocked').length} red</span>
                    </td>
                  </tr>
                  {m.tasks.map((t) => {
                    const md = meta[t.id] || {};
                    const isSel = t.id === 'task_44';
                    return (
                      <tr key={t.id} className={'cc-tl-row ' + (isSel ? 'is-selected' : '')}>
                        <td className="cc-tl-cell-rail"></td>
                        <td className="mono cc-tl-id">{t.id}{isSel && <span className="cc-tl-sel-tag mono">selected</span>}</td>
                        <td><RoleBadge role={t.role} /></td>
                        <td className="cc-tl-label">{t.label}</td>
                        <td><StatusPill status={t.status} /></td>
                        <td className="mono num cc-tl-num">{t.attempts || '—'}</td>
                        <td className="mono num cc-tl-num">{md.repairs ? <span className="cc-tl-rep">{md.repairs}</span> : <span className="cc-dim">0</span>}</td>
                        <td className="mono num cc-tl-num">
                          {md.loc_add || md.loc_del
                            ? <><b style={{ color: 'var(--st-pass)' }}>+{md.loc_add}</b><span className="cc-dim"> / </span><b style={{ color: 'var(--st-fail)' }}>−{md.loc_del}</b></>
                            : <span className="cc-dim">—</span>}
                        </td>
                        <td className="mono num cc-tl-num"><CostCell micros={t.cost} dim={!t.cost} /></td>
                        <td className="cc-tl-blocker">
                          {md.last_blocker
                            ? <><span className="mono cc-tl-bcode">{md.last_blocker.code}</span> <span className="mono cc-dim">{md.last_blocker.at}</span></>
                            : <span className="cc-dim mono">—</span>}
                        </td>
                        <td><span className="cc-tl-arrow">{ICONS.chev}</span></td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              ))}
              {/* superseded — collapsed group */}
              <tr className="cc-tl-grp is-coll">
                <td colSpan={11}>
                  <span className="cc-tl-grp-chev">▸</span>
                  <span className="mono cc-tl-grp-id">SUPERSEDED</span>
                  <span className="cc-tl-grp-label">Replaced by re-plan from m2</span>
                  <span className="cc-tl-grp-counts mono cc-dim">{supseded.length} task{supseded.length === 1 ? '' : 's'} · hidden by default</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="cc-tl-foot mono cc-dim">
          {tasks.length} tasks · grouped by milestone · click a row to open task view · ↑↓ navigate · ⏎ open · /scope <span className="mono">role:qa.verify.scrutiny status:running</span>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 10. TASK VIEW — §8a canonical task detail
// ─────────────────────────────────────────────────────────
function ArtTaskView({ paused, accent, pulse }) {
  const task = window.CC_TASKS.find((t) => t.id === 'task_44');
  const md = window.CC_TASK_META[task.id];
  const contract = window.CC_TASK_CONTRACT_44;
  const signoffs = window.CC_TASK_SIGNOFFS_44;
  const recovery = window.CC_TASK_RECOVERY_44;
  const sr = window.CC_TASK_SELFREPAIR_44;
  const intvs = window.CC_TASK_INTERVENTIONS_44;
  const arts = window.CC_TASK_ARTIFACTS_44;
  const handoffs = _handoffsForTask(task.id);
  const runs = _runsForTask(task.id);
  const roll = _rollupTask(task.id);

  const last = md.last_blocker;

  return (
    <CCApp tab="tasks" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll cc-tv-pane">

        {/* ── header ─────────────────────────────────── */}
        {paused && (
          <div className="cc-banner cc-banner-pause">
            <span className="cc-banner-tag mono">PAUSED</span>
            <div>Feature paused by <span className="mono">op_josh</span> · 12:24:01Z · "Holding for design review." · workers will not pick this task up until resumed</div>
          </div>
        )}

        <div className="cc-hero cc-tv-hero">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="cc-kicker mono">TASK · {contract.role} · {task.ms.toUpperCase()}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <h2 className="cc-hero-title cc-tv-title"><span className="mono">{task.id}</span> · {task.label}</h2>
              <StatusPill status={task.status} />
            </div>
            <div className="cc-hero-meta cc-tv-meta">
              <span><span className="cc-dim mono">milestone</span> <span className="mono">{contract.milestone_id}</span></span>
              <span><span className="cc-dim mono">contract.v</span> <span className="mono">{contract.contract_version}</span></span>
              <span><span className="cc-dim mono">input_hash</span> <span className="mono cc-tv-hash">{contract.input_contract_hash}</span> {ICONS.copy}</span>
              <span><span className="cc-dim mono">attempts</span> <span className="mono">{task.attempts}</span> <span className="cc-dim">/ {contract.expected_outputs.scrutiny_attempts_max}</span></span>
              <span><span className="cc-dim mono">repairs</span> <span className="mono">{md.repairs}</span> <span className="cc-dim">/ {contract.budget.repairs_max}</span></span>
              <span><span className="cc-dim mono">authored_by</span> <RoleBadge role={contract.authored_by} /></span>
              <span><span className="cc-dim mono">signoff_policy</span> <span className="mono">{contract.signoff_policy}</span></span>
            </div>
          </div>
          <div className="cc-hero-r">
            <button className="cc-btn cc-btn-ghost">Open contract</button>
            <button className="cc-btn cc-btn-ghost">Comment…</button>
            <button className="cc-btn cc-btn-danger">Drop &amp; replan…</button>
            <button className="cc-btn cc-btn-primary">Retry attempt 3</button>
          </div>
        </div>

        {/* ── stat row ───────────────────────────────── */}
        <div className="cc-stat-row cc-tv-stats">
          <Stat k="cumulative cost" v={fmtUSD(roll.cost, 2)}      d={<span className="cc-dim">{(roll.cost / contract.budget.cost_micros_max * 100).toFixed(0)}% of budget · {fmtUSD(contract.budget.cost_micros_max, 2)} cap</span>} />
          <Stat k="tokens (in)"     v={fmtTokens(roll.tok_in)}    d={<><span className="cc-tok-cached">{Math.round(roll.tok_cached / roll.tok_in * 100)}% cached</span><span className="cc-dim"> · {fmtTokens(roll.tok_cached)} hit</span></>} />
          <Stat k="tokens (out)"    v={fmtTokens(roll.tok_out)}   d={<><span className="cc-tok-out">{fmtTokens(roll.tok_out)}</span><span className="cc-tok-reason"> · {fmtTokens(roll.tok_reason)} reason</span></>} />
          <Stat k="wall-clock"      v={fmtSecs(Object.values(roll.wall).reduce((a, b) => a + b, 0))} d={<span className="cc-dim">{runs.length} runs · {(roll.wall.model || 0)}s model</span>} />
          <Stat k="last attempt"    v={<span className="mono">{roll.last_started}</span>} d={<span className="cc-dim">run {runs[0]?.id} · attempt {runs[0]?.attempt}</span>} />
          <Stat k="last blocker_code" v={last ? <span className="mono cc-tv-blockcode">{last.code}</span> : <span className="cc-dim mono">—</span>}
                d={last ? <span className="mono cc-dim">{last.at} · run {runs.find((r) => r.status === 'failed')?.id || '—'}</span> : <span className="cc-dim">no failure on record</span>} />
        </div>

        {/* ── body grid ──────────────────────────────── */}
        <div className="cc-tv-grid">

          {/* left col */}
          <div className="cc-tv-col">
            {/* contract pane */}
            <div className="cc-panel">
              <PanelHd kicker="CONTRACT" title="task_contract.json" count={`v${contract.contract_version}`}
                action={<div style={{ display: 'flex', gap: 6 }}>
                  <span className="cc-chip is-on mono">collapsed</span>
                  <span className="cc-chip mono">expanded</span>
                  <span className="cc-chip mono">diff vs v1</span>
                </div>} />
              <pre className="cc-tv-contract mono">{`{
  "task_id": "task_44",
  "feature_id": "${contract.feature_id}",
  "milestone_id": "${contract.milestone_id}",
  "contract_version": ${contract.contract_version},
  "input_contract_hash": "${contract.input_contract_hash}",
  "role": "${contract.role}",
  "signoff_policy": "${contract.signoff_policy}",
  "authored_by": "${contract.authored_by}",
  "inputs": {
    "review_handoff_id": "${contract.inputs.review_handoff_id}",
    "review_decision":   "${contract.inputs.review_decision}",
    "diff_loc":          ${contract.inputs.diff_loc},
    "files_under_review":${contract.inputs.files_under_review},
    "seed_corpus":       "${contract.inputs.seed_corpus}"
  },
  "expected_outputs": {
    "scrutiny_attempts_min": ${contract.expected_outputs.scrutiny_attempts_min},
    "scrutiny_attempts_max": ${contract.expected_outputs.scrutiny_attempts_max},
    "must_break_at_least":   ${contract.expected_outputs.must_break_at_least},
    "blocker_codes_handled": [
      "engineering.qa_semantic_quality_failed",
      "engineering.qa_verify_failed",
      "engineering.qa_tests_not_red"
    ],
    "artifacts": ["scrutiny_log", "fuzzed_inputs", "red_proof_diff"]
  },
  "validators": [
    { "id": "authored-by", "rule": "authored_by ≠ implementer.task_42" },
    { "id": "red-proof",   "rule": "attempt yields ≥1 failing test" },
    { "id": "fence",       "rule": "fuzz includes split-on-cursor" }
  ],
  "budget": { "wall_seconds_max": 600, "cost_micros_max": 1200000, "repairs_max": 3 },
  "handoff_in":  "${contract.handoff_in}",
  "handoff_out": ["h_803", "h_804"]
}`}</pre>
            </div>

            {/* worker runs timeline */}
            <div className="cc-panel">
              <PanelHd kicker="WORKER RUNS" title="Timeline · this task" count={`${runs.length} runs · attempt 2 of 4`}
                action={<span className="mono cc-dim">live · cc_events</span>} />
              <div className="cc-tv-runs">
                {runs.map((r, i) => (
                  <div key={r.id} className={'cc-tv-run is-' + r.status}>
                    <div className="cc-tv-run-rail">
                      <span className={'cc-tv-run-dot st-' + (r.status === 'running' ? 'run cc-pulse' : r.status === 'passed' ? 'pass' : 'fail')} />
                      {i < runs.length - 1 && <span className="cc-tv-run-line" />}
                    </div>
                    <div className="cc-tv-run-card">
                      <div className="cc-tv-run-hd">
                        <span className="mono cc-tv-run-id">run {r.id}</span>
                        <StatusPill status={r.status} />
                        <span className="mono cc-dim">attempt {r.attempt}</span>
                        <span className="mono cc-dim">{r.model}</span>
                        <span className="mono cc-dim" style={{ marginLeft: 'auto' }}>started {r.started}</span>
                      </div>
                      <div className="cc-tv-run-body">
                        <div className="cc-tv-run-stats">
                          <KV k="cost"    v={<CostCell micros={r.cost} />} mono />
                          <KV k="tok in"  v={<TokenCell value={r.tok_in} />} mono />
                          <KV k="cached"  v={<TokenCell value={r.tok_cached} kind="cached" />} mono />
                          <KV k="tok out" v={<TokenCell value={r.tok_out} kind="output" />} mono />
                          <KV k="reason"  v={<TokenCell value={r.tok_reason} kind="reasoning" />} mono />
                        </div>
                        <WallClockBar split={r.wall} height={6} />
                      </div>
                      {r.status === 'failed' && (
                        <div className="cc-tv-run-foot mono">
                          <span className="cc-tv-bk">blocker_code</span>
                          <span className="mono cc-tv-blockcode">engineering.qa_semantic_quality_failed</span>
                          <span className="cc-dim">→ self-repair sr_31</span>
                          <span className="cc-dim" style={{ marginLeft: 'auto' }}>logs · diff · junit</span>
                        </div>
                      )}
                      {r.status === 'running' && (
                        <div className="cc-tv-run-foot mono">
                          <i className="cc-pulse" style={{ background: 'var(--st-run)', width: 6, height: 6, borderRadius: 3, display: 'inline-block' }} />
                          <span>now: <span className="cc-tv-running-now">fuzz · symmetric reverse iter</span></span>
                          <span className="cc-dim">142 / 200 cases</span>
                          <span className="cc-dim" style={{ marginLeft: 'auto' }}>→ h_804 (recovery_request, open)</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* right col */}
          <div className="cc-tv-col">
            {/* handoffs in/out */}
            <div className="cc-panel">
              <PanelHd kicker="HANDOFFS" title="In / out" count={`${handoffs.length}`} />
              <div className="cc-tv-ho">
                {handoffs.map((h) => {
                  const dir = h.from === task.id ? 'out' : 'in';
                  return (
                    <div key={h.id} className={'cc-tv-ho-row dir-' + dir}>
                      <span className={'cc-tv-ho-dir mono'}>{dir === 'in' ? '◂ IN' : 'OUT ▸'}</span>
                      <span className="mono cc-tv-ho-id">{h.id}</span>
                      <span className="cc-tv-ho-pair">
                        <RoleBadge role={h.from_role} />
                        <span className="cc-dim mono">→</span>
                        <RoleBadge role={h.to_role} />
                      </span>
                      <span className="mono cc-tv-ho-kind cc-dim">{h.kind}</span>
                      <span className="cc-tv-ho-r">
                        <StatusPill status={h.status === 'open' ? 'running' : 'passed'} />
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* qa signoffs */}
            <div className="cc-panel">
              <PanelHd kicker="QA SIGNOFFS" title="scrutiny_and_usertest · both required" />
              <div className="cc-tv-sigs">
                {signoffs.map((s) => (
                  <div key={s.id} className={'cc-tv-sig is-' + s.state}>
                    <div className="cc-tv-sig-hd">
                      <span className="mono cc-tv-sig-id">{s.id}</span>
                      <span className={'cc-tv-sig-type mono v-' + s.validator_type}>{s.validator_type}</span>
                      <StatusPill status={s.state === 'pending' ? 'pending' : s.state === 'holding' ? 'running' : 'passed'} />
                      <span className="mono cc-dim" style={{ marginLeft: 'auto' }}>{s.attempts} attempt{s.attempts === 1 ? '' : 's'}</span>
                    </div>
                    <div className="cc-tv-sig-body mono cc-dim">{s.last_attempt}</div>
                    <div className="cc-tv-sig-foot mono cc-dim">{s.note}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* recovery actions */}
            <div className="cc-panel">
              <PanelHd kicker="RECOVERY" title="Actions originated by this task" count={recovery.length} />
              <div className="cc-tv-rec">
                {recovery.map((r) => (
                  <div key={r.id} className={'cc-tv-rec-row is-' + r.state}>
                    <div className="cc-tv-rec-l">
                      <div className="cc-tv-rec-hd">
                        <span className="mono">{r.id}</span>
                        <span className="cc-dim mono">→ {r.target_task}</span>
                        <span className="mono cc-tv-blockcode">{r.blocker_code}</span>
                        <StatusPill status={r.state === 'open' ? 'running' : 'passed'} />
                      </div>
                      <div className="cc-tv-rec-body">{r.summary}</div>
                      <div className="cc-tv-rec-files mono cc-dim">{r.file_set.join(' · ')}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* self-repair issues */}
            <div className="cc-panel">
              <PanelHd kicker="SELF-REPAIR" title="Verifier-raised, fixed in-flight" count={sr.length} />
              <div className="cc-tv-sr">
                {sr.map((s) => (
                  <div key={s.id} className="cc-tv-sr-row">
                    <span className="mono cc-tv-sr-id">{s.id}</span>
                    <span className="mono cc-dim cc-tv-sr-from">{s.from}</span>
                    <span className="cc-tv-sr-issue">{s.issue}</span>
                    <span className="cc-tv-sr-arrow mono cc-dim">→</span>
                    <span className="cc-tv-sr-fix mono">{s.fix}</span>
                    <span className="mono cc-dim cc-tv-sr-at">+<CostCell micros={s.delta_cost} /></span>
                  </div>
                ))}
              </div>
            </div>

            {/* interventions touching task */}
            <div className="cc-panel">
              <PanelHd kicker="INTERVENTIONS" title="Operator clicks on this task" count={intvs.length} />
              <div className="cc-tv-int">
                {intvs.map((i) => (
                  <div key={i.id} className="cc-tv-int-row">
                    <span className="mono cc-tv-int-id">#{i.id}</span>
                    <span className="mono cc-dim">{i.ts}</span>
                    <span className="mono cc-tv-int-actor">{i.actor}</span>
                    <span className={'cc-int-kind mono kind-' + i.kind.replace(/_/g, '-')}>{i.kind}</span>
                    <span className="cc-tv-int-note">{i.note}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* artifacts gallery */}
            <div className="cc-panel">
              <PanelHd kicker="ARTIFACTS" title="Run-attached" count={arts.length}
                action={<span className="mono cc-dim">durable · sha-anchored · {(arts.reduce((a, x) => a + x.bytes, 0) / 1024).toFixed(0)} KB</span>} />
              <div className="cc-tv-arts">
                {arts.map((a) => (
                  <div key={a.id} className={'cc-tv-art type-' + a.type + (a.live ? ' is-live' : '')}>
                    <div className="cc-tv-art-thumb">
                      <span className="cc-tv-art-tag mono">.{a.name.split('.').pop()}</span>
                      {a.live && <span className="cc-tv-art-live mono"><i className="cc-pulse" />LIVE</span>}
                    </div>
                    <div className="cc-tv-art-name mono">{a.name}</div>
                    <div className="cc-tv-art-meta mono cc-dim">
                      <span>{a.by}</span><span>{(a.bytes / 1024).toFixed(1)} KB</span><span>{a.at}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── telemetry roll-up ──────────────────────── */}
        <div className="cc-panel cc-tv-roll">
          <PanelHd kicker="TELEMETRY ROLL-UP" title="This task only" action={<span className="mono cc-dim">{runs.length} runs · {fmtSecs(Object.values(roll.wall).reduce((a, b) => a + b, 0))} wall</span>} />
          <div className="cc-tv-roll-body">
            <div className="cc-tv-roll-block">
              <div className="cc-kicker mono">WALL-CLOCK MIX</div>
              <WallClockBar split={roll.wall} height={12} />
            </div>
            <div className="cc-tv-roll-block">
              <div className="cc-kicker mono">COST BY ATTEMPT</div>
              <div className="cc-tv-roll-att">
                {[...runs].reverse().map((r) => {
                  const max = Math.max(...runs.map((x) => x.cost));
                  return (
                    <div key={r.id} className="cc-tv-roll-att-row">
                      <span className="mono cc-dim cc-tv-roll-att-i">a{r.attempt}</span>
                      <span className="cc-tv-roll-att-bar"><span style={{ width: (r.cost / max * 100) + '%', background: r.status === 'failed' ? 'var(--st-fail)' : r.status === 'running' ? 'var(--st-run)' : 'var(--st-pass)' }} /></span>
                      <span className="mono"><CostCell micros={r.cost} /></span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="cc-tv-roll-block">
              <div className="cc-kicker mono">TOKEN MIX</div>
              <SparkStack
                width={220} height={10}
                parts={[
                  { value: roll.tok_cached,                    color: 'var(--tok-cached)', label: 'cached' },
                  { value: roll.tok_in - roll.tok_cached,      color: 'var(--tok-in)',     label: 'fresh in' },
                  { value: roll.tok_reason,                    color: 'var(--tok-reason)', label: 'reasoning' },
                  { value: roll.tok_out,                       color: 'var(--tok-out)',    label: 'output' },
                ]}
              />
              <div className="cc-tv-roll-tokens mono cc-dim">
                <span><i style={{ background: 'var(--tok-cached)' }} /> cached <b>{fmtTokens(roll.tok_cached)}</b></span>
                <span><i style={{ background: 'var(--tok-in)' }} /> fresh in <b>{fmtTokens(roll.tok_in - roll.tok_cached)}</b></span>
                <span><i style={{ background: 'var(--tok-reason)' }} /> reason <b>{fmtTokens(roll.tok_reason)}</b></span>
                <span><i style={{ background: 'var(--tok-out)' }} /> output <b>{fmtTokens(roll.tok_out)}</b></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

// ─────────────────────────────────────────────────────────
// 11. TASK WORKSPACE — operator workbench
// Action-side surface for triaging a single task: context · evidence
// viewer (diff/log/trace) · live model stream · triage actions.
// Contrasts with ArtTaskView (reference-grade reading view).
// ─────────────────────────────────────────────────────────
function ArtTaskWorkspace({ paused, accent, pulse }) {
  const task = window.CC_TASKS.find((t) => t.id === 'task_44');
  const md = window.CC_TASK_META[task.id];
  const d = window.CC_TASK_DETAIL[task.id];
  const runs = _runsForTask(task.id);
  const roll = _rollupTask(task.id);
  const totalWall = Object.values(roll.wall).reduce((a, b) => a + b, 0);
  const costPct = Math.min(100, Math.round(roll.cost / d.contract.budget.cost_micros_max * 100));
  const wallPct = Math.min(100, Math.round(totalWall / d.contract.budget.wall_seconds_max * 100));

  return (
    <CCApp tab="tasks" paused={paused} accent={accent}>
      <div className="cc-pane cc-tw">

        {/* ── compact header strip ───────────────────── */}
        <div className="cc-tw-strip">
          <div className="cc-tw-strip-l">
            <div className="cc-kicker mono">TASK WORKSPACE</div>
            <div className="cc-tw-strip-id">
              <span className="mono">{task.id}</span>
              <RoleBadge role={task.role} full />
              <StatusPill status={task.status} />
            </div>
            <div className="cc-tw-strip-meta mono cc-dim">
              <span>attempt <span className="cc-tw-strong">{task.attempts}</span> / {d.contract.expected_outputs.scrutiny_attempts_max}</span>
              <span>·</span>
              <span>repairs <span className="cc-tw-strong">{md.repairs}</span> / {d.contract.budget.repairs_max}</span>
              <span>·</span>
              <span>contract v{d.contract.contract_version}</span>
              <span>·</span>
              <span>{d.contract.input_contract_hash}</span>
            </div>
          </div>
          <div className="cc-tw-strip-r">
            <button className="cc-btn cc-btn-ghost">Comment…</button>
            <button className="cc-btn cc-btn-ghost">Drop &amp; replan…</button>
            <button className="cc-btn cc-btn-danger">{ICONS.pause} Pause</button>
            <button className="cc-btn cc-btn-primary">{ICONS.play} Retry attempt 3</button>
          </div>
        </div>

        {/* ── 3-col workspace ────────────────────────── */}
        <div className="cc-tw-grid">

          {/* CONTEXT ─────────────────────────────────── */}
          <aside className="cc-tw-ctx">

            <div className="cc-tw-ctx-sect">
              <div className="cc-kicker mono">POSITION · DAG</div>
              <div className="cc-tw-mini">
                {/* mini DAG: deps → this → dependents */}
                <div className="cc-tw-mini-row">
                  <span className="cc-tw-mini-node mono"><RoleBadge role="reviewer" /> task_43</span>
                  <span className="cc-tw-mini-arrow">{ICONS.arrowR}</span>
                </div>
                <div className="cc-tw-mini-self">
                  <span className="cc-tw-mini-node is-self mono"><RoleBadge role={task.role} /> {task.id}</span>
                </div>
                <div className="cc-tw-mini-row">
                  <span className="cc-tw-mini-arrow">{ICONS.arrowR}</span>
                  <span className="cc-tw-mini-node mono"><RoleBadge role="qa.verify.usertest" /> task_45 <span className="cc-dim">QUEUED</span></span>
                </div>
                <div className="cc-tw-mini-row">
                  <span className="cc-tw-mini-arrow">{ICONS.arrowR}</span>
                  <span className="cc-tw-mini-node is-recov mono"><RoleBadge role="recovery" /> task_46 <span className="cc-dim">via h_804</span></span>
                </div>
              </div>
              <div className="cc-tw-ctx-foot mono cc-dim">
                milestone <span className="cc-tw-strong">{task.ms.toUpperCase()}</span> · cross-shard reader · {d.contract.signoff_policy}
              </div>
            </div>

            <div className="cc-tw-ctx-sect">
              <div className="cc-kicker mono">BLOCKER</div>
              <div className="cc-tw-blocker">
                <div className="mono cc-tv-blockcode">{md.last_blocker.code}</div>
                <div className="mono cc-dim">{md.last_blocker.at} · run 9181</div>
                <div className="cc-tw-blocker-desc">Pool starves when 4 concurrent readers contend one shard; lease past 80ms blocks the cursor.</div>
              </div>
            </div>

            <div className="cc-tw-ctx-sect">
              <div className="cc-kicker mono">SIGNOFFS · {d.contract.signoff_policy}</div>
              <div className="cc-tw-sigs">
                {d.signoffs.map((s) => (
                  <div key={s.id} className={'cc-tw-sigrow v-' + s.validator_type}>
                    <span className="mono cc-tw-sig-type">{s.validator_type}</span>
                    <StatusPill status={s.state === 'pending' ? 'pending' : s.state === 'holding' ? 'running' : 'passed'} />
                    <span className="mono cc-dim cc-tw-sig-meta">{s.attempts}× · {s.last_attempt.split('·')[0].trim()}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="cc-tw-ctx-sect">
              <div className="cc-kicker mono">BUDGET</div>
              <div className="cc-tw-budgets">
                <div className="cc-tw-bud">
                  <div className="cc-tw-bud-l mono"><span>cost</span><span className="cc-tw-bud-v">{fmtUSD(roll.cost, 3)} <span className="cc-dim">/ {fmtUSD(d.contract.budget.cost_micros_max, 2)}</span></span></div>
                  <div className="cc-tw-bud-bar"><span style={{ width: costPct + '%', background: costPct > 80 ? 'var(--st-fail)' : costPct > 50 ? 'var(--st-block)' : 'var(--accent)' }} /></div>
                  <div className="cc-tw-bud-pct mono cc-dim">{costPct}% used</div>
                </div>
                <div className="cc-tw-bud">
                  <div className="cc-tw-bud-l mono"><span>wall</span><span className="cc-tw-bud-v">{fmtSecs(totalWall)} <span className="cc-dim">/ {fmtSecs(d.contract.budget.wall_seconds_max)}</span></span></div>
                  <div className="cc-tw-bud-bar"><span style={{ width: wallPct + '%', background: wallPct > 80 ? 'var(--st-fail)' : wallPct > 50 ? 'var(--st-block)' : 'var(--accent)' }} /></div>
                  <div className="cc-tw-bud-pct mono cc-dim">{wallPct}% used · {fmtSecs(d.contract.budget.wall_seconds_max - totalWall)} left</div>
                </div>
              </div>
            </div>

          </aside>

          {/* WORKBENCH ───────────────────────────────── */}
          <main className="cc-tw-work">

            {/* evidence viewer */}
            <div className="cc-tw-evid">
              <div className="cc-tw-evid-tabs">
                <div className="cc-tw-evid-tabs-l">
                  <button className="cc-tw-evid-tab is-active mono">Diff <span className="cc-tw-evid-tab-c">2 files</span></button>
                  <button className="cc-tw-evid-tab mono">Log <span className="cc-tw-evid-tab-c">2</span></button>
                  <button className="cc-tw-evid-tab mono">Trace <span className="cc-tw-evid-tab-c">200 cases</span></button>
                  <button className="cc-tw-evid-tab mono">JUnit</button>
                </div>
                <div className="cc-tw-evid-tabs-r mono cc-dim">attempt 3 · run 9181 · {ICONS.chev}</div>
              </div>

              <div className="cc-tw-evid-body">
                {/* file list */}
                <div className="cc-tw-files">
                  <div className="cc-kicker mono cc-tw-files-h">FILES · 2 changed</div>
                  <button className="cc-tw-file mono is-active">
                    <span className="cc-tw-file-name">src/range/pool.py</span>
                    <span className="cc-tw-file-loc">
                      <b style={{ color: 'var(--st-pass)' }}>+42</b>
                      <b style={{ color: 'var(--st-fail)' }}>−18</b>
                    </span>
                  </button>
                  <button className="cc-tw-file mono">
                    <span className="cc-tw-file-name">tests/range/test_pool_lease_eviction.py</span>
                    <span className="cc-tw-file-loc">
                      <b style={{ color: 'var(--st-pass)' }}>+22</b>
                      <b style={{ color: 'var(--st-fail)' }}>−4</b>
                    </span>
                  </button>
                  <div className="cc-tw-files-h mono cc-dim" style={{ marginTop: 12 }}>FROM RECOVERY · task_46</div>
                  <button className="cc-tw-file mono is-pending">
                    <span className="cc-tw-file-name">src/range/pool.py</span>
                    <span className="mono cc-dim cc-tw-file-loc">awaiting</span>
                  </button>
                </div>

                {/* diff content */}
                <div className="cc-tw-diff">
                  <div className="cc-tw-diff-hd mono cc-dim">@@ -88,12 +88,21 @@ class CursorPool · checkout</div>
                  <pre className="cc-tw-diff-body mono">{`     def checkout(self, shard_id: str, *, lease_ms: int = 80) -> Cursor:
-        cur = self._pool.get(shard_id) or self._open(shard_id)
-        self._leases[shard_id] = time.monotonic_ns() + lease_ms * 1_000_000
-        return cur
+        # Lease eviction: if a slot has been held past its lease, force-release
+        # before granting to the next waiter — prevents 4-reader starvation.
+        now = time.monotonic_ns()
+        held = self._leases.get(shard_id, 0)
+        if held and held < now:
+            try:
+                self._open_cursors[shard_id].cancel()
+            finally:
+                self._open_cursors.pop(shard_id, None)
+                self._leases.pop(shard_id, None)
+        cur = self._open_cursors.get(shard_id) or self._open(shard_id)
+        self._leases[shard_id] = now + lease_ms * 1_000_000
+        return cur
`}</pre>
                </div>
              </div>

              <div className="cc-tw-evid-foot">
                <div className="mono cc-dim">
                  <span className="cc-tw-evid-vfail">×</span> red-proof: <span className="mono">test_pool_lease_eviction · 4-reader contention</span> fails on attempt 3
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                  <button className="cc-btn cc-btn-ghost">Open in editor</button>
                  <button className="cc-btn cc-btn-ghost">Copy patch</button>
                </div>
              </div>
            </div>

            {/* live stream */}
            <div className="cc-tw-live">
              <div className="cc-tw-live-hd">
                <span className={'cc-tw-live-dot ' + (pulse ? 'cc-pulse' : '')} />
                <span className="cc-kicker mono">LIVE · attempt 4 · scrutiny</span>
                <span className="mono cc-dim">run 9182 · claude-sonnet-4.6</span>
                <span className="mono cc-tw-live-progress">142 / 200 cases · 6m 12s</span>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span className="cc-chip mono is-on">tail</span>
                  <span className="cc-chip mono">trace</span>
                  <span className="cc-chip mono">stderr</span>
                </span>
              </div>
              <pre className="cc-tw-live-body mono">{`[12:42:08.412] case 138 · symmetric · reverse_iter(ranges=4, shards=2) → PASS
[12:42:09.001] case 139 · symmetric · empty_shard(ranges=2)             → PASS
[12:42:09.412] case 140 · fence    · split_on_cursor(at=0x3f00)         → PASS
[12:42:10.018] case 141 · load     · 4_readers_contend(window=80ms)
[12:42:10.221]   shard_a · checkout(lease=80ms)                          ok 12ms
[12:42:10.234]   shard_a · checkout(lease=80ms)                          ok 8ms
[12:42:10.298]   shard_a · checkout(lease=80ms)                          ok 18ms
[12:42:10.412]   shard_a · checkout(lease=80ms)                          BLOCK 142ms — lease eviction fired
[12:42:10.560]   shard_a · cursor.cancel()                               ok
[12:42:10.612]   shard_a · checkout()                                    ok 32ms
[12:42:10.701] case 141                                                  → PASS  (was: BLOCKED)
[12:42:11.020] case 142 · load     · 4_readers_contend(window=20ms)      → ...
█`}</pre>
              <div className="cc-tw-live-foot mono cc-dim">
                <span>tokens: <span className="cc-tok cc-tok-cached">144.2k cached</span> · <span className="cc-tok cc-tok-out">3.1k out</span></span>
                <span>·</span>
                <span>cost: <span className="num cc-tw-strong">$0.182</span> so far</span>
                <span>·</span>
                <span>token savior: <span className="cc-tw-strong">−24%</span></span>
                <span style={{ marginLeft: 'auto' }}>auto-scroll ON · ⌘L to lock</span>
              </div>
            </div>

          </main>

          {/* ACTIONS ─────────────────────────────────── */}
          <aside className="cc-tw-act">

            <div className="cc-tw-act-sect">
              <div className="cc-kicker mono">TRIAGE · what's next</div>
              <div className="cc-tw-symptom">
                <span className="mono cc-dim">symptom</span>
                <div className="mono">Scrutiny <b className="cc-tv-blockcode">attempt 3 broke</b> on lease starvation under 4-reader contention. Attempt 4 in progress applying eviction fix.</div>
              </div>

              <div className="cc-tw-decisions">
                <button className="cc-tw-decision is-rec">
                  <div className="cc-tw-decision-hd">
                    <span className="cc-tw-decision-tag mono">RECOMMENDED</span>
                    <span className="cc-tw-decision-label">Let attempt 4 finish</span>
                  </div>
                  <div className="cc-tw-decision-body mono cc-dim">
                    Recovery worker has already opened <span className="mono">h_804 → task_46</span> with the eviction patch. ETA 3m 40s. No operator action needed unless attempt 4 fails again.
                  </div>
                  <div className="cc-tw-decision-foot mono cc-dim">
                    confidence <span className="cc-tw-strong">0.84</span> · qa.scrutiny self-repair history
                  </div>
                </button>
                <button className="cc-tw-decision">
                  <div className="cc-tw-decision-hd">
                    <span className="cc-tw-decision-label">Drop &amp; replan from m2</span>
                  </div>
                  <div className="cc-tw-decision-body mono cc-dim">
                    Mark this task superseded and ask planner to inherit baseline. Use if scrutiny attempts 1–3 all failed to converge.
                  </div>
                </button>
                <button className="cc-tw-decision">
                  <div className="cc-tw-decision-hd">
                    <span className="cc-tw-decision-label">Skip slice · proceed to m3</span>
                  </div>
                  <div className="cc-tw-decision-body mono cc-dim">
                    Bypass scrutiny on this task and let m3 work proceed. Audit row stamped. Use only when blocker is out of scope.
                  </div>
                </button>
              </div>
            </div>

            <div className="cc-tw-act-sect">
              <div className="cc-kicker mono">COMMENT · audit-stamped</div>
              <div className="cc-tw-comment">
                <textarea className="cc-tw-comment-input mono" rows={3} placeholder="Note for the next operator… e.g. 'Watch fence boundary in scrutiny attempt 4'" defaultValue=""></textarea>
                <div className="cc-tw-comment-foot">
                  <span className="mono cc-dim">writes engineering_operator_interventions · kind=comment</span>
                  <button className="cc-btn cc-btn-primary cc-tw-comment-btn">Post comment</button>
                </div>
              </div>
            </div>

            <div className="cc-tw-act-sect">
              <div className="cc-kicker mono">RECENT OPS · on this task</div>
              <div className="cc-tw-ops">
                {d.interventions.slice(-4).map((i) => (
                  <div key={i.id} className="cc-tw-op">
                    <span className="mono cc-dim cc-tw-op-ts">{i.ts}</span>
                    <span className={'cc-int-kind mono kind-' + i.kind.replace(/_/g, '-')}>{i.kind}</span>
                    <span className="mono cc-dim cc-tw-op-actor">{i.actor}</span>
                  </div>
                ))}
                <div className="cc-tw-op">
                  <span className="mono cc-dim cc-tw-op-ts">11:34:18</span>
                  <span className="cc-int-kind mono kind-self-repair">self_repair</span>
                  <span className="mono cc-dim cc-tw-op-actor">qa.scrutiny</span>
                </div>
              </div>
            </div>

          </aside>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtTasksList, ArtTaskView, ArtTaskWorkspace });
