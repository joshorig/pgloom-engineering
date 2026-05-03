# live-planner-autonomy-baseline

Output: `docs/reports/live-planner-suite-2026-05-03-095322-live-planner-autonomy-baseline`

Thresholds: failed

## Failures
- `{'case_id': 'dag-r006-wide', 'backend': 'codex', 'reason': 'production_grade_verdict', 'expected': 'accept', 'actual': 'revise', 'findings': [{'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root benchmarks/src/test/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}, {'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root benchmarks/src/test/java/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}, {'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root dag-framework-api/src/test/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}, {'code': 'qa_root_missing_for_verification', 'message': 'Verification requires QA root dag-framework-api/src/test/java/, but QA slices do not allow it.', 'severity': 'blocking', 'slice_id': None}]}`
- `{'case_id': 'dag-r006-wide', 'backend': 'codex', 'reason': 'production_grade_score', 'expected_min': 90, 'actual': 0}`

## Runs
- trp-r016-wide / codex / gpt-5.5: 0
- dag-r006-wide / codex / gpt-5.5: 0
