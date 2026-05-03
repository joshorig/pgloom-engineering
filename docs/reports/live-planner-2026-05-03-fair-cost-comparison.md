# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| claude sonnet | accepted | 1 | 4 | 173758 | 41269 | 1.074466 | 0.918528 | revise (30) | 6392 saved (65.8%) | 5 |
| claude sonnet | accepted | 1 | 5 | 267505 | 65684 | 1.471515 | 1.337814 | revise (30) | 10375 saved (71.0%) | 5 |
| codex gpt-5.5/high | accepted | 2 | 8 | 187783 | 77508 | n/a | 1.128495 | revise (50) | 6386 saved (65.7%) | 6 |
| codex gpt-5.5/high | accepted | 1 | 5 | 123945 | 55309 | n/a | 0.731457 | accept (100) | 10388 saved (71.1%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
