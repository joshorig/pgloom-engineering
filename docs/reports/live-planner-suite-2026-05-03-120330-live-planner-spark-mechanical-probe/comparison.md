# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| codex gpt-5.5/high | accepted | 1 | 3 | 70671 | 26310 | n/a | 0.290667 | accept (100) / prod accept (100) | 25018 saved (85.4%) | 5 |
| codex gpt-5.5/high | accepted | 2 | 6 | 156601 | 68961 | n/a | 0.6255 | accept (100) / prod accept (100) | 8252 saved (78.0%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
