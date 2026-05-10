import { expect, test } from "@playwright/test";

const featureId = "wf_03a7d36266f044859eeaa024161ac564";
const feature = {
  feature_id: featureId,
  project: "pgloom-engineering",
  branch: "feat/command-center-ui",
  state: "running",
  paused: false,
  cost_usd_micros: 12400,
  runs: 12,
  roles_seen: "planner,designer,implementer",
  last_blocker: null,
  created_at: "2026-05-10T00:00:00Z",
  updated_at: "2026-05-10T01:10:00Z",
  input_tokens: 8400,
  cached_input_tokens: 1200,
  output_tokens: 3300,
  reasoning_tokens: 700,
  token_savior_saved_tokens: 1400,
  rtk_saved_tokens: 320,
  running_seconds: 1220
};

const dag = {
  milestones: [
    { id: "m0", label: "bootstrap", task_ids: ["t0"] },
    { id: "m1", label: "implement", task_ids: ["t1"] }
  ],
  tasks: [
    {
      id: "t0",
      role: "planner",
      status: "completed",
      depends_on: [],
      milestone_id: "m0",
      task_slice_id: "impl-bootstrap",
      last_run: {
        id: 9001,
        feature_id: featureId,
        project: "pgloom-engineering",
        task_id: "t0",
        role: "planner",
        phase: "plan",
        validator_type: null,
        status: "completed",
        attempt: 1,
        model_provider: "openai",
        model: "gpt-4o-mini",
        input_tokens: 1200,
        cached_input_tokens: 200,
        cache_creation_tokens: 40,
        output_tokens: 300,
        reasoning_tokens: 50,
        token_savior_saved_tokens: 10,
        rtk_saved_tokens: 0,
        queued_seconds: 3,
        leased_seconds: 8,
        model_seconds: 25,
        verification_seconds: 7,
        blocked_seconds: 0,
        running_seconds: 120,
        cost_usd_micros: 4200,
        started_at: "2026-05-10T00:05:00Z"
      }
    },
    {
      id: "t1",
      role: "implementer",
      status: "running",
      depends_on: ["t0"],
      milestone_id: "m1",
      task_slice_id: "impl-implement",
      last_run: null
    }
  ],
  edges: [{ from: "t0", to: "t1", kind: "dependency" }]
};

const run = {
  id: 9001,
  feature_id: featureId,
  project: "pgloom-engineering",
  task_id: "t1",
  role: "implementer",
  phase: "implement",
  validator_type: null,
  status: "running",
  attempt: 2,
  model_provider: "openai",
  model: "gpt-4.1-mini",
  input_tokens: 1800,
  cached_input_tokens: 400,
  cache_creation_tokens: 25,
  output_tokens: 700,
  reasoning_tokens: 200,
  token_savior_saved_tokens: 120,
  rtk_saved_tokens: 60,
  queued_seconds: 4,
  leased_seconds: 12,
  model_seconds: 44,
  verification_seconds: 6,
  blocked_seconds: 1,
  running_seconds: 210,
  cost_usd_micros: 8200,
  started_at: "2026-05-10T00:15:00Z"
};

const handoff = {
  id: "handoff-1",
  from_task_id: "t0",
  to_task_id: "t1",
  handoff_type: "handoff",
  status: "ready",
  summary: "Plan output handed to implementer",
  contract: {
    title: "Plan to implement",
    summary: "Implementer receives plan spec",
    inputs: {
      task_id: "t1",
      role: "implementer",
      allowed_paths: ["core/**"],
      expected_outputs: ["result.md"],
      context_budget: 1300,
      validation_strategy: { command: ["tests pass"] }
    },
    objective: "ship reliable core scaffold",
    validation_strategy: { command: ["checks pass"] }
  },
  created_at: "2026-05-10T00:20:00Z",
  updated_at: "2026-05-10T00:20:10Z"
};

const taskHeader = {
  task_id: "t1",
  feature_id: featureId,
  plan_contract_id: "plan-1",
  role: "implementer",
  status: "active",
  runtime_state: "running",
  task_type: "engineering.implement",
  slot: "implementer",
  milestone_id: "m1",
  task_slice_id: "impl-implement",
  input_contract: { objective: "ship reliable core scaffold", allowed_paths: ["core/**"] },
  output_contract: { expected_outputs: ["result.md"] },
  validation_errors: [],
  created_at: "2026-05-10T00:10:00Z",
  updated_at: "2026-05-10T00:20:00Z"
};

const taskTelemetry = {
  runs: 1,
  input_tokens: 1800,
  cached_input_tokens: 400,
  output_tokens: 700,
  reasoning_tokens: 200,
  cost_usd_micros: 8200,
  running_seconds: 210,
  queued_seconds: 4,
  leased_seconds: 12,
  model_seconds: 44,
  verification_seconds: 6,
  blocked_seconds: 1
};

const artifacts = [
  {
    id: "artifact-diff",
    feature_id: featureId,
    task_id: "t1",
    kind: "worktree_diff",
    name: "worktree.diff",
    path: "/tmp/worktree.diff",
    sha256: "abcdef123456",
    metadata: { files_changed: 3 },
    created_at: "2026-05-10T00:25:00Z"
  }
];

const council = {
  id: "council-1",
  feature_id: featureId,
  task_id: "t0",
  role: "planner",
  purpose: "initial_plan",
  status: "passed",
  legacy: false,
  critic_verdict: "accept",
  cost_usd_micros: 2000,
  total_tokens: 1220,
  iterations_used: 1,
  iteration_max: 2,
  panelists: [{ id: 1, panelist_kind: "panelist", panelist_ordinal: 0, status: "passed", cost_usd_micros: 900 }],
  worker_runs: [run],
  started_at: "2026-05-10T00:00:00Z",
  finished_at: "2026-05-10T00:05:00Z"
};

const legacyCouncil = {
  id: "council_legacy_plan-1_0",
  feature_id: featureId,
  task_id: "t0",
  role: "planner",
  purpose: "initial_plan",
  status: "passed",
  legacy: true,
  critic_verdict: "accept",
  report: { critic: { verdict: "accept" } },
  panelists: [],
  worker_runs: []
};

const runsAggregate = [
  {
    row_id: 1,
    role: "planner",
    phase: "plan",
    attempts: 1,
    rows: 3
  }
];

const tokenSavior = [
  {
    project: "pgloom-engineering",
    profile_name: "feature.plan",
    rows: 2,
    input_tokens_original: 8000,
    input_tokens_after_savior: 6400,
    tokens_saved: 1600,
    reduction_ratio: 0.2,
    estimated_cost_saved_usd_micros: 1200
  }
];

const modelUsage = [
  {
    project: "pgloom-engineering",
    profile_name: "gpt",
    calls: 2,
    input_tokens: 3600,
    cached_input_tokens: 600,
    output_tokens: 1000,
    reasoning_tokens: 250,
    cost_usd_micros: 12400,
    providers: "openai",
    models: "gpt-4.1-mini"
  }
];

const slots = [
  {
    slot: "planner",
    max: 1,
    holding: 1,
    running: 1,
    leased: 0,
    queued: 0,
    blocked: 0,
    lock_count: 1,
    tasks: [
      {
        project: "pgloom-engineering",
        workflow_id: featureId,
        task_id: "t0",
        task_type: "planner",
        state: "running",
        lease_owner: "owner-planner",
        lease_expires_at: "2026-05-10T02:00:00Z",
        updated_at: "2026-05-10T00:35:00Z"
      }
    ],
    holds: [
      {
        resource_key: "full_app_run:pgloom-engineering",
        project: "pgloom-engineering",
        workflow_id: featureId,
        owner_id: "owner-planner",
        task_id: "t0",
        expires_at: "2026-05-10T02:00:00Z"
      }
    ]
  },
  {
    slot: "implementer",
    max: 1,
    holding: 1,
    running: 1,
    leased: 1,
    queued: 2,
    blocked: 0,
    lock_count: 0,
    tasks: [
      {
        project: "pgloom-engineering",
        workflow_id: featureId,
        task_id: "t1",
        task_type: "implementer",
        state: "leased",
        lease_owner: "owner-impl",
        lease_expires_at: "2026-05-10T02:00:00Z",
        updated_at: "2026-05-10T00:31:00Z"
      }
    ],
    holds: []
  }
];

const realtime = {
  channel: "cc_events",
  subscribers: 4,
  max_queue_size: 200,
  start_realtime: true,
  database_configured: true
};

async function mockApi(page) {
  await page.route("**/api/**", async (route) => {
    const requestPath = new URL(route.request().url()).pathname;
    let payload = {};

    if (requestPath === "/api/features") {
      payload = [feature];
    } else if (requestPath === `/api/features/${featureId}`) {
      payload = feature;
    } else if (requestPath === `/api/features/${featureId}/dag`) {
      payload = dag;
    } else if (requestPath === `/api/features/${featureId}/runs`) {
      payload = [run];
    } else if (requestPath === `/api/features/${featureId}/model-usage`) {
      payload = modelUsage;
    } else if (requestPath === `/api/features/${featureId}/token-savior`) {
      payload = tokenSavior;
    } else if (requestPath === `/api/features/${featureId}/slots`) {
      payload = slots;
    } else if (requestPath === `/api/features/${featureId}/handoffs`) {
      payload = [handoff];
    } else if (requestPath === `/api/features/${featureId}/councils`) {
      payload = [council, legacyCouncil];
    } else if (requestPath === `/api/features/${featureId}/councils/council-1`) {
      payload = council;
    } else if (requestPath === `/api/features/${featureId}/councils/council_legacy_plan-1_0`) {
      payload = legacyCouncil;
    } else if (requestPath === `/api/features/${featureId}/tasks/t1`) {
      payload = taskHeader;
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/runs`) {
      payload = [run];
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/handoffs`) {
      payload = [handoff];
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/qa`) {
      payload = [];
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/recovery`) {
      payload = [];
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/interventions`) {
      payload = [];
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/artifacts`) {
      payload = artifacts;
    } else if (requestPath === `/api/features/${featureId}/tasks/t1/telemetry`) {
      payload = taskTelemetry;
    } else if (requestPath === `/api/features/${featureId}/qa-signoffs`) {
      payload = [];
    } else if (requestPath === `/api/features/${featureId}/interventions`) {
      payload = [];
    } else if (requestPath === "/api/runs") {
      payload = [run];
    } else if (requestPath === "/api/model-usage") {
      payload = modelUsage;
    } else if (requestPath === "/api/token-savior") {
      payload = tokenSavior;
    } else if (requestPath === "/api/slots") {
      payload = slots;
    } else if (requestPath === "/api/realtime/status") {
      payload = realtime;
    } else if (requestPath === `/api/features/${featureId}/recovery`) {
      payload = [];
    } else if (requestPath === `/api/features/${featureId}/runs/aggregate`) {
      payload = runsAggregate;
    } else {
      payload = [];
    }

    await route.fulfill({ json: payload });
  });
}

async function go(page, path) {
  await page.goto(path);
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("features list is renderable and shows shortcuts", async ({ page }) => {
  await go(page, "/features");
  await expect(page.getByRole("link", { name: "COMMAND CENTER / pgloom-engineering" })).toBeVisible();
  await expect(page.getByText("FEATURES")).toBeVisible();
  await expect(page.getByText("1 active")).toBeVisible();
  await expect(page.getByText("Operator surfaces")).toBeVisible();
  await expect(page.getByRole("link", { name: "Slot occupancy" })).toBeVisible();
});

test("feature overview shows plan progression", async ({ page }) => {
  await go(page, `/feature/${featureId}`);
  await expect(page.getByRole("heading", { name: /pgloom-engineering · feat\/command-center-ui/i })).toBeVisible();
  await expect(page.getByText(/MILESTONES|TASK SLICES/)).toBeVisible();
  await expect(page.getByText("1 / 2")).toBeVisible();
});

test("feature DAG and handoff routes render", async ({ page }) => {
  await go(page, `/feature/${featureId}/dag`);
  await expect(page.locator("svg.cc-dag-svg")).toBeVisible();
  await expect(page.getByText("2 tasks", { exact: false })).toBeVisible();

  await go(page, `/feature/${featureId}/handoffs`);
  await expect(page.getByText("HANDOFFS · 1")).toBeVisible();
  await expect(page.getByText("HANDOFF · handoff-1")).toBeVisible();
  await expect(page.locator(".cc-tasklink")).toHaveCount(4);

  await go(page, `/feature/${featureId}/task/t1`);
  await expect(page.getByRole("heading", { name: "impl-implement" })).toBeVisible();
  await expect(page.getByText("Evidence gallery")).toBeVisible();
  await expect(page.getByText("worktree_diff")).toBeVisible();

  await go(page, `/feature/${featureId}/councils`);
  await expect(page.getByText("COUNCILS · 2")).toBeVisible();
  await go(page, `/feature/${featureId}/councils/council_legacy_plan-1_0`);
  await expect(page.getByText("This council was projected from plan-contract JSON")).toBeVisible();

  await go(page, `/feature/${featureId}/telemetry`);
  await expect(page.getByText("TELEMETRY DETAIL", { exact: false })).toBeVisible();
  await expect(page.locator(".cc-runs-tbl .cc-tasklink")).toHaveCount(1);
});

test("global telemetry and realtime pages render", async ({ page }) => {
  await go(page, "/telemetry/tokens");
  await expect(page.getByRole("heading", { name: /Token economy/i })).toBeVisible();
  await expect(page.getByText("all projects · project breakdown · profile accounting")).toBeVisible();

  await go(page, "/telemetry/slots");
  await expect(page.getByText("GLOBAL · worker slot occupancy", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /planner active/ })).toBeVisible();

  await go(page, "/realtime");
  await expect(page.getByText("REALTIME · pg_notify('cc_events', ...)")).toBeVisible();
  await expect(page.getByText("channel cc_events")).toBeVisible();
});

test("top-left brand link returns to home features", async ({ page }) => {
  await go(page, `/feature/${featureId}/telemetry`);
  await page.getByRole("link", { name: "COMMAND CENTER / pgloom-engineering" }).click();
  await expect(page).toHaveURL("/features");
});
