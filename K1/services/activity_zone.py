from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from k1edge.runtime_store import read_json, write_json


BODY_POINT_THRESHOLD = 0.25
MIN_ZONE_AREA = 0.001


class ActivityZoneStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def status(self) -> dict[str, Any]:
        payload = read_json(self.path, default={})
        return {
            "zone": normalize_zone(payload.get("zone")),
            "updated_at": payload.get("updated_at"),
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("zone") is None:
            return self.clear()
        zone = normalize_zone(payload.get("zone"))
        if zone is None:
            raise ValueError("invalid safe zone")
        write_json(self.path, {"zone": zone, "updated_at": time.time()})
        return self.status()

    def clear(self) -> dict[str, Any]:
        write_json(self.path, {"zone": None, "updated_at": time.time()})
        return self.status()


def normalize_zone(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        left = clamp01(float(value["left"]))
        top = clamp01(float(value["top"]))
        right = clamp01(float(value["right"]))
        bottom = clamp01(float(value["bottom"]))
    except (KeyError, TypeError, ValueError):
        return None
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    if (right - left) * (bottom - top) < MIN_ZONE_AREA:
        return None
    return {
        "left": round(left, 4),
        "top": round(top, 4),
        "right": round(right, 4),
        "bottom": round(bottom, 4),
    }


def safe_zone_detection_status(
    zone_status: dict[str, Any],
    latest: dict[str, Any] | None,
) -> dict[str, Any]:
    zone = normalize_zone(zone_status.get("zone"))
    captured_at = latest.get("captured_at") if isinstance(latest, dict) else None
    updated_at = latest.get("updated_at") if isinstance(latest, dict) else None
    result = {
        "zone": zone,
        "triggered": False,
        "body_point_count": 0,
        "body_point_threshold": BODY_POINT_THRESHOLD,
        "updated_at": zone_status.get("updated_at"),
        "evaluated_at": updated_at or captured_at,
    }
    if zone is None or not isinstance(latest, dict):
        return result
    persons = latest.get("persons")
    person = persons[0] if isinstance(persons, list) and persons else None
    keypoints = person.get("keypoints") if isinstance(person, dict) else None
    frame_size = latest.get("frame_size")
    if not isinstance(keypoints, list) or not isinstance(frame_size, (list, tuple)) or len(frame_size) < 2:
        return result
    try:
        frame_width, frame_height = float(frame_size[0]), float(frame_size[1])
    except (TypeError, ValueError):
        return result
    if frame_width <= 0 or frame_height <= 0:
        return result

    body_points: list[tuple[float, float]] = []
    for point in keypoints:
        try:
            x, y, confidence = float(point[0]), float(point[1]), float(point[2])
        except (IndexError, TypeError, ValueError):
            continue
        if confidence >= BODY_POINT_THRESHOLD:
            body_points.append((x / frame_width, y / frame_height))
    result["body_point_count"] = len(body_points)
    result["triggered"] = bool(body_points) and any(
        x < zone["left"] or x > zone["right"] or y < zone["top"] or y > zone["bottom"]
        for x, y in body_points
    )
    return result


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
