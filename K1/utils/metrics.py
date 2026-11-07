from __future__ import annotations

from collections import deque
from typing import Any
import time


def age_seconds(epoch_seconds: Any, now: float | None = None) -> float | None:
    try:
        value = float(epoch_seconds)
    except (TypeError, ValueError):
        return None
    now = time.time() if now is None else now
    age = now - value
    if age < 0:
        return None
    return round(age, 3)


def recent_rate(timestamps: deque[float], now: float | None = None, window_s: float = 5.0) -> float | None:
    now = time.time() if now is None else now
    recent = [value for value in timestamps if now - value <= window_s]
    if len(recent) < 2:
        return None
    span = recent[-1] - recent[0]
    if span <= 0:
        return None
    return round((len(recent) - 1) / span, 2)
