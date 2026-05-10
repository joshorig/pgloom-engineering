from __future__ import annotations

import json


def extract_json(text: str) -> object:
    stripped = text.strip()
    if stripped:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    fenced = _extract_fenced_json(text)
    if fenced is not None:
        return json.loads(fenced)
    for span in reversed(_balanced_json_spans(text)):
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in model response")


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


def _balanced_json_spans(text: str) -> list[str]:
    spans: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue
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
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                spans.append(text[start : index + 1])
                start = None
    return spans
