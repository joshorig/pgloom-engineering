# live-planner-autonomy-baseline

Output: `docs/reports/live-planner-suite-2026-05-03-013448-live-planner-autonomy-baseline`

Thresholds: failed

## Failures
- `{'case_id': 'trp-r003-small', 'backend': 'claude', 'reason': 'api_equivalent_cost', 'expected_max': 1.25, 'actual': 1.483878}`
- `{'case_id': 'trp-r016-wide', 'backend': 'claude', 'reason': 'api_equivalent_cost', 'expected_max': 1.75, 'actual': 2.841273}`
- `{'case_id': 'trp-r016-wide', 'backend': 'codex', 'reason': 'production_grade_verdict', 'expected': 'accept', 'actual': 'revise', 'findings': [{'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root src/test/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}, {'code': 'qa_root_not_registered', 'message': 'Required QA root is not registered/discovered: src/test/', 'severity': 'blocking', 'slice_id': None}]}`
- `{'case_id': 'trp-r016-wide', 'backend': 'codex', 'reason': 'production_grade_score', 'expected_min': 90, 'actual': 45}`
- `{'case_id': 'dag-r001-medium', 'backend': 'codex', 'reason': 'production_grade_score', 'expected_min': 90, 'actual': 85}`
- `{'case_id': 'dag-r006-wide', 'backend': 'codex', 'reason': 'production_grade_verdict', 'expected': 'accept', 'actual': 'revise', 'findings': [{'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root src/test/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}, {'code': 'qa_root_not_registered', 'message': 'Required QA root is not registered/discovered: src/test/', 'severity': 'blocking', 'slice_id': None}]}`
- `{'case_id': 'dag-r006-wide', 'backend': 'codex', 'reason': 'production_grade_score', 'expected_min': 90, 'actual': 50}`

## Runs
- lvc-r003-small / claude / sonnet: 0
- lvc-r003-small / codex / gpt-5.5: 0
- lvc-r002-wide / claude / sonnet: 0
- lvc-r002-wide / codex / gpt-5.5: 0
- trp-r003-small / claude / sonnet: 0
- trp-r003-small / codex / gpt-5.5: 0
- trp-r016-wide / claude / sonnet: 0
- trp-r016-wide / codex / gpt-5.5: 0
- dag-r003-small / claude / sonnet: 1
- dag-r003-small / codex / gpt-5.5: 0
- dag-r001-medium / claude / sonnet: 1
- dag-r001-medium / codex / gpt-5.5: 0
- dag-r006-wide / claude / sonnet: 1
- dag-r006-wide / codex / gpt-5.5: 0
