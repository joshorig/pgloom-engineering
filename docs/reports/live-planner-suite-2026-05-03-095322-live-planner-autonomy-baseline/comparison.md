# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| codex gpt-5.5/high | accepted | 2 | 10 | 679931 | 373071 | n/a | 2.504821 | accept (100) / prod accept (100) | 32030 saved (78.9%) | 7 |
| codex gpt-5.5/high | accepted | 2 | 10 | 245291 | 115360 | n/a | 1.539217 | accept (100) / prod revise (0) | 8790 saved (79.2%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
