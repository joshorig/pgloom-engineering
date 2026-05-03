# Planner Model Usage Comparison

| Run | Status | Iteration | Calls | Actual tokens | Estimated tokens | Observed cost USD | API-equiv cost USD | Quality | Token Savior | Slices |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| claude sonnet | accepted | 1 | 4 | 206123 | 39877 | 0.923716 | 0.891467 | accept (100) / prod accept (100) | 15615 saved (84.8%) | 5 |
| codex gpt-5.5/high | accepted | 1 | 4 | 89121 | 34314 | n/a | 0.495298 | accept (100) / prod accept (100) | 15615 saved (84.8%) | 5 |
| claude sonnet | accepted | 1 | 5 | 280881 | 66511 | 1.435938 | 1.30492 | accept (100) / prod accept (100) | 10556 saved (72.2%) | 6 |
| codex gpt-5.5/high | accepted | 1 | 5 | 117829 | 49466 | n/a | 0.58966 | accept (100) / prod accept (100) | 10556 saved (72.2%) | 6 |
| claude sonnet | accepted | 2 | 8 | 316228 | 105185 | 1.571509 | 1.483878 | accept (100) / prod accept (100) | 24869 saved (83.9%) | 5 |
| codex gpt-5.5/high | accepted | 2 | 8 | 196182 | 90621 | n/a | 1.070283 | accept (100) / prod accept (95) | 24869 saved (83.9%) | 5 |
| claude sonnet | accepted | 2 | 10 | 601761 | 205785 | 3.463701 | 2.841273 | accept (100) / prod accept (100) | 31943 saved (78.7%) | 6 |
| codex gpt-5.5/high | accepted | 1 | 5 | 130964 | 65043 | n/a | 0.815404 | accept (100) / prod revise (45) | 31943 saved (78.7%) | 5 |
| codex gpt-5.5/high | accepted | 1 | 4 | 93784 | 39729 | n/a | 0.591986 | accept (100) / prod accept (100) | 5636 saved (61.2%) | 6 |
| codex gpt-5.5/high | accepted | 1 | 4 | 91827 | 38423 | n/a | 0.540128 | accept (100) / prod accept (85) | 6379 saved (61.1%) | 6 |
| codex gpt-5.5/high | accepted | 2 | 10 | 237189 | 105399 | n/a | 1.420092 | accept (100) / prod revise (50) | 8709 saved (78.5%) | 5 |

Notes:
- Codex CLI text output exposes actual total tokens via a trailing 'tokens used' line.
- Claude text-mode artifacts from this run do not expose provider usage, so Claude token totals are estimated from prompt/response characters.
- Claude JSON output includes total_cost_usd when available.
- Codex CLI text output from these runs exposes tokens but not USD cost.
- api_equivalent_cost_usd is computed from input/cache/output token splits when pricing is configured.
