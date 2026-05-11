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


def test_extract_json_uses_codex_agent_message_from_jsonl() -> None:
    payload = {"contract_version": "engineering.contracts.v1", "feature_id": "wf_1"}
    response = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(payload),
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    assert extract_json(response) == payload


def test_extract_json_ignores_codex_transport_events_without_agent_message() -> None:
    response = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
        ]
    )

    try:
        extract_json(response)
    except ValueError as exc:
        assert str(exc) == "no JSON object found in model response"
    else:
        raise AssertionError("expected no JSON object")
