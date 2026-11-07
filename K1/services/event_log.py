"""Vision / sound episodes for the naviga app and the AI Q&A context.

AppRuntime calls EventLog.observe() once a second with the current state of each
event type (True / False, or None when that input is unknown right now: camera off,
pose result stale, LLM answering with YOLO paused, YAMNet warming up). The log turns
those samples into episodes:

    {"id": "...", "type": "cry", "source": "yamnet", "start_at": 1790000000.0,
     "end_at": 1790000180.0, "duration_s": 180.0, "ongoing": false, "peak_score": 0.83}

- An episode opens after the condition has been true for `open_s` (pose rules are
  single-frame, so face cover needs 2 s; YAMNet's own tracker already smooths cry).
- It closes after the condition has been false for `close_s`; the end time is the last
  sample that was still true.
- A new episode within `merge_s` of the previous one of the same type reopens it, so
  a baby who keeps rolling back and forth is one episode, not ten.
- Unknown samples never open or close anything. If an open episode stays unknown for
  `unknown_close_s` (camera died, service stopped watching) it is closed at its last
  true sample and marked "interrupted".

Episodes are kept for 7 days (max 5000) in runtime/care/events.json.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from k1edge.runtime_store import read_json, write_json

EVENT_TYPES = ("cry", "face_cover_rollover", "boundary", "no_person")
EVENT_SOURCES = {"cry": "yamnet", "face_cover_rollover": "vision", "boundary": "vision", "no_person": "vision"}
VISION_STATES = ("visible", "covered", "no_person", "paused", "stale", "unknown")
KEEP_S = 7 * 86400
MAX_EVENTS = 5000
MAX_LIST = 1000
DEFAULT_LIST = 100
MIN_WINDOW_S = 60.0
MAX_WINDOW_S = 7 * 86400.0
VISION_STATE_MAX_AGE_S = 10.0
OPEN_PERSIST_S = 30.0  # rewrite the file this often while an episode is open (keeps last_seen_at fresh)


class EventLogError(Exception):
    """Bad query parameters; maps to a plain JSON error."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EventRule:
    open_s: float
    close_s: float
    merge_s: float


DEFAULT_RULES = {
    "cry": EventRule(open_s=0.0, close_s=3.0, merge_s=30.0),
    "face_cover_rollover": EventRule(open_s=2.0, close_s=5.0, merge_s=30.0),
    "boundary": EventRule(open_s=2.0, close_s=5.0, merge_s=30.0),
    "no_person": EventRule(open_s=5.0, close_s=5.0, merge_s=30.0),
}


def parse_time(value: Any, name: str) -> float | None:
    """Seconds or milliseconds since the epoch (the app sends milliseconds)."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EventLogError(f"{name} must be a number (seconds or milliseconds since epoch)") from None
    if number < 0:
        raise EventLogError(f"{name} must not be negative")
    return number / 1000.0 if number > 1e11 else number


class _Tracker:
    """Per-type state between samples."""

    def __init__(self) -> None:
        self.true_since: float | None = None   # first true sample of the current true run
        self.false_since: float | None = None  # first false sample of the current false run
        self.last_true: float | None = None    # last true sample
        self.last_known: float | None = None   # last sample that was not None
        self.open_id: str | None = None        # id of the open episode, if any


class EventLog:
    def __init__(
        self,
        path: Path | None,
        clock: Callable[[], float] = time.time,
        rules: dict[str, EventRule] | None = None,
    ) -> None:
        self.path = path
        self.clock = clock
        self.rules = {**DEFAULT_RULES, **(rules or {})}
        self.unknown_close_s = 120.0
        self._lock = threading.Lock()
        self._trackers = {name: _Tracker() for name in EVENT_TYPES}
        self._events: list[dict[str, Any]] = []
        self._vision_state = "unknown"
        self._vision_state_at: float | None = None
        self._persisted_at = 0.0
        self._load()

    # ---------------------------------------------------------------- storage

    def _load(self) -> None:
        if self.path is None:
            return
        payload = read_json(self.path)
        events = [dict(e) for e in payload.get("events") or [] if isinstance(e, dict) and e.get("type") in EVENT_TYPES]
        changed = False
        for event in events:
            if event.get("end_at") is None:
                # The service stopped while this episode was open: end it where it was last seen.
                end = float(event.get("last_seen_at") or event.get("start_at") or 0.0)
                event["end_at"] = round(end, 3)
                event["duration_s"] = round(max(0.0, end - float(event.get("start_at") or end)), 1)
                event["ongoing"] = False
                event["interrupted"] = True
                changed = True
        self._events = events
        if changed:
            self._save(self.clock())

    def _save(self, now: float) -> None:
        cutoff = now - KEEP_S
        self._events = [e for e in self._events if e.get("end_at") is None or float(e["end_at"]) >= cutoff][-MAX_EVENTS:]
        self._persisted_at = now
        if self.path is not None:
            write_json(self.path, {"updated_at": now, "events": self._events})

    def _find(self, event_id: str | None) -> dict[str, Any] | None:
        if event_id is None:
            return None
        return next((e for e in reversed(self._events) if e.get("id") == event_id), None)

    # ---------------------------------------------------------------- observing

    def observe(
        self,
        active: dict[str, bool | None],
        now: float | None = None,
        vision_state: str | None = None,
        details: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Feed one sample per event type; returns the episodes opened or closed by it."""
        now = self.clock() if now is None else float(now)
        details = details or {}
        changes: list[dict[str, Any]] = []
        with self._lock:
            if vision_state is not None:
                self._vision_state = vision_state if vision_state in VISION_STATES else "unknown"
                self._vision_state_at = now
            dirty = False
            for name in EVENT_TYPES:
                change = self._step(name, active.get(name), now, details.get(name) or {})
                if change is not None:
                    changes.append(dict(change))
                    dirty = True
            if not dirty and any(t.open_id for t in self._trackers.values()) and now - self._persisted_at >= OPEN_PERSIST_S:
                dirty = True
            if dirty:
                self._save(now)
        return changes

    def _step(self, name: str, value: bool | None, now: float, detail: dict[str, Any]) -> dict[str, Any] | None:
        tracker = self._trackers[name]
        rule = self.rules[name]
        open_event = self._find(tracker.open_id)
        if value is None:
            if open_event is not None and tracker.last_known is not None and now - tracker.last_known >= self.unknown_close_s:
                return self._close(tracker, open_event, interrupted=True)
            return None
        tracker.last_known = now
        if value:
            tracker.false_since = None
            tracker.last_true = now
            if tracker.true_since is None:
                tracker.true_since = now
            if open_event is not None:
                open_event["last_seen_at"] = round(now, 3)
                self._update_peak(open_event, detail)
                return None
            if now - tracker.true_since < rule.open_s:
                return None
            return self._open(name, tracker, rule, detail)
        tracker.true_since = None
        if open_event is None:
            return None
        if tracker.false_since is None:
            tracker.false_since = now
        if now - tracker.false_since >= rule.close_s:
            return self._close(tracker, open_event)
        return None

    def _open(self, name: str, tracker: _Tracker, rule: EventRule, detail: dict[str, Any]) -> dict[str, Any]:
        start = float(tracker.true_since or tracker.last_true or self.clock())
        previous = next((e for e in reversed(self._events) if e.get("type") == name), None)
        if previous is not None and previous.get("end_at") is not None and start - float(previous["end_at"]) <= rule.merge_s:
            # Same episode resumed: reopen it instead of starting a new one.
            previous.update({"end_at": None, "duration_s": None, "ongoing": True, "last_seen_at": round(tracker.last_true or start, 3)})
            previous["merged"] = int(previous.get("merged") or 0) + 1
            previous.pop("interrupted", None)
            self._update_peak(previous, detail)
            tracker.open_id = previous["id"]
            return previous
        event = {
            "id": f"{name}-{int(start * 1000)}",
            "type": name,
            "source": EVENT_SOURCES[name],
            "start_at": round(start, 3),
            "end_at": None,
            "duration_s": None,
            "ongoing": True,
            "last_seen_at": round(tracker.last_true or start, 3),
        }
        self._update_peak(event, detail)
        self._events.append(event)
        tracker.open_id = event["id"]
        return event

    def _close(self, tracker: _Tracker, event: dict[str, Any], interrupted: bool = False) -> dict[str, Any]:
        end = float(tracker.last_true or event.get("last_seen_at") or event["start_at"])
        event["end_at"] = round(end, 3)
        event["duration_s"] = round(max(0.0, end - float(event["start_at"])), 1)
        event["ongoing"] = False
        event["last_seen_at"] = round(end, 3)
        if interrupted:
            event["interrupted"] = True
        tracker.open_id = None
        tracker.true_since = None
        tracker.false_since = None
        return event

    @staticmethod
    def _update_peak(event: dict[str, Any], detail: dict[str, Any]) -> None:
        score = detail.get("score")
        if isinstance(score, (int, float)):
            event["peak_score"] = round(max(float(score), float(event.get("peak_score") or 0.0)), 4)

    # ---------------------------------------------------------------- queries

    def list_events(
        self,
        since: Any = None,
        until: Any = None,
        event_type: Any = None,
        limit: Any = None,
    ) -> list[dict[str, Any]]:
        """Newest first. An ongoing episode counts as overlapping any range up to now."""
        since_s = parse_time(since, "since")
        until_s = parse_time(until, "until")
        if event_type not in (None, "") and event_type not in EVENT_TYPES:
            raise EventLogError(f"type must be one of {', '.join(EVENT_TYPES)}")
        if limit in (None, ""):
            count = DEFAULT_LIST
        else:
            try:
                count = int(limit)
            except (TypeError, ValueError):
                raise EventLogError("limit must be an integer") from None
            if count < 1:
                raise EventLogError("limit must be at least 1")
        count = min(count, MAX_LIST)
        now = self.clock()
        result = []
        with self._lock:
            for event in reversed(self._events):
                if event_type and event.get("type") != event_type:
                    continue
                start = float(event["start_at"])
                end = float(event["end_at"]) if event.get("end_at") is not None else now
                if since_s is not None and end < since_s:
                    continue
                if until_s is not None and start > until_s:
                    continue
                result.append(self._public(event, now))
                if len(result) >= count:
                    break
        return result

    def summary(self, window_s: Any = 7200.0, now: float | None = None) -> dict[str, Any]:
        """Per-type counts for the last window_s seconds plus the current camera state."""
        try:
            window = float(window_s)
        except (TypeError, ValueError):
            raise EventLogError("window_s must be a number of seconds") from None
        if not MIN_WINDOW_S <= window <= MAX_WINDOW_S:
            raise EventLogError(f"window_s must be between {int(MIN_WINDOW_S)} and {int(MAX_WINDOW_S)}")
        now = self.clock() if now is None else float(now)
        since = now - window
        with self._lock:
            types: dict[str, Any] = {}
            active = []
            for name in EVENT_TYPES:
                hits = []
                for event in self._events:
                    if event.get("type") != name:
                        continue
                    end = float(event["end_at"]) if event.get("end_at") is not None else now
                    if end >= since and float(event["start_at"]) <= now:
                        hits.append((event, end))
                total = sum(max(0.0, min(end, now) - max(float(e["start_at"]), since)) for e, end in hits)
                latest = max(hits, key=lambda item: float(item[0]["start_at"]))[0] if hits else None
                types[name] = {
                    "count": len(hits),
                    "total_s": round(total, 1),
                    "latest": self._public(latest, now) if latest else None,
                }
                if latest is not None and latest.get("end_at") is None:
                    active.append(name)
            fresh = self._vision_state_at is not None and now - self._vision_state_at <= VISION_STATE_MAX_AGE_S
            return {
                "now": round(now, 3),
                "window_s": window,
                "since": round(since, 3),
                "current": {
                    "vision_state": self._vision_state if fresh else "unknown",
                    "vision_state_at": self._vision_state_at,
                    "active": active,
                },
                "types": types,
            }

    @staticmethod
    def _public(event: dict[str, Any], now: float) -> dict[str, Any]:
        item = dict(event)
        if item.get("end_at") is None:
            item["duration_s"] = round(max(0.0, now - float(item["start_at"])), 1)
            item["ongoing"] = True
        return item
