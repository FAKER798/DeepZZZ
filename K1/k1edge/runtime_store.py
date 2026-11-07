from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = {} if default is None else default.copy()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback
    return payload if isinstance(payload, dict) else fallback


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": SCHEMA_VERSION, **payload}
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def age_seconds(payload: dict[str, Any], now: float | None = None) -> float | None:
    updated_at = payload.get("updated_at")
    if updated_at is None:
        return None
    try:
        return round((time.time() if now is None else now) - float(updated_at), 1)
    except (TypeError, ValueError):
        return None


def is_stale(payload: dict[str, Any], max_age_s: float, now: float | None = None) -> bool:
    age = age_seconds(payload, now=now)
    return age is None or age > max_age_s

