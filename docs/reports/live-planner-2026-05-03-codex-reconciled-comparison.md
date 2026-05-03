# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Cost USD | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| codex gpt-5.5/high | accepted | 2 | 8 | 187783 | 77508 | n/a | 6386 saved (65.7%) | 6 |
| codex gpt-5.5/high | accepted | 1 | 5 | 123945 | 55309 | n/a | 10388 saved (71.1%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
