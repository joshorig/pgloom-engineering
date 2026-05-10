// artboards-6.jsx — Project registry: per-project gates, test roots, semantic rules

function ArtRegistry({ paused, accent }) {
  const [active, setActive] = React.useState('lvc');

  const projects = [
    { key: 'lvc', name: 'Linear-Versioned Compaction', repo: 'github.com/pgloom/lvc',  lang: 'rust',   features: 12, last: '2m ago', status: 'healthy' },
    { key: 'trp', name: 'Topological Range Planner',   repo: 'github.com/pgloom/trp',  lang: 'python', features:  7, last: '14m ago', status: 'healthy' },
    { key: 'jmh', name: 'Java Microbench Harness',     repo: 'github.com/pgloom/jmh',  lang: 'java',   features:  4, last: '1h ago',  status: 'degraded' },
    { key: 'mlp', name: 'Multi-Layer Planner',         repo: 'github.com/pgloom/mlp',  lang: 'python', features:  3, last: '3h ago',  status: 'healthy' },
    { key: 'rng', name: 'Range API',                   repo: 'github.com/pgloom/rng',  lang: 'rust',   features:  9, last: '4d ago',  status: 'idle' },
  ];

  const reg = {
    lvc: {
      qa_command: 'cargo test --workspace --no-fail-fast',
      gates: [
        { id: 'fmt',     cmd: 'cargo fmt --check',       avg: '0.4s',  required: true },
        { id: 'clippy',  cmd: 'cargo clippy -- -D warnings', avg: '24s', required: true },
        { id: 'unit',    cmd: 'cargo test --lib',         avg: '38s',  required: true },
        { id: 'doc',     cmd: 'cargo doc --no-deps',      avg: '12s',  required: false },
        { id: 'bench',   cmd: 'cargo bench --no-run',     avg: '22s',  required: false },
      ],
      test_roots: ['crates/lvc-core/tests', 'crates/lvc-shard/tests', 'crates/lvc-fence/tests'],
      endpoints: { app: 'http://127.0.0.1:${port}', health: '/healthz', ready: '/readyz' },
      bench: { tool: 'criterion', dir: 'target/criterion', baseline: 'main' },
      semantic_rules: [
        'reader._merge MUST preserve fence ordering',
        'shard locator returns deterministic shard for input key',
        'no panics inside compaction inner loop (use Result)',
        'memtable flush is single-writer; readers see consistent snapshot',
      ],
    },
    trp: {
      qa_command: 'pytest -xvs',
      gates: [
        { id: 'ruff',     cmd: 'ruff check .',              avg: '1.2s', required: true },
        { id: 'mypy',     cmd: 'mypy trp',                  avg: '14s',  required: true },
        { id: 'pytest',   cmd: 'pytest -xvs',               avg: '42s',  required: true },
        { id: 'coverage', cmd: 'pytest --cov=trp --cov-fail-under=85', avg: '52s', required: false },
      ],
      test_roots: ['tests/unit', 'tests/contract'],
      endpoints: { app: 'http://127.0.0.1:${port}', health: '/healthz' },
      bench: { tool: 'pytest-benchmark', dir: '.benchmarks', baseline: 'main' },
      semantic_rules: [
        'planner output is a topologically-valid DAG',
        'every contract has a verified milestone_id',
        'reasoning fields never cross the JSON boundary',
      ],
    },
    jmh: {
      qa_command: './gradlew check',
      gates: [
        { id: 'spotless', cmd: './gradlew spotlessCheck',  avg: '6s',   required: true },
        { id: 'compile',  cmd: './gradlew compileJava',    avg: '32s',  required: true },
        { id: 'test',     cmd: './gradlew test',           avg: '1m 12s', required: true },
        { id: 'jmh',      cmd: './gradlew jmh',            avg: '3m 04s', required: false },
      ],
      test_roots: ['src/test/java', 'benchmarks/src/jmh/java'],
      endpoints: { app: 'http://127.0.0.1:${port}/api', health: '/api/health' },
      bench: { tool: 'jmh', dir: 'benchmarks/build/results', baseline: 'main' },
      semantic_rules: [
        'JMH benchmarks must declare warmup/measurement explicitly',
        'no shared mutable state between benchmark invocations',
      ],
    },
    mlp: {
      qa_command: 'pytest -xvs',
      gates: [
        { id: 'ruff',   cmd: 'ruff check .',  avg: '0.8s', required: true },
        { id: 'mypy',   cmd: 'mypy mlp',      avg: '11s',  required: true },
        { id: 'pytest', cmd: 'pytest -xvs',   avg: '28s',  required: true },
      ],
      test_roots: ['tests'],
      endpoints: { app: 'http://127.0.0.1:${port}' },
      bench: null,
      semantic_rules: ['planner output is JSON-serializable'],
    },
    rng: {
      qa_command: 'cargo test',
      gates: [
        { id: 'fmt',    cmd: 'cargo fmt --check', avg: '0.3s', required: true },
        { id: 'clippy', cmd: 'cargo clippy',      avg: '18s',  required: true },
        { id: 'test',   cmd: 'cargo test',        avg: '24s',  required: true },
      ],
      test_roots: ['tests'],
      endpoints: { app: 'http://127.0.0.1:${port}' },
      bench: null,
      semantic_rules: ['range queries are inclusive at lower bound, exclusive at upper'],
    },
  };

  const r = reg[active];
  const p = projects.find((x) => x.key === active);

  return (
    <CCApp tab="overview" paused={paused} accent={accent}>
      <div className="cc-pane cc-scroll" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="cc-v-hd">
          <div>
            <div className="cc-kicker mono">PROJECT REGISTRY · docs/evals/project-registry.yaml</div>
            <h2 className="cc-v-title">Per-project metadata · gates · semantic rules</h2>
            <p className="mono cc-dim" style={{ fontSize: 12, marginTop: 4 }}>read by planner · QA author · implementer · validators · dispatch gates</p>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <Stat k="projects" v={String(projects.length)} d={<span className="mono cc-dim">{projects.filter((x) => x.status === 'healthy').length} healthy</span>} />
            <Stat k="active features" v={String(projects.reduce((a, b) => a + b.features, 0))} />
            <Stat k="last sync" v="08:14" d={<span className="mono cc-dim">git pull · 12m ago</span>} />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 14, flex: 1, minHeight: 0 }}>
          {/* PROJECTS LIST */}
          <div className="cc-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <PanelHd kicker="REGISTERED" title={projects.length + ' projects'} />
            <div className="cc-reg-list cc-scroll">
              {projects.map((pj) => (
                <button key={pj.key} className={'cc-reg-item ' + (pj.key === active ? 'is-active' : '')} onClick={() => setActive(pj.key)}>
                  <div className="cc-reg-item-hd">
                    <span className="mono cc-reg-key">{pj.key}</span>
                    <span className={'cc-reg-status mono is-' + pj.status}>{pj.status}</span>
                  </div>
                  <div className="cc-reg-name">{pj.name}</div>
                  <div className="cc-reg-meta mono cc-dim">
                    <span>{pj.lang}</span>
                    <span>·</span>
                    <span>{pj.features} features</span>
                    <span>·</span>
                    <span>{pj.last}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* PROJECT DETAIL */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: 0, overflow: 'auto' }}>
            <div className="cc-panel">
              <PanelHd kicker={'PROJECT · ' + p.key} title={p.name} action={<span className="mono cc-dim">{p.repo}</span>} />
              <div className="cc-reg-keyvals">
                <div className="cc-reg-kv"><span className="cc-kicker mono">QA COMMAND</span><span className="mono cc-reg-cmd">{r.qa_command}</span></div>
                <div className="cc-reg-kv"><span className="cc-kicker mono">APP ENDPOINT</span><span className="mono">{r.endpoints.app}</span></div>
                {r.endpoints.health && <div className="cc-reg-kv"><span className="cc-kicker mono">HEALTH</span><span className="mono">{r.endpoints.health}</span></div>}
                {r.bench && <div className="cc-reg-kv"><span className="cc-kicker mono">BENCHMARK</span><span className="mono">{r.bench.tool} · {r.bench.dir} · baseline={r.bench.baseline}</span></div>}
              </div>
            </div>

            <div className="cc-panel">
              <PanelHd kicker="REQUIRED GATES" title="dispatch refuses tasks that don't pass" action={<span className="mono cc-dim">required + advisory</span>} />
              <table className="cc-table cc-reg-gates">
                <thead><tr><th>id</th><th>command</th><th style={{ textAlign: 'right' }}>avg</th><th>required</th></tr></thead>
                <tbody>
                  {r.gates.map((g) => (
                    <tr key={g.id} className={g.required ? '' : 'is-advisory'}>
                      <td className="mono">{g.id}</td>
                      <td className="mono cc-reg-cmd">{g.cmd}</td>
                      <td className="mono cc-dim" style={{ textAlign: 'right' }}>{g.avg}</td>
                      <td>
                        {g.required
                          ? <span className="cc-pill is-merged mono">required</span>
                          : <span className="cc-pill mono" style={{ background: 'var(--panel-3)', color: 'var(--t3)' }}>advisory</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="cc-panel">
                <PanelHd kicker="TEST ROOTS" title={r.test_roots.length + ' paths'} />
                <ul className="cc-reg-paths">
                  {r.test_roots.map((tr) => <li key={tr} className="mono">{tr}</li>)}
                </ul>
              </div>
              <div className="cc-panel">
                <PanelHd kicker="SEMANTIC RULES" title="prompt + deterministic gate context" />
                <ul className="cc-reg-rules">
                  {r.semantic_rules.map((s, i) => (
                    <li key={i}>
                      <span className="cc-reg-rule-num mono">{String(i + 1).padStart(2, '0')}</span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      </div>
    </CCApp>
  );
}

Object.assign(window, { ArtRegistry });
