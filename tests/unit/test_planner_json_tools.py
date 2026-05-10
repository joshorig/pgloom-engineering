from __future__ import annotations

import json

from pgloom_engineering.planner.json_tools import extract_json


def test_extract_json_accepts_raw_json_with_command_strings() -> None:
    payload = {
        "contract_version": "engineering.contracts.v1",
        "commands": [
            "./gradlew --no-daemon :benchmarks:jmhSmokeCheck -Pjmh.smoke=true",
            "javac -cp '/tmp/path with spaces/lib.jar' /tmp/RangeScanReplay.java",
        ],
        "findings": [
            {
                "summary": "mmap range smoke exceeded allocation threshold",
                "details": "allocated 0.039 B/op above 0.005 B/op",
            }
        ],
    }

    assert extract_json(json.dumps(payload)) == payload


def test_extract_json_accepts_balanced_object_with_trailing_noise() -> None:
    payload = {
        "contract_version": "engineering.contracts.v1",
        "verdict": "fail",
        "validation_evidence": [{"metadata": {"alloc_bytes_per_op": 0.039}}],
    }

    assert extract_json(json.dumps(payload) + "]") == payload
