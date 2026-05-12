// data-tasks.jsx — per-task detail fixtures used by the Tasks views.
// Pairs with data.jsx — extends the existing CC_TASKS rows with metadata
// the §8a task detail surface needs (contract, signoffs, recovery, self-repair,
// artifacts) without duplicating the per-task list.

// Per-task summary fields layered onto CC_TASKS rows. Keyed by task_id.
const CC_TASK_META = {
  task_10: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: 'c0a4f1', loc_add: 0,   loc_del: 0,   files: 1 },
  task_11: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: 'd1882a', loc_add: 0,   loc_del: 0,   files: 1 },
  task_12: { repairs: 1, last_blocker: { code: 'engineering.qa_tests_not_red', at: '08:38:12Z' },
                                                                              contract_v: 1, hash: '4e7c12', loc_add: 0,   loc_del: 0,   files: 0 },
  task_15: { repairs: 1, last_blocker: { code: 'engineering.implementation_verification_failed', at: '08:46:11Z' },
                                                                              contract_v: 1, hash: 'a17822', loc_add: 412, loc_del: 38,  files: 14 },
  task_16: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: 'b2c9d0', loc_add: 0,   loc_del: 0,   files: 14 },
  task_17: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '7714ab', loc_add: 88,  loc_del: 0,   files: 4 },
  task_18: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '0e62c1', loc_add: 0,   loc_del: 0,   files: 0 },
  task_19: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '5a3320', loc_add: 0,   loc_del: 0,   files: 0 },
  task_41: { repairs: 0, last_blocker: null,                                  contract_v: 2, hash: 'aa0044', loc_add: 122, loc_del: 0,   files: 5 },
  task_42: { repairs: 2, last_blocker: { code: 'engineering.review_rejected', at: '10:51:02Z' },
                                                                              contract_v: 2, hash: 'aa0044', loc_add: 488, loc_del: 76,  files: 6 },
  task_43: { repairs: 0, last_blocker: null,                                  contract_v: 2, hash: 'aa0044', loc_add: 0,   loc_del: 0,   files: 0 },
  task_44: { repairs: 1, last_blocker: { code: 'engineering.qa_semantic_quality_failed', at: '11:34:02Z' },
                                                                              contract_v: 2, hash: 'aa0044', loc_add: 64,  loc_del: 22,  files: 4 },
  task_45: { repairs: 0, last_blocker: null,                                  contract_v: 2, hash: 'aa0044', loc_add: 0,   loc_del: 0,   files: 0 },
  task_46: { repairs: 0, last_blocker: null,                                  contract_v: 2, hash: 'aa0044', loc_add: 0,   loc_del: 0,   files: 0 },
  task_50: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_51: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_52: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_53: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_70: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_71: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_72: { repairs: 0, last_blocker: null,                                  contract_v: 1, hash: '——————', loc_add: 0,   loc_del: 0,   files: 0 },
  task_99: { repairs: 0, last_blocker: { code: 'engineering.task_contract_missing', at: '09:52:11Z' },
                                                                              contract_v: 1, hash: '7e9911', loc_add: 0,   loc_del: 0,   files: 0 },
};

// ─────────────────────────────────────────────────────────
// CC_TASK_DETAIL — one entry per task that has a Task-view artboard.
// Shape: { contract, signoffs, recovery, selfrepair, interventions, artifacts }
// ArtTaskView reads this; CC_TASKS provides the row-level header data.
// ─────────────────────────────────────────────────────────
const CC_TASK_DETAIL = {

  // ── task_44 · qa.verify.scrutiny · RUNNING ─────────────
  task_44: {
    contract: {
      task_id: 'task_44',
      feature_id: 'wf_03418122e9444754',
      milestone_id: 'm2',
      contract_version: 2,
      input_contract_hash: 'sha256:aa0044…fc217e',
      authored_by: 'planner',
      authored_at: '2026-05-10T08:42:11Z',
      role: 'qa.verify.scrutiny',
      signoff_policy: 'scrutiny_and_usertest',
      inputs: {
        review_handoff_id: 'h_802', review_decision: 'approve',
        diff_loc: 612, files_under_review: 8,
        seed_corpus: 'tests/range/fixtures/cross_shard.json',
      },
      expected_outputs: {
        scrutiny_attempts_min: 2, scrutiny_attempts_max: 4, must_break_at_least: 1,
        blocker_codes_handled: [
          'engineering.qa_semantic_quality_failed',
          'engineering.qa_verify_failed',
          'engineering.qa_tests_not_red',
        ],
        artifacts: ['scrutiny_log', 'fuzzed_inputs', 'red_proof_diff'],
      },
      budget: { wall_seconds_max: 600, cost_micros_max: 1_200_000, repairs_max: 3 },
      handoff_in: 'h_802', handoff_out: ['h_803', 'h_804'],
    },
    signoffs: [
      { id: 'sig_207', validator_type: 'scrutiny', state: 'pending', attempts: 4,
        last_attempt: 'attempt 4 · symmetric · queued',
        note: 'Attempt 3 broke (lease starvation); recovery requested via h_804.' },
      { id: 'sig_208', validator_type: 'usertest', state: 'holding', attempts: 1,
        last_attempt: 'slot 2/3 · op_yliao · 6m 12s',
        note: 'Slot held; not yet released. Independent of scrutiny verdict.' },
    ],
    recovery: [
      { id: 'rec_88', target_task: 'task_46', state: 'open',
        blocker_code: 'engineering.qa_semantic_quality_failed',
        summary: 'Pool starvation under 4-reader contention. Spawning corrective slice on src/range/pool.py.',
        file_set: ['src/range/pool.py', 'tests/range/test_pool_lease_eviction.py'] },
      { id: 'rec_87', target_task: 'task_44', state: 'closed',
        blocker_code: 'engineering.qa_verify_failed',
        summary: 'Self-repair: scrutiny harness mis-imported corpus path; fixed inline.',
        file_set: ['tests/range/test_reader_order.py'] },
    ],
    selfrepair: [
      { id: 'sr_31', at: '11:34:18Z', from: 'attempt 1 → 2',
        issue: 'corpus path resolution', fix: 'switched to importlib.resources', delta_cost: 198_000 },
      { id: 'sr_30', at: '11:35:42Z', from: 'attempt 2 internal',
        issue: 'flaky fence-boundary test', fix: 'pinned random seed via @pytest.fixture', delta_cost: 14_000 },
    ],
    interventions: [
      { id: 1045, ts: '11:51:30Z', actor: 'op_josh', kind: 'comment',       note: '"Watch fence boundary in scrutiny attempt 2."' },
      { id: 1048, ts: '12:18:22Z', actor: 'op_josh', kind: 'replan',        note: 'from m2 · "fence-boundary semantics shifted"' },
      { id: 1049, ts: '12:24:01Z', actor: 'op_josh', kind: 'pause_feature', note: 'Holding for design review.' },
    ],
    artifacts: [
      { id: 'art_412', name: 'scrutiny_log_a3.md',      type: 'log',   bytes: 18_204, by: 'run 9181', at: '11:21Z' },
      { id: 'art_413', name: 'red_proof_a3.diff',       type: 'diff',  bytes:  4_122, by: 'run 9181', at: '11:21Z' },
      { id: 'art_414', name: 'fuzz_corpus_split.json',  type: 'json',  bytes: 92_440, by: 'run 9182', at: '11:36Z' },
      { id: 'art_415', name: 'pool_lease_trace.svg',    type: 'image', bytes:  6_812, by: 'run 9182', at: '11:38Z' },
      { id: 'art_416', name: 'pytest_junit_a3.xml',     type: 'junit', bytes: 12_002, by: 'run 9181', at: '11:21Z' },
      { id: 'art_417', name: 'scrutiny_log_a4.md',      type: 'log',   bytes:  9_814, by: 'run 9182', at: '11:48Z', live: true },
    ],
  },

  // ── task_42 · implementer · PASSED (3 attempts, 2 repairs) ──
  task_42: {
    contract: {
      task_id: 'task_42',
      feature_id: 'wf_03418122e9444754',
      milestone_id: 'm2',
      contract_version: 2,
      input_contract_hash: 'sha256:aa0044…b18e22',
      authored_by: 'planner',
      authored_at: '2026-05-10T09:48:02Z',
      role: 'implementer',
      signoff_policy: 'scrutiny_and_usertest',
      inputs: {
        spec_handoff_id: 'h_799', spec_task: 'task_41',
        allowed_paths: ['src/range/**', 'tests/range/**'],
        forbidden_paths: ['src/auth/**', 'migrations/**'],
      },
      expected_outputs: {
        files_changed_min: 3, files_changed_max: 12,
        tests_added_min: 4,
        artifacts: ['diff', 'test_added_list', 'pytest_output'],
      },
      budget: { wall_seconds_max: 1200, cost_micros_max: 900_000, repairs_max: 3 },
      handoff_in: 'h_799', handoff_out: ['h_801'],
    },
    signoffs: [
      { id: 'sig_201', validator_type: 'review',  state: 'passed', attempts: 1,
        last_attempt: 'reviewer approved via h_801', note: 'No blockers; merge-ready.' },
    ],
    recovery: [],
    selfrepair: [
      { id: 'sr_22', at: '10:41:11Z', from: 'attempt 1 → 2',
        issue: 'ruff E501 in _merge.py', fix: 'wrapped long expression across 3 lines', delta_cost: 18_000 },
      { id: 'sr_23', at: '10:48:02Z', from: 'attempt 2 → 3',
        issue: 'pyright partial-Unknown on iterator',
        fix: 'introduced typed Range alias for List[Range]', delta_cost: 24_000 },
    ],
    interventions: [
      { id: 1044, ts: '11:31:48Z', actor: 'op_josh', kind: 'resume_feature', note: 'resumed after design review.' },
    ],
    artifacts: [
      { id: 'art_380', name: 'reader_impl_a3.diff',        type: 'diff',  bytes: 24_812, by: 'run 9179', at: '10:51Z' },
      { id: 'art_381', name: 'pytest_a3.out',              type: 'log',   bytes: 18_204, by: 'run 9179', at: '10:54Z' },
      { id: 'art_382', name: 'coverage_a3.xml',            type: 'junit', bytes:  6_812, by: 'run 9179', at: '10:54Z' },
      { id: 'art_383', name: 'reader_impl_a2.diff',        type: 'diff',  bytes: 22_180, by: 'run 9178', at: '10:34Z' },
      { id: 'art_384', name: 'pyright_a2.log',             type: 'log',   bytes:  4_122, by: 'run 9178', at: '10:36Z' },
      { id: 'art_385', name: 'reader_impl_a1.diff',        type: 'diff',  bytes: 18_400, by: 'run 9168', at: '10:14Z' },
    ],
  },

  // ── task_50 · implementer · QUEUED (pre-execution) ─────
  task_50: {
    contract: {
      task_id: 'task_50',
      feature_id: 'wf_03418122e9444754',
      milestone_id: 'm3',
      contract_version: 1,
      input_contract_hash: 'sha256:pending — locked behind m2',
      authored_by: 'planner',
      authored_at: '2026-05-10T08:42:11Z',
      role: 'implementer',
      signoff_policy: 'scrutiny_and_usertest',
      inputs: {
        spec_handoff_id: 'awaiting · qa.author task_51',
        depends_on: 'task_45 (m2 user-test)',
        allowed_paths: ['src/verifier/**', 'tests/verifier/**'],
      },
      expected_outputs: {
        files_changed_min: 2, files_changed_max: 8,
        tests_added_min: 3,
        artifacts: ['diff', 'verifier_wire_log'],
      },
      budget: { wall_seconds_max: 900, cost_micros_max: 600_000, repairs_max: 3 },
      handoff_in: '— (gated by m2)', handoff_out: [],
    },
    signoffs: [],
    recovery: [],
    selfrepair: [],
    interventions: [],
    artifacts: [],
  },
};

Object.assign(window, {
  CC_TASK_META,
  CC_TASK_DETAIL,
  // Legacy aliases — kept so the first cut of ArtTaskView doesn't break if
  // anything else still imports them by name.
  CC_TASK_CONTRACT_44:      CC_TASK_DETAIL.task_44.contract,
  CC_TASK_SIGNOFFS_44:      CC_TASK_DETAIL.task_44.signoffs,
  CC_TASK_RECOVERY_44:      CC_TASK_DETAIL.task_44.recovery,
  CC_TASK_SELFREPAIR_44:    CC_TASK_DETAIL.task_44.selfrepair,
  CC_TASK_INTERVENTIONS_44: CC_TASK_DETAIL.task_44.interventions,
  CC_TASK_ARTIFACTS_44:     CC_TASK_DETAIL.task_44.artifacts,
});
