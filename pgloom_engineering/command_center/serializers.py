from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

USD_MICROS = 1_000_000


def usd_to_micros(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int((value * USD_MICROS).to_integral_value())
    return int(round(float(value) * USD_MICROS))


def iso_z(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return iso_z(value)
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.endswith("_usd"):
            out[f"{key}_micros"] = usd_to_micros(value)
            continue
        out[key] = json_value(value)
    return out


def serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [serialize_row(row) for row in rows]
