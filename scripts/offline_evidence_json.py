"""Strict JSON boundary for offline evidence; never include input in errors."""
from __future__ import annotations

import json
import math
from pathlib import Path


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("offline_json_duplicate_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("offline_json_nonfinite_number")


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("offline_json_nonfinite_number")
    return result


def parse_offline_json(raw: bytes) -> dict:
    """Decode a UTF-8 object, allowing BOM and preserving the caller's raw bytes."""
    try:
        result = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_object,
            parse_constant=_constant, parse_float=_float,
        )
    except (ValueError, RecursionError):
        raise ValueError("offline_json_invalid") from None
    if not isinstance(result, dict):
        raise ValueError("offline_json_object_required")
    return result


def load_offline_json(path: Path) -> dict:
    return parse_offline_json(path.read_bytes())
