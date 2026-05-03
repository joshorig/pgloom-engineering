# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| claude sonnet | accepted | 1 | 4 | 216026 | 43789 | 1.030555 | 0.899385 | accept (100) / prod accept (100) | 5611 saved (60.9%) | 6 |
| claude sonnet | accepted | 1 | 4 | 188022 | 49406 | 0.95648 | 0.919143 | accept (100) / prod accept (100) | 6336 saved (60.7%) | 6 |
| claude sonnet | accepted | 2 | 10 | 444485 | 173753 | 2.765229 | 2.365626 | accept (100) / prod accept (100) | 8625 saved (77.7%) | 6 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
