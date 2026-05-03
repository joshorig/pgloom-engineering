# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Cost USD | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| claude sonnet | accepted | 1 | 4 | 173758 | 41269 | 1.074466 | 6392 saved (65.8%) | 5 |
| claude sonnet | accepted | 1 | 5 | 267505 | 65684 | 1.471515 | 10375 saved (71.0%) | 5 |
| codex gpt-5.5/high | exhausted | n/a | 4 | 99112 | 33471 | n/a | 6392 saved (65.8%) | 0 |
| codex gpt-5.5/high | exhausted | n/a | 5 | 130902 | 52469 | n/a | 10388 saved (71.1%) | 0 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
