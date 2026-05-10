# Implementor brief — Token Savior advanced (MCP server mode + vector retrieval)

> **Status: STUB.** Do not implement until the basic Token Savior wiring brief at `docs/prompts/token-savings-fix-and-prefix-cache.md` is shipped, accepted, and producing real `tokens_saved` rows on `lvc-standard` R‑002. AND until at least one of the heavy code‑reading roles (Implementer, Reviewer, QA Engineer) has a concrete implementation brief in flight. Both prerequisites must be true; otherwise this brief is integration without a consumer.

---

## 1. What this brief will cover

The basic wiring brief (`token-savings-fix-and-prefix-cache.md`) installs `token-savior-recall` v2.6.0 as a path dep and turns on the **in‑process Python import** path. That covers the Planner's project‑context packing.

This brief turns on the two **optional extras** that pay off only when heavier code‑reading roles exist:

- **`mcp` extra**: Token Savior runs as an MCP server (Model Context Protocol). Implementer / Reviewer / QA can call its 105 tools — symbol lookup, call‑graph navigation, file‑slice retrieval — through the standard MCP toolchain rather than dumping whole files into context. The 97% reduction claim from the upstream README applies to flows where the model would otherwise `cat` files; it does not help the Planner because the Planner does not read source code.
- **`memory-vector` extra**: adds `sqlite-vec` + `sentence-transformers` for semantic similarity retrieval. Useful for "find existing tests similar to the one I'm about to write" (QA author), "find prior reviews that touched this surface" (Reviewer), "find similar implementations" (Implementer). Lighter than Zilliz `claude-context` (#10 in the linked tweet) and avoids a separate vector store.

Together they cover the problem space of the linked tweet's #3 (code‑review‑graph) and #10 (Zilliz claude‑context) without adding parallel infrastructure.

The full strategy lives in `docs/notes/token-economy.md` § 4 (rows 4 + 10) and § 5.

---

## 2. Hard prerequisites

This brief is unsafe to start until **all** are true:

1. The basic Token Savior wiring brief at `docs/prompts/token-savings-fix-and-prefix-cache.md` is fully landed; `import token_savior` works in the dev + CI envs; `engineering_token_savior_usage` rows show real `tokens_saved` from the in‑process import path with `metadata.method="token_savior_pack_context"`.
2. At least one of: Implementer, Reviewer, QA Engineer has an implementation brief landed (not necessarily shipped — but a brief that the integration in this brief can target).
3. Track D (worktree + git + GitHub) is at least partially landed — code‑reading roles need a worktree to point Token Savior at.
4. The replay regression eval framework from the basic brief (`scripts/replay_token_economy_regression.py`) is in place, so this brief can re‑use it for quality gating.

If any prerequisite is missing, write the missing piece first.

---

## 3. Scope (high‑level — to expand on full write‑up)

### 3.1 Token Savior MCP server mode

- Install with `[mcp]` extra: `token-savior-recall[mcp]` (pulls in `mcp>=1.25`).
- Stand up a Token Savior MCP server process via the `token-savior` console script (entry point declared in upstream `pyproject.toml:[project.scripts]`).
- Either run it as a sidecar daemon (one per host, indexes the project worktrees on start) or invoke per‑task (slower but hermetic). Decision: per‑host sidecar for production; per‑task for tests.
- Implementer / Reviewer / QA TaskContracts gain an `mcp_tools_available: list[str]` field that lists the Token Savior tool names exposed to that handler. The handler's CLIModelProvider invocation passes `--mcp-config` referencing the Token Savior server.
- `pgloom_engineering/integrations/token_savior_mcp.py` — small client wrapper: lifecycle (start/stop sidecar), tool discovery, health check.

### 3.2 `memory-vector` extra for similarity retrieval

- Install with `[memory-vector]` extra: pulls in `sqlite-vec` and `sentence-transformers`.
- Index target: per‑project worktree, indexed on Token Savior init or refresh. Index lives at `<project_root>/.token-savior/vector.db` (or wherever Token Savior puts it; consult the upstream README at integration time).
- New helpers in `pgloom_engineering/integrations/token_savior_vector.py`:
  - `find_similar_tests(project, target_test_path, k=5) -> list[TestNeighbor]`
  - `find_similar_reviews(project, slice_objective, k=5) -> list[ReviewNeighbor]`
  - `find_similar_implementations(project, slice_objective, allowed_paths, k=5) -> list[CodeNeighbor]`
- Each result includes the path, a short snippet, and a similarity score. Consumers decide whether to pull more.

### 3.3 Recording

Every MCP tool invocation and every vector query records to `engineering_token_savior_usage` with:

- `metadata.method = "token_savior_mcp_tool"` or `"token_savior_vector_query"`
- `metadata.tool = "<tool_name>"` (for MCP)
- `metadata.role = "implementer" | "reviewer" | "qa.author" | "qa.verify.scrutiny" | "qa.verify.usertest"`
- `tokens_before = "what this role would have sent if it had cat'd the file"` (estimated against the file size)
- `tokens_after = "size of what Token Savior actually returned"`

The `tokens_before` estimate is necessarily approximate. Document the heuristic in code comments; the dashboard cares about ratio not absolute.

### 3.4 Sidecar lifecycle (production)

- Systemd unit `pgloom-engineering-token-savior.service` starts the MCP server on host boot, indexes registered project roots, exposes a Unix socket.
- `pgloom-engineering` CLI gains `token-savior status` / `token-savior reindex --project <name>` verbs.
- Health check integrates with `pgloom.health` — if the sidecar is down, code‑reading roles fall back to `cat` (with a warning logged and a `metadata.fallback_reason` recorded).
- Per‑project resource lock for reindex operations (large projects can take minutes).

### 3.5 Quality gating

Run the replay regression eval from the basic brief, plus a new code‑reading replay corpus that exercises the heavy roles. Same thresholds: token reduction must be real, validator/critic counts must not increase by > 10%, iterations must not increase by > 1.

### 3.6 Comparison test against Zilliz (defer or run once)

If we ever want to revisit Zilliz `claude-context` (#10 in the tweet), this brief's vector retrieval is the baseline to beat. Run a one‑shot comparison: same retrieval queries, both backends, compare reduction ratio and downstream PlanContract / ReviewVerdict / QAResult quality. If Zilliz wins decisively, we add it; otherwise Token Savior's `memory-vector` is the durable choice.

---

## 4. Open design questions to resolve when fully writing this brief

- **Per‑task vs per‑host MCP server.** Per‑task is hermetic and easier to test; per‑host is lower latency and amortizes indexing cost. Likely answer: per‑task for tests, per‑host for production, with the MCP client transparent to the consumer.
- **Reindex policy.** When does the vector index refresh? On every commit to a tracked project, or on a schedule, or lazy on first query after a configurable staleness? Token Savior may already have a policy; consult upstream.
- **Cross‑project queries.** Does any role ever need to retrieve from a project other than the one its TaskContract names? Probably no — keep cross‑project queries explicitly disabled until a real need surfaces.
- **MCP tool allowlist per role.** Token Savior exposes 105 tools. Which subset does each role actually need? Likely Implementer needs symbol‑lookup + call‑graph; Reviewer needs diff‑annotation + similar‑review; QA needs test‑file‑listing + similar‑test. Define the allowlists once roles' briefs are concrete.
- **Cost of `sentence-transformers`.** Loads a model into memory; first query is slow. Acceptable for sidecar mode; problematic for per‑task. Likely the answer to "per‑task vs per‑host" is forced by this — per‑host wins for vector queries, per‑task may still work for MCP symbol queries.

---

## 5. Out of scope

- Reimplementing any of Token Savior's functionality.
- Writing the Implementer / Reviewer / QA handlers themselves (separate briefs).
- Adopting Zilliz `claude-context` — explicitly deferred behind Token Savior's vector capability.
- Master plan edits.

---

## 6. Reference paths

| What | Where |
|---|---|
| Strategy doc | `docs/notes/token-economy.md` |
| Basic Token Savior wiring brief (must be done first) | `docs/prompts/token-savings-fix-and-prefix-cache.md` |
| Token Savior local checkout | `/Volumes/devssd/repos/oss/token-savior` (`token-savior-recall` v2.6.0) |
| Token Savior upstream README + tool list | `/Volumes/devssd/repos/oss/token-savior/README.md` |
| Token Savior MCP server entry point | `token_savior.server:main_sync` (per `pyproject.toml:[project.scripts]`) |
| MCP spec | https://modelcontextprotocol.io |
| Zilliz claude‑context (alternative considered) | https://github.com/zilliztech/claude-context |
| Tweet thread (source of the survey) | https://x.com/rodmanai/status/2050604420870852654 |

---

## 7. Until this brief is fully written

- The basic Token Savior wiring brief covers the Planner's needs entirely. Do not over‑build for roles that don't exist yet.
- If implementing Implementer / Reviewer / QA reveals that the basic in‑process import is insufficient for code reading (e.g. cold‑start latency, indexing scope mismatch), capture the finding in `docs/reports/<role>-completion.md` under "Token Savior gaps" so this brief picks it up.
- If a quick experiment with the `mcp` or `memory-vector` extras would unblock a specific decision, run it against `lvc-standard` and record the result in `docs/reports/`. This brief will pick up your findings rather than re‑deriving them.
- Do not begin implementation against this stub. The fully‑written version will be drafted after the basic wiring is in production AND a code‑reading role is in flight.
