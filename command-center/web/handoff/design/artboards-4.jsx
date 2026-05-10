// artboards-4.jsx — qa-usertest slot occupancy + full_app_run resource locks

function ArtSlots({ paused, accent, pulse }) {
  // qa-usertest slots: 4 max
  const slots = [
    { i: 0, state: 'leased', project: 'lvc',  feature: 'wf_03418122', task: 'task_44', for: '14m 22s', port: 8421, db: 'usertest_lvc_8421' },
    { i: 1, state: 'leased', project: 'trp',  feature: 'wf_034128b3', task: 'task_19', for: '06m 41s', port: 8422, db: 'usertest_trp_8422' },
    { i: 2, state: 'idle',   project: null,   feature: null,          task: null,      for: '—',      port: 8423, db: '—' },
    { i: 3, state: 'leased', project: 'jmh',  feature: 'wf_034176ab', task: 'task_07', for: '01m 12s', port: 8424, db: 'usertest_jmh_8424' },
  ];

  // full_app_run per-project locks (project-scoped)
  const projectLocks = [
    { project: 'lvc', held_by: 'wf_03418122 · task_44', held_for: '14m 22s', queue: [
      { feature: 'wf_03418fa1', task: 'task_61', wait: '08m 14s', priority: 'normal' },
      { feature: 'wf_03418122', task: 'task_47', wait: '02m 31s', priority: 'normal' },
    ]},
    { project: 'trp', held_by: 'wf_034128b3 · task_19', held_for: '06m 41s', queue: [] },
    { project: 'jmh', held_by: 'wf_034176ab · task_07', held_for: '01m 12s', queue: [
      { feature: 'wf_034176ab', task: 'task_09', wait: '00m 38s', priority: 'normal' },
    ]},
    { project: 'mlp', held_by: null, held_for: null, queue: [] },
    { project: 'rng', held_by: null, held_for: null, queue: [] },
  ];

  // queue history strip — last 60 minutes by project
  const history = [
    { project: 'lvc', bars: [.2,.4,.5,.3,.6,.7,.9,1,1,1,.8,.7,.6,.6,.7,.8,.9,1,1,.9,.8,.7,.6,.7,.8,.9,1,.9,.8,.7] },
    { project: 'trp', bars: [.0,.0,.1,.2,.3,.3,.4,.4,.5,.5,.4,.5,.6,.5,.4,.3,.2,.1,.0,.0,.1,.3,.4,.5,.6,.6,.5,.4,.3,.3] },
    { project: 'jmh', bars: [.5,.6,.6,.7,.4,.3,.2,.1,.0,.0,.0,.2,.4,.5,.6,.4,.2,.1,.0,.1,.2,.3,.4,.3,.2,.1,.1,.2,.3,.3] },
    { project: 'mlp', bars: [.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0] },
    { project: 'rng', bars: [.0,.1,.0,.0,.1,.0,.0,.0,.0,.0,.0,.0,.0,.1,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0,.0] },
  ];

  return (
    <CCApp tab="telemetry" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">qa.verify.usertest · resource locks</div>
            <h2 className="cc-v-title">Slot occupancy &amp; full_app_run leases</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>per-project lock serializes same-project app runs · different-project tests run in parallel</p>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <Stat k="slots" v="3 / 4" d={<span className="cc-dim mono">1 idle</span>} />
            <Stat k="locks held" v="3 / 5" d={<span className="cc-dim mono">2 free</span>} />
            <Stat k="queued" v="3" d={<span className="mono cc-dim">avg wait 03m 47s</span>} />
          </div>
        </div>

        {/* SLOTS */}
        <div className="cc-panel">
          <PanelHd kicker="qa-usertest slots" title="lease window · max 4" action={<span className="mono cc-dim">queue → lease → model → verification</span>} />
          <div className="cc-slots">
            {slots.map((s) => (
              <div key={s.i} className={'cc-slot ' + (s.state === 'leased' ? 'is-leased' : 'is-idle')}>
                <div className="cc-slot-hd">
                  <span className="mono cc-slot-num">slot #{s.i}</span>
                  <span className={'cc-slot-state mono ' + (s.state === 'leased' ? 'st-run' : 'st-idle')}>
                    <i className={pulse && s.state === 'leased' ? 'cc-pulse' : ''} />
                    {s.state}
                  </span>
                </div>
                {s.state === 'leased' ? (
                  <>
                    <div className="cc-slot-row"><span className="cc-dim">project</span><span className="mono cc-accent-ink">{s.project}</span></div>
                    <div className="cc-slot-row"><span className="cc-dim">feature</span><span className="mono">{s.feature}</span></div>
                    <div className="cc-slot-row"><span className="cc-dim">task</span><span className="mono">{s.task}</span></div>
                    <div className="cc-slot-row"><span className="cc-dim">port</span><span className="mono">{s.port}</span></div>
                    <div className="cc-slot-row"><span className="cc-dim">db</span><span className="mono">{s.db}</span></div>
                    <div className="cc-slot-foot">
                      <span className="mono cc-dim">held for</span>
                      <span className="mono">{s.for}</span>
                    </div>
                  </>
                ) : (
                  <div className="cc-slot-empty mono">awaiting claim</div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* PER-PROJECT full_app_run LOCKS */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 14, flex: 1, minHeight: 0 }}>
          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="full_app_run · per-project mutex" title="who holds the lock · who's waiting" />
            <div className="cc-lock-list cc-scroll">
              {projectLocks.map((p) => (
                <div key={p.project} className={'cc-lock ' + (p.held_by ? 'is-held' : 'is-free')}>
                  <div className="cc-lock-hd">
                    <div className="cc-lock-key">
                      <span className={'cc-lock-dot ' + (p.held_by ? (pulse ? 'cc-pulse' : '') : 'is-free')} />
                      <span className="mono cc-lock-proj">project · {p.project}</span>
                    </div>
                    <span className="mono cc-dim cc-lock-state">{p.held_by ? 'HELD' : 'FREE'}</span>
                  </div>
                  {p.held_by ? (
                    <div className="cc-lock-body">
                      <div className="cc-lock-line"><span className="cc-dim">held by</span><span className="mono">{p.held_by}</span><span className="mono cc-dim">· {p.held_for}</span></div>
                      {p.queue.length > 0 ? (
                        <div className="cc-lock-queue">
                          <span className="cc-kicker mono">queue · {p.queue.length}</span>
                          {p.queue.map((q, i) => (
                            <div key={i} className="cc-lock-q">
                              <span className="mono cc-dim">{String(i + 1).padStart(2, '0')}</span>
                              <span className="mono">{q.feature}</span>
                              <span className="mono cc-dim">·</span>
                              <span className="mono">{q.task}</span>
                              <span className="mono cc-lock-wait">waiting {q.wait}</span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="cc-lock-empty mono">queue empty</div>
                      )}
                    </div>
                  ) : (
                    <div className="cc-lock-empty mono">free · next claim acquires immediately</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="contention · last 60 min" title="queue depth by project" action={<span className="mono cc-dim">10s buckets</span>} />
            <div className="cc-contention cc-scroll">
              {history.map((h) => (
                <div key={h.project} className="cc-contention-row">
                  <span className="mono cc-contention-key">{h.project}</span>
                  <div className="cc-contention-bars">
                    {h.bars.map((v, i) => (
                      <span key={i} className="cc-contention-bar" style={{ height: Math.max(2, v * 28) + 'px', opacity: v < 0.05 ? 0.18 : 0.55 + v * 0.45 }} />
                    ))}
                  </div>
                  <span className="mono cc-dim cc-contention-now">{Math.round(h.bars[h.bars.length - 1] * 4)}q</span>
                </div>
              ))}
              <div className="cc-contention-axis mono cc-dim">
                <span>-60m</span><span>-30m</span><span>now</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtSlots });
