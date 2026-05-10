// data.jsx — Command Center mocked-but-realistic data layer.
// All numeric money fields are integer usd_micros. Times are ISO-8601 Z.

const FEATURE = {
  id_short: 'wf_03418122',
  id: 'wf_03418122e9444754',
  title: 'Range API · cross-shard reader (R)',
  project: 'shardquery',
  branch: 'feat/wf_03418122-range-reader',
  hash: 'plan@b8e21a',
  created_at: '2026-05-10T08:42:11Z',
  current_milestone: 'm2',
  next_task: 'task_44',
  elapsed_seconds: 7892,
};

const FEATURES_LIST = [
  { short: 'wf_03418122', project: 'shardquery',  title: 'Range API · cross-shard reader (R)',  state: 'paused',     runs: 47, cost: 8_864_220, roles: 5, blocker: 'qa.scrutiny attempt 2',  created: '08:42Z' },
  { short: 'wf_03418fa1', project: 'shardquery',  title: 'Lease eviction · pool fairness',      state: 'running',    runs: 22, cost: 3_140_000, roles: 4, blocker: '—',                       created: '09:14Z' },
  { short: 'wf_034182cd', project: 'shardquery',  title: 'Cursor restart on epoch rollover',    state: 'running',    runs: 9,  cost:   814_000, roles: 3, blocker: '—',                       created: '11:02Z' },
  { short: 'wf_034176ab', project: 'compose',     title: 'Composer presence pings',             state: 'blocked',    runs: 33, cost: 5_206_000, roles: 5, blocker: 'user-test slot full',     created: 'yest 22:11' },
  { short: 'wf_03415a18', project: 'compose',     title: 'Drag-reorder for slide thumbs',       state: 'passed',     runs: 28, cost: 2_902_000, roles: 5, blocker: 'merged',                  created: 'yest 16:40' },
  { short: 'wf_034142d2', project: 'gateway',     title: 'Outbound rate-limit on /generate',    state: 'failed',     runs: 18, cost: 2_244_000, roles: 4, blocker: 'recovery loop ×3',        created: 'yest 11:21' },
  { short: 'wf_034128b3', project: 'gateway',     title: 'Replay-attack guard on tool calls',   state: 'queued',     runs: 0,  cost:        0,  roles: 0, blocker: 'awaiting plan',           created: 'just now' },
  { short: 'wf_034112e0', project: 'design-sys',  title: 'Token migration · oklch → relative',  state: 'running',    runs: 14, cost: 1_088_000, roles: 3, blocker: '—',                       created: '07:31Z' },
];

const MILESTONES = [
  { id: 'm0', label: 'Plan & contracts',           signed: true  },
  { id: 'm1', label: 'Type & route scaffolding',   signed: true  },
  { id: 'm2', label: 'Cross-shard reader',         signed: false },
  { id: 'm3', label: 'Verifier wiring',            signed: false },
  { id: 'm4', label: 'Evidence & merge',           signed: false },
];

const TASKS = [
  // m0 (signed)
  { id: 'task_10', role: 'planner',           ms: 'm0', status: 'passed', deps: [], attempts: 1, cost: 240_000, label: 'Plan tree' },
  { id: 'task_11', role: 'qa.author',         ms: 'm0', status: 'passed', deps: ['task_10'], attempts: 1, cost: 110_000, label: 'Spec contracts' },
  { id: 'task_12', role: 'qa.verify.scrutiny',ms: 'm0', status: 'passed', deps: ['task_11'], attempts: 2, cost: 168_000, label: 'Plan scrutiny' },
  // m1 (signed)
  { id: 'task_15', role: 'implementer',       ms: 'm1', status: 'passed', deps: ['task_12'], attempts: 2, cost: 312_000, label: 'Type scaffold' },
  { id: 'task_16', role: 'reviewer',          ms: 'm1', status: 'passed', deps: ['task_15'], attempts: 1, cost: 144_000, label: 'Review scaffold' },
  { id: 'task_17', role: 'qa.author',         ms: 'm1', status: 'passed', deps: ['task_15'], attempts: 1, cost: 96_000,  label: 'Route tests' },
  { id: 'task_18', role: 'qa.verify.scrutiny',ms: 'm1', status: 'passed', deps: ['task_16','task_17'], attempts: 1, cost: 158_000, label: 'Route scrutiny' },
  { id: 'task_19', role: 'qa.verify.usertest',ms: 'm1', status: 'passed', deps: ['task_18'], attempts: 1, cost: 312_000, label: 'Routes user-test' },
  // m2 (in progress)
  { id: 'task_41', role: 'qa.author',         ms: 'm2', status: 'passed',  deps: ['task_18'], attempts: 1, cost: 170_000, label: 'Reader spec' },
  { id: 'task_42', role: 'implementer',       ms: 'm2', status: 'passed',  deps: ['task_41'], attempts: 3, cost: 632_000, label: 'Reader impl' },
  { id: 'task_43', role: 'reviewer',          ms: 'm2', status: 'passed',  deps: ['task_42'], attempts: 1, cost: 142_000, label: 'Reader review' },
  { id: 'task_44', role: 'qa.verify.scrutiny',ms: 'm2', status: 'running', deps: ['task_43'], attempts: 2, cost: 509_000, label: 'Reader scrutiny' },
  { id: 'task_45', role: 'qa.verify.usertest',ms: 'm2', status: 'queued',  deps: ['task_44'], attempts: 0, cost: 0,       label: 'Reader user-test' },
  { id: 'task_46', role: 'recovery',          ms: 'm2', status: 'blocked', deps: ['task_44'], attempts: 0, cost: 0,       label: 'Corrective slice' },
  // m3
  { id: 'task_50', role: 'implementer',       ms: 'm3', status: 'queued', deps: ['task_45'], attempts: 0, cost: 0, label: 'Verifier wiring'   },
  { id: 'task_51', role: 'qa.author',         ms: 'm3', status: 'queued', deps: ['task_45'], attempts: 0, cost: 0, label: 'Verifier red proof'},
  { id: 'task_52', role: 'reviewer',          ms: 'm3', status: 'queued', deps: ['task_50'], attempts: 0, cost: 0, label: 'Verifier review'   },
  { id: 'task_53', role: 'qa.verify.scrutiny',ms: 'm3', status: 'queued', deps: ['task_52'], attempts: 0, cost: 0, label: 'Verifier scrutiny' },
  // m4
  { id: 'task_70', role: 'qa.verify.usertest',ms: 'm4', status: 'queued', deps: ['task_53'], attempts: 0, cost: 0, label: 'Final user-test' },
  { id: 'task_71', role: 'reviewer',          ms: 'm4', status: 'queued', deps: ['task_70'], attempts: 0, cost: 0, label: 'Final review'    },
  { id: 'task_72', role: 'planner',           ms: 'm4', status: 'queued', deps: ['task_71'], attempts: 0, cost: 0, label: 'Evidence pack'   },
  // superseded
  { id: 'task_99', role: 'implementer',       ms: 'm2', status: 'superseded', deps: ['task_15'], attempts: 1, cost: 88_000, label: 'Reader pre-replan' },
];

// Each handoff is a rich row used in Handoff list + detail.
const HANDOFFS = [
  {
    id: 'h_804',
    from: 'task_44', to: 'task_46',
    from_role: 'qa.verify.scrutiny', to_role: 'recovery',
    kind: 'recovery_request',
    title: 'Recovery: cross-shard reader · attempt 2 → repair slice',
    status: 'open',
    created: '11:51:33Z', waiting: '32m 14s',
    files: 4, diff_loc: 86, add: 64, del: 22,
    cost: 311_000, tok_in: 184_000, tok_out: 8_400,
    gates: [
      { label: 'lint clean (ruff)',                 status: 'pass', meta: '12 files · 0 issues' },
      { label: 'type clean (pyright)',              status: 'pass', meta: '0 errors · strict' },
      { label: 'unit tests',                        status: 'fail', meta: '2 red · test_fence_boundary' },
      { label: 'property tests (n=200)',            status: 'pass', meta: '200 / 200' },
      { label: 'spec-coverage diff ≤ +20%',         status: 'pass', meta: '+8% net new' },
      { label: 'authored-by ≠ verifier',            status: 'pass', meta: 'qa.scrutiny ≠ implementer' },
      { label: 'no orphan side effects',            status: 'warn', meta: 'pool.checkout outside tx · noted' },
    ],
  },
  {
    id: 'h_803', from: 'task_44', to: 'task_44',
    from_role: 'qa.verify.scrutiny', to_role: 'qa.verify.scrutiny',
    kind: 'verification_repair', title: 'Self-repair: pool lease eviction property',
    status: 'closed', created: '11:34:02Z', waiting: '—',
    files: 1, diff_loc: 24, add: 22, del: 2, cost: 198_000, tok_in: 162_000, tok_out: 6_900,
    gates: [],
  },
  {
    id: 'h_802', from: 'task_43', to: 'task_44',
    from_role: 'reviewer', to_role: 'qa.verify.scrutiny',
    kind: 'qa_scrutiny', title: 'Scrutiny dispatch · reader review approved',
    status: 'closed', created: '11:18:55Z', waiting: '—',
    files: 8, diff_loc: 612, add: 488, del: 124, cost: 142_000, tok_in: 144_000, tok_out: 4_200,
    gates: [],
  },
  {
    id: 'h_801', from: 'task_42', to: 'task_43',
    from_role: 'implementer', to_role: 'reviewer',
    kind: 'review_request', title: 'Review request · reader impl attempt 3',
    status: 'closed', created: '11:08:14Z', waiting: '—',
    files: 6, diff_loc: 488, add: 412, del: 76, cost: 244_000, tok_in: 220_000, tok_out: 9_800,
    gates: [],
  },
];

const RUNS = [
  { id: 9182, task: 'task_44', role: 'qa.verify.scrutiny', model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'running', attempt: 2, cost: 311_000, tok_in: 184_000, tok_cached: 162_000, tok_out: 8_400, tok_reason: 24_000, wall: { queue: 12, lease: 4, model: 188, verify: 22, blocked: 0 }, started: '11:34:02Z' },
  { id: 9181, task: 'task_44', role: 'qa.verify.scrutiny', model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'failed',  attempt: 1, cost: 198_000, tok_in: 162_000, tok_cached: 144_000, tok_out: 6_900, tok_reason: 18_000, wall: { queue: 14, lease: 6, model: 142, verify: 18, blocked: 0 }, started: '11:18:55Z' },
  { id: 9180, task: 'task_43', role: 'reviewer',           model: 'gpt-5.4-medium',    provider: 'openai',    status: 'passed',  attempt: 1, cost: 142_000, tok_in: 144_000, tok_cached: 128_000, tok_out: 4_200, tok_reason: 11_000, wall: { queue: 6,  lease: 3, model: 96,  verify: 12, blocked: 0 }, started: '11:08:14Z' },
  { id: 9179, task: 'task_42', role: 'implementer',        model: 'gpt-5.4-high',      provider: 'openai',    status: 'passed',  attempt: 3, cost: 244_000, tok_in: 220_000, tok_cached: 195_000, tok_out: 9_800, tok_reason: 16_000, wall: { queue: 22, lease: 5, model: 252, verify: 38, blocked: 14 }, started: '10:51:02Z' },
  { id: 9178, task: 'task_42', role: 'implementer',        model: 'gpt-5.4-high',      provider: 'openai',    status: 'failed',  attempt: 2, cost: 188_000, tok_in: 192_000, tok_cached: 174_000, tok_out: 8_100, tok_reason: 12_000, wall: { queue: 28, lease: 7, model: 188, verify: 24, blocked: 0  }, started: '10:34:18Z' },
  { id: 9177, task: 'task_41', role: 'qa.author',          model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'passed',  attempt: 1, cost: 170_000, tok_in: 102_000, tok_cached: 88_000,  tok_out: 7_400, tok_reason: 9_000,  wall: { queue: 8,  lease: 4, model: 132, verify: 16, blocked: 0  }, started: '10:14:11Z' },
  { id: 9176, task: 'task_18', role: 'qa.verify.scrutiny', model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'passed',  attempt: 1, cost: 158_000, tok_in: 138_000, tok_cached: 122_000, tok_out: 5_800, tok_reason: 14_000, wall: { queue: 10, lease: 4, model: 118, verify: 14, blocked: 0  }, started: '09:48:02Z' },
  { id: 9175, task: 'task_17', role: 'qa.author',          model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'passed',  attempt: 1, cost: 96_000,  tok_in: 78_000,  tok_cached: 66_000,  tok_out: 4_200, tok_reason: 6_000,  wall: { queue: 6,  lease: 3, model: 78,  verify: 10, blocked: 0  }, started: '09:31:12Z' },
  { id: 9174, task: 'task_16', role: 'reviewer',           model: 'gpt-5.4-medium',    provider: 'openai',    status: 'passed',  attempt: 1, cost: 144_000, tok_in: 128_000, tok_cached: 112_000, tok_out: 4_400, tok_reason: 9_000,  wall: { queue: 8,  lease: 4, model: 96,  verify: 12, blocked: 0  }, started: '09:14:48Z' },
  { id: 9173, task: 'task_15', role: 'implementer',        model: 'gpt-5.4-high',      provider: 'openai',    status: 'passed',  attempt: 2, cost: 312_000, tok_in: 224_000, tok_cached: 198_000, tok_out: 11_400,tok_reason: 18_000, wall: { queue: 14, lease: 5, model: 252, verify: 32, blocked: 0  }, started: '08:48:33Z' },
  { id: 9172, task: 'task_12', role: 'qa.verify.scrutiny', model: 'claude-sonnet-4.6', provider: 'anthropic', status: 'passed',  attempt: 2, cost: 168_000, tok_in: 132_000, tok_cached: 118_000, tok_out: 6_100, tok_reason: 12_000, wall: { queue: 12, lease: 4, model: 124, verify: 16, blocked: 0  }, started: '08:42:11Z' },
  { id: 9171, task: 'task_10', role: 'planner',            model: 'gpt-5.4-thinking',  provider: 'openai',    status: 'passed',  attempt: 1, cost: 240_000, tok_in: 188_000, tok_cached: 0,       tok_out: 14_200,tok_reason: 42_000, wall: { queue: 4,  lease: 3, model: 312, verify: 8,  blocked: 0  }, started: '08:30:00Z' },
];

const VALIDATION = {
  scrutiny: {
    breaks: [
      { attempt: 1, outcome: 'survived', tag: 'fuzz · split-on-cursor',   summary: 'Reader yielded duplicate row at fence boundary when range split exactly on cursor.', broke: 'src/range/reader.py · _merge', tests_total: 38, tests_failed: 2, elapsed: '142s' },
      { attempt: 2, outcome: 'caught',   tag: 'fuzz · empty cross-shard', summary: 'Empty range across N shards now correctly yields zero rows; prior version raised IndexError.', broke: '—', tests_total: 38, tests_failed: 0, elapsed: '188s' },
      { attempt: 3, outcome: 'survived', tag: 'load · 4 readers',         summary: 'Lease pool starves when 4 concurrent readers contend a single shard; cursor blocks past 80ms lease.', broke: 'src/range/pool.py · checkout', tests_total: 38, tests_failed: 1, elapsed: '212s' },
      { attempt: 4, outcome: 'pending',  tag: 'symmetric · reverse iter', summary: 'Reverse iteration not yet executed; queued for next attempt.', broke: '—', tests_total: 0, tests_failed: 0, elapsed: '—' },
    ],
  },
  spec: [
    { label: 'Returns rows in non-decreasing key order across shards',                method: 'property test',  evidence: 'tests/range/test_reader_order.py', status: 'pass' },
    { label: 'Empty range across N shards returns zero rows, no error',                method: 'unit test',      evidence: 'tests/range/test_reader_empty.py', status: 'pass' },
    { label: 'Resumes correctly across shard split when cursor falls on fence',        method: 'unit + manual',  evidence: 'tests/range/test_fence_boundary.py', status: 'fail' },
    { label: 'Pool checkout fair under 4-reader contention (no >200ms starvation)',    method: 'load test',      evidence: 'tests/range/test_pool_lease_eviction.py', status: 'fail' },
    { label: 'Cursor decodes tolerate epoch rollover',                                  method: 'unit test',      evidence: 'tests/range/test_cursor_epoch.py', status: 'pass' },
    { label: 'User-test: cross-shard query, 4 ranges, 2 shards completes <2s',          method: 'user-test',      evidence: 'pending slot 2/3',                  status: 'pending' },
    { label: 'User-test: lease-timeout shard behaves as documented',                    method: 'user-test',      evidence: 'queued',                             status: 'queued' },
  ],
  coverage: [
    { file: 'src/range/reader.py',  pct: 92, lines: 318 },
    { file: 'src/range/pool.py',    pct: 84, lines: 144 },
    { file: 'src/range/cursor.py',  pct: 96, lines: 72  },
    { file: 'src/range/_merge.py',  pct: 78, lines: 78  },
  ],
};

const INTERVENTIONS = [
  { id: 1042, ts: '2026-05-10T11:18:02Z', actor: 'op_josh',  kind: 'pause_feature',         payload: {},                                                            note: 'Suspect cost spike in reader impl.' },
  { id: 1043, ts: '2026-05-10T11:19:14Z', actor: 'op_josh',  kind: 'pause_feature',         payload: {},                                                            note: 'Double-pause — accidental click; audit log keeps both rows.' },
  { id: 1044, ts: '2026-05-10T11:31:48Z', actor: 'op_josh',  kind: 'resume_feature',        payload: {},                                                            note: '' },
  { id: 1045, ts: '2026-05-10T11:51:30Z', actor: 'op_josh',  kind: 'comment',               payload: { note: 'Watch fence boundary in scrutiny attempt 2.' },        note: '' },
  { id: 1046, ts: '2026-05-10T12:04:12Z', actor: 'op_josh',  kind: 'skip_slice',            payload: { task_id: 'task_99', reason: 'pre-replan, do not retry' },     note: '' },
  { id: 1047, ts: '2026-05-10T12:11:48Z', actor: 'op_yliao', kind: 'sign_milestone',        payload: { from_milestone: 'm1', task_id: 'task_19' },                   note: 'Routes user-test green; sign m1.' },
  { id: 1048, ts: '2026-05-10T12:18:22Z', actor: 'op_josh',  kind: 'replan',                payload: { from_milestone: 'm2', reason: 'fence-boundary semantics shifted' }, note: '' },
  { id: 1049, ts: '2026-05-10T12:24:01Z', actor: 'op_josh',  kind: 'pause_feature',         payload: {},                                                            note: 'Holding for design review.' },
];

const TELEMETRY = {
  totals: {
    cost_micros: 8_864_220,
    tok_in: 4_412_000,
    tok_cached: 3_882_000,
    tok_out: 184_400,
    tok_reason: 318_000,
    savior_saved: 1_104_000,
    runs: 47,
    repairs: 7,
  },
  by_model: [
    { id: 'claude-sonnet-4.6', cost: 4_212_000, runs: 24 },
    { id: 'gpt-5.4-high',      cost: 2_988_000, runs: 12 },
    { id: 'gpt-5.4-medium',    cost: 1_124_000, runs:  8 },
    { id: 'gpt-5.4-thinking',  cost:   540_220, runs:  3 },
  ],
  by_role: [
    { role: 'planner',            cost: 412_000,   wall: 412,   runs: 2  },
    { role: 'implementer',        cost: 1_976_000, wall: 1_840, runs: 14 },
    { role: 'reviewer',           cost: 388_000,   wall: 388,   runs: 4  },
    { role: 'qa.author',          cost: 449_000,   wall: 312,   runs: 5  },
    { role: 'qa.verify.scrutiny', cost: 944_000,   wall: 1_182, runs: 12 },
    { role: 'qa.verify.usertest', cost: 4_695_220, wall: 1_810, runs: 10 },
  ],
  wall_mix: { queue: 712, lease: 248, model: 5_140, verify: 1_642, blocked: 150 },
  user_test_slot: { project: 'shardquery', current: 'wf_03418122', queued: 1, max: 3, holding_seconds: 412 },
  // 24h cumulative cost timeline w/ event markers
  timeline: (function () {
    const pts = [];
    const evtAt = { 4: 'plan', 9: 'sign m1', 14: 're-plan', 18: 'pause', 22: 'resume' };
    let cum = 0;
    for (let i = 0; i <= 24; i++) {
      // accelerating curve
      const inc = 80_000 + i * 18_000 + (i > 14 ? 110_000 : 0) + (i === 18 ? 0 : 0);
      cum += inc * (i > 18 && i < 22 ? 0.2 : 1); // pause flattens
      pts.push({ h: i, cum: Math.round(cum), event: evtAt[i] });
    }
    return pts;
  })(),
};

Object.assign(window, {
  CC_FEATURE: FEATURE,
  CC_FEATURES_LIST: FEATURES_LIST,
  CC_MILESTONES: MILESTONES,
  CC_TASKS: TASKS,
  CC_HANDOFFS: HANDOFFS,
  CC_RUNS: RUNS,
  CC_VALIDATION: VALIDATION,
  CC_INTERVENTIONS: INTERVENTIONS,
  CC_TELEMETRY: TELEMETRY,
});
