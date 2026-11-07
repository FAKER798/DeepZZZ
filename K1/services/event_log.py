"""Vision / cry events for the naviga app (/api/events, /api/events/summary).

Once a second the main service hands EventLog.observe() the current vision and YAMNet
state; it turns them into events with a start, an end and a duration, saved in
runtime/care/events.json (see 接口文档.md for the API contract).

Event types (the app's BoardEventType):
    cry                  YAMNet cry state machine says crying
    face_cover_rollover  nose and both eyes not visible (face down / covered), or the baby
                         lies on its side (shoulders seen edge-on); every roll-over away
                         from the back shows up as one event in the app ("遮脸/翻身")
    boundary             a trusted body keypoint is outside the safe zone
    no_person            nobody detected

Each input is True / False / None. None means "can't tell right now" (camera off,
pose result stale, YOLO paused for the local LLM, YAMNet not ready): it neither starts
nor ends an event, and after UNKNOWN_INTERRUPT_S an ongoing event is closed at its last
confirmed moment with interrupted=True.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from k1edge.runtime_store import read_json, write_json

EVENT_TYPES = ("cry", "face_cover_rollover", "boundary", "no_person")
SOURCES = {"cry": "yamnet", "face_cover_rollover": "vision", "boundary": "vision", "no_person": "vision"}
VISION_STATES = ("visible", "covered", "no_person", "paused", "stale", "unknown")


@dataclass(frozen=True)
class Rule:
    start_s: float  # condition must hold this long before an event starts
    end_s: float  # condition must be false this long before the event ends


RULES = {
    "cry": Rule(start_s=0.0, end_s=3.0),  # the YAMNet state machine already smooths
    "face_cover_rollover": Rule(start_s=2.0, end_s=5.0),
    "boundary": Rule(start_s=2.0, end_s=5.0),
    "no_person": Rule(start_s=5.0, end_s=5.0),
}
MERGE_GAP_S = 30.0  # same type again within 30 s of the end -> same event continues
ROLL_FROM_BACK_S = 60.0  # face up this recently before a face_cover_rollover -> from_posture "back"
UNKNOWN_INTERRUPT_S = 120.0
FLUSH_INTERVAL_S = 30.0  # rewrite last_seen_at of ongoing events this often
RETENTION_S = 7 * 86400.0
MAX_EVENTS = 5000
SAMPLE_MAX_AGE_S = 10.0  # current vision_state older than this -> "unknown"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MIN_WINDOW_S = 60.0
MAX_WINDOW_S = 604800.0

# ---------------------------------------------------------------- pose -> posture

KEYPOINT_THRESHOLD = 0.25  # same as the face / safe-zone checks
NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR = 0, 1, 2, 3, 4
LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP = 5, 6, 11, 12
# Seen from above, a baby on its back has shoulders about as wide as the shoulder-to-hip
# distance (ratio ~0.8-1.0); on its side the shoulders stack up and the ratio drops.
SIDE_SHOULDER_RATIO = 0.4
MIN_TORSO_PX = 8.0  # torso shorter than this (in 320x180 frames) is too small to judge


class EventLogError(ValueError):
    """Bad query parameter -> HTTP 400."""


def _point(keypoints: list[Any], index: int) -> tuple[float, float, float]:
    try:
        x, y, confidence = keypoints[index][:3]
        return float(x), float(y), float(confidence or 0.0)
    except (IndexError, TypeError, ValueError):
        return 0.0, 0.0, 0.0


def classify_posture(keypoints: list[Any], side_ratio: float = SIDE_SHOULDER_RATIO) -> dict[str, Any]:
    """posture: back (face up) | side | face_down (nose and eyes hidden: prone or covered).

    side_ratio <= 0 turns side-lying detection off (only face_down counts).
    """
    seen = [_point(keypoints, i)[2] >= KEYPOINT_THRESHOLD for i in range(max(17, len(keypoints)))]
    face_visible = seen[NOSE] or seen[LEFT_EYE] or seen[RIGHT_EYE]
    result: dict[str, Any] = {"posture": "back", "shoulder_ratio": None}
    if not face_visible:
        result["posture"] = "face_down"
        return result

    if not (seen[LEFT_SHOULDER] and seen[RIGHT_SHOULDER] and (seen[LEFT_HIP] or seen[RIGHT_HIP])):
        return result  # body hidden (blanket): face is visible, nothing more to judge
    ls, rs = _point(keypoints, LEFT_SHOULDER), _point(keypoints, RIGHT_SHOULDER)
    hips = [_point(keypoints, i) for i in (LEFT_HIP, RIGHT_HIP) if seen[i]]
    shoulder_mid = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hip_mid = (sum(h[0] for h in hips) / len(hips), sum(h[1] for h in hips) / len(hips))
    torso = math.dist(shoulder_mid, hip_mid)
    if torso < MIN_TORSO_PX:
        return result
    ratio = math.dist(ls[:2], rs[:2]) / torso
    result["shoulder_ratio"] = round(ratio, 3)
    both_eyes = seen[LEFT_EYE] and seen[RIGHT_EYE]
    if side_ratio > 0 and ratio < side_ratio and not both_eyes:
        result["posture"] = "side"
    return result


# ---------------------------------------------------------------- helpers


def to_seconds(value: Any, name: str) -> float | None:
    """Query time in seconds or milliseconds (> 1e11 is taken as ms, which the app sends)."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EventLogError(f"{name} must be a number") from None
    if not math.isfinite(number) or number < 0:
        raise EventLogError(f"{name} must be a non-negative number")
    return number / 1000.0 if number > 1e11 else number


def parse_limit(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_LIMIT
    try:
        limit = int(str(value))
    except ValueError:
        raise EventLogError("limit must be an integer") from None
    if limit < 1:
        raise EventLogError("limit must be at least 1")
    return min(limit, MAX_LIMIT)


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


class _Track:
    """Debounce state of one event type (not persisted)."""

    def __init__(self) -> None:
        self.true_since: float | None = None  # start of the current run of True samples
        self.false_since: float | None = None  # first False sample since the last True
        self.unknown_since: float | None = None


class EventLog:
    def __init__(
        self,
        path: Path,
        clock: Callable[[], float] = time.time,
        rules: dict[str, Rule] | None = None,
    ) -> None:
        self.path = path
        self.clock = clock
        self.rules = rules or RULES
        self._lock = threading.Lock()
        self._tracks = {name: _Track() for name in EVENT_TYPES}
        self._current: dict[str, Any] = {"vision_state": "unknown", "vision_state_at": None}
        self._last_flush = 0.0
        self._last_back_at: float | None = None
        self._events: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------ storage

    def _load(self) -> None:
        payload = read_json(self.path, default={"events": []})
        events = [e for e in payload.get("events") or [] if isinstance(e, dict) and e.get("type") in EVENT_TYPES]
        changed = False
        for event in events:
            if event.get("end_at") is None:  # the service stopped while it was ongoing
                event["end_at"] = event.get("last_seen_at") or event.get("start_at")
                event["interrupted"] = True
                changed = True
        self._events = events
        if self._prune(self.clock()) or changed:
            self._save()

    def _save(self) -> None:
        write_json(self.path, {"events": self._events})
        self._last_flush = self.clock()

    def _prune(self, now: float) -> bool:
        before = len(self._events)
        keep = [e for e in self._events if e.get("end_at") is None or float(e["end_at"]) >= now - RETENTION_S]
        keep.sort(key=lambda e: float(e.get("start_at") or 0.0))
        self._events = keep[-MAX_EVENTS:]
        return len(self._events) != before

    def _ongoing(self, event_type: str) -> dict[str, Any] | None:
        for event in reversed(self._events):
            if event["type"] == event_type and event.get("end_at") is None:
                return event
        return None

    def _latest(self, event_type: str) -> dict[str, Any] | None:
        for event in reversed(self._events):
            if event["type"] == event_type:
                return event
        return None

    # ------------------------------------------------------------ sampling

    def observe(
        self,
        observed: dict[str, bool | None],
        now: float | None = None,
        vision_state: str = "unknown",
        details: dict[str, Any] | None = None,
    ) -> None:
        """One sample: observed[type] is True / False / None (unknown) for each event type.

        details may carry cry_score (kept as peak_score) and posture (kept on the
        face_cover_rollover event: side / face_down, and the posture it rolled from).
        """
        now = self.clock() if now is None else float(now)
        details = details or {}
        dirty = False
        with self._lock:
            self._current = {
                "vision_state": vision_state if vision_state in VISION_STATES else "unknown",
                "vision_state_at": now,
                "posture": details.get("posture"),
                "shoulder_ratio": details.get("shoulder_ratio"),
            }
            if details.get("posture") == "back":
                self._last_back_at = now
            for event_type in EVENT_TYPES:
                dirty |= self._step(event_type, observed.get(event_type), now, details)
            if dirty:
                self._prune(now)
                self._save()
            elif any(e.get("end_at") is None for e in self._events) and now - self._last_flush >= FLUSH_INTERVAL_S:
                self._save()

    def _step(self, event_type: str, value: bool | None, now: float, details: dict[str, Any]) -> bool:
        track = self._tracks[event_type]
        rule = self.rules[event_type]
        event = self._ongoing(event_type)
        if value is None:
            if track.unknown_since is None:
                track.unknown_since = now
            # A run of True / False must be seen without gaps to start / end an event.
            track.true_since = track.false_since = None
            if event is not None and now - track.unknown_since >= UNKNOWN_INTERRUPT_S:
                self._finish(event, interrupted=True)
                track.true_since = track.false_since = None
                return True
            return False
        track.unknown_since = None

        if value:
            track.false_since = None
            if track.true_since is None:
                track.true_since = now
            if event is not None:
                event["last_seen_at"] = now
                self._update_details(event, details)
                return False
            if now - track.true_since < rule.start_s:
                return False
            self._begin(event_type, track.true_since, now, details)
            return True

        track.true_since = None
        if event is None:
            return False
        if track.false_since is None:
            track.false_since = now
        if now - track.false_since >= rule.end_s:
            self._finish(event)
            track.false_since = None
            return True
        return False

    def _begin(self, event_type: str, start_at: float, now: float, details: dict[str, Any]) -> None:
        previous = self._latest(event_type)
        if previous is not None and previous.get("end_at") is not None and start_at - float(previous["end_at"]) <= MERGE_GAP_S:
            previous["end_at"] = None
            previous.pop("interrupted", None)
            previous["merged"] = int(previous.get("merged") or 0) + 1
            previous["last_seen_at"] = now
            self._update_details(previous, details)
            return
        event: dict[str, Any] = {
            "id": f"{event_type}-{int(round(start_at * 1000))}",
            "type": event_type,
            "source": SOURCES[event_type],
            "start_at": start_at,
            "end_at": None,
            "last_seen_at": now,
        }
        if event_type == "face_cover_rollover":
            # Seen face up shortly before: a real roll-over rather than e.g. a blanket
            # that was already over the face when the camera started.
            if self._last_back_at is not None and start_at - self._last_back_at <= ROLL_FROM_BACK_S:
                event["from_posture"] = "back"
        self._update_details(event, details)
        self._events.append(event)

    def _update_details(self, event: dict[str, Any], details: dict[str, Any]) -> None:
        if event["type"] == "cry" and details.get("cry_score") is not None:
            event["peak_score"] = round(max(float(event.get("peak_score") or 0.0), float(details["cry_score"])), 4)
        elif event["type"] == "face_cover_rollover" and details.get("posture") in {"side", "face_down"}:
            event["posture"] = details["posture"]

    def _finish(self, event: dict[str, Any], interrupted: bool = False) -> None:
        event["end_at"] = event.get("last_seen_at") or event["start_at"]
        if interrupted:
            event["interrupted"] = True

    # ------------------------------------------------------------ queries

    def _public(self, event: dict[str, Any], now: float) -> dict[str, Any]:
        end = event.get("end_at")
        ongoing = end is None
        duration = (now if ongoing else float(end)) - float(event["start_at"])
        result = {
            "id": event["id"],
            "type": event["type"],
            "source": event["source"],
            "start_at": _round(event["start_at"]),
            "end_at": _round(end),
            "duration_s": round(max(0.0, duration), 1),
            "ongoing": ongoing,
            "last_seen_at": _round(event.get("last_seen_at")),
        }
        for key in ("peak_score", "merged", "interrupted", "posture", "from_posture"):
            if event.get(key) is not None:
                result[key] = event[key]
        return result

    def list_events(
        self,
        since: Any = None,
        until: Any = None,
        event_type: Any = None,
        limit: Any = None,
    ) -> list[dict[str, Any]]:
        since_s = to_seconds(since, "since")
        until_s = to_seconds(until, "until")
        if event_type not in (None, "") and event_type not in EVENT_TYPES:
            raise EventLogError("type must be one of " + ", ".join(EVENT_TYPES))
        count = parse_limit(limit)
        now = self.clock()
        with self._lock:
            events = list(self._events)
        result = []
        for event in sorted(events, key=lambda e: float(e["start_at"]), reverse=True):
            if event_type and event["type"] != event_type:
                continue
            end = now if event.get("end_at") is None else float(event["end_at"])
            if since_s is not None and end < since_s:
                continue
            if until_s is not None and float(event["start_at"]) > until_s:
                continue
            result.append(self._public(event, now))
            if len(result) >= count:
                break
        return result

    def summary(self, window_s: Any = None) -> dict[str, Any]:
        if window_s is None or window_s == "":
            window = 7200.0
        else:
            try:
                window = float(window_s)
            except (TypeError, ValueError):
                raise EventLogError("window_s must be a number") from None
            if not (MIN_WINDOW_S <= window <= MAX_WINDOW_S):
                raise EventLogError(f"window_s must be between {int(MIN_WINDOW_S)} and {int(MAX_WINDOW_S)}")
        now = self.clock()
        since = now - window
        with self._lock:
            events = list(self._events)
            current = dict(self._current)
        state_at = current.get("vision_state_at")
        state = current.get("vision_state") or "unknown"
        if state_at is None or now - float(state_at) > SAMPLE_MAX_AGE_S:
            state = "unknown"
        types: dict[str, Any] = {}
        for event_type in EVENT_TYPES:
            matching = []
            total = 0.0
            for event in events:
                if event["type"] != event_type:
                    continue
                end = now if event.get("end_at") is None else float(event["end_at"])
                if end < since:
                    continue
                matching.append(event)
                total += max(0.0, end - max(float(event["start_at"]), since))
            latest = max(matching, key=lambda e: float(e["start_at"])) if matching else None
            types[event_type] = {
                "count": len(matching),
                "total_s": round(total, 1),
                "latest": self._public(latest, now) if latest else None,
            }
        rollovers = [
            e for e in events
            if e["type"] == "face_cover_rollover" and (now if e.get("end_at") is None else float(e["end_at"])) >= since
        ]
        types["face_cover_rollover"]["side_count"] = sum(1 for e in rollovers if e.get("posture") == "side")
        types["face_cover_rollover"]["face_down_count"] = sum(1 for e in rollovers if e.get("posture") == "face_down")
        active = [e["type"] for e in events if e.get("end_at") is None]
        return {
            "now": round(now, 3),
            "since": round(since, 3),
            "window_s": window,
            "current": {
                "vision_state": state,
                "vision_state_at": _round(state_at),
                "active": [t for t in EVENT_TYPES if t in active],
                "posture": current.get("posture") if state not in {"unknown", "stale", "paused"} else None,
                "shoulder_ratio": current.get("shoulder_ratio") if state not in {"unknown", "stale", "paused"} else None,
            },
            "types": types,
        }
