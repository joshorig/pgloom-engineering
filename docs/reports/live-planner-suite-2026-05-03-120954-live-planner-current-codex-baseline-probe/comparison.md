# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| codex gpt-5.5/high | accepted | 1 | 3 | 67241 | 26734 | n/a | 0.339755 | accept (100) / prod accept (100) | 25018 saved (85.4%) | 5 |
| codex gpt-5.5/high | accepted | 2 | 7 | 174689 | 82981 | n/a | 1.149774 | accept (100) / prod accept (100) | 8252 saved (78.0%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
