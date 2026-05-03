from __future__ import annotations

import json


def extract_json(text: str) -> object:
    fenced = _extract_fenced_json(text)
    if fenced is not None:
        return json.loads(fenced)
    span = _last_balanced_json_span(text)
    if span is None:
        raise ValueError("no JSON object found in model response")
    return json.loads(span)


def _extract_fenced_json(text: str) -> str | None:
    marker = "```json"
    start = text.find(marker)
    if start < 0:
        return None
    body_start = text.find("\n", start)
    if body_start < 0:
        return None
    end = text.find("```", body_start + 1)
    if end < 0:
        return None
    return text[body_start + 1 : end].strip()


def _last_balanced_json_span(text: str) -> str | None:
    end = text.rfind("}")
    if end < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(end, -1, -1):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "}":
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0:
                return text[index : end + 1]
    return None
