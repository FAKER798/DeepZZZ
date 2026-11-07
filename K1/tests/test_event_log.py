from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.event_log import EventLog, EventLogError

T0 = 1_790_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def feed(log: EventLog, clock: Clock, seconds: int, **active) -> list[dict]:
    """One sample per second for `seconds` seconds; returns all changes."""
    changes = []
    for _ in range(seconds):
        changes += log.observe({name: active.get(name) for name in ("cry", "face_cover_rollover", "boundary", "no_person")},
                               now=clock.now, vision_state=active.get("vision_state", "visible"))
        clock.now += 1.0
    return changes


def make_log(tmp: str) -> tuple[EventLog, Clock, Path]:
    clock = Clock()
    path = Path(tmp) / "events.json"
    return EventLog(path, clock=clock), clock, path


class EpisodeTests(unittest.TestCase):
    def test_face_cover_needs_two_seconds_and_closes_after_five(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            feed(log, clock, 3, face_cover_rollover=False)
            # a single-frame blip is not an episode
            self.assertEqual(feed(log, clock, 1, face_cover_rollover=True), [])
            feed(log, clock, 1, face_cover_rollover=False)
            self.assertEqual(log.list_events(), [])
            start = clock.now
            opened = feed(log, clock, 10, face_cover_rollover=True)
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0]["start_at"], start)  # starts at the first true sample, not after the 2 s wait
            self.assertTrue(opened[0]["ongoing"])
            last_true = clock.now - 1
            closed = feed(log, clock, 6, face_cover_rollover=False)
            self.assertEqual(len(closed), 1)
            event = log.list_events()[0]
            self.assertFalse(event["ongoing"])
            self.assertEqual(event["end_at"], last_true)
            self.assertEqual(event["duration_s"], 9.0)
            self.assertEqual(event["source"], "vision")

    def test_cry_opens_at_once_and_keeps_peak_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            for score in (0.5, 0.9, 0.7):
                log.observe({"cry": True}, now=clock.now, details={"cry": {"score": score}})
                clock.now += 1
            feed(log, clock, 4, cry=False)
            event = log.list_events(event_type="cry")[0]
            self.assertEqual(event["source"], "yamnet")
            self.assertEqual(event["peak_score"], 0.9)
            self.assertEqual(event["duration_s"], 2.0)

    def test_episode_within_30s_is_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            feed(log, clock, 5, cry=True)
            feed(log, clock, 20, cry=False)  # closed after 3 s, then 17 s quiet
            feed(log, clock, 5, cry=True)
            events = log.list_events(event_type="cry")
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0]["ongoing"])
            self.assertEqual(events[0]["merged"], 1)
            feed(log, clock, 60, cry=False)
            feed(log, clock, 3, cry=True)
            self.assertEqual(len(log.list_events(event_type="cry")), 2)

    def test_unknown_keeps_episode_then_closes_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            feed(log, clock, 5, face_cover_rollover=True)
            last_true = clock.now - 1
            # LLM answering for 60 s: YOLO paused, inputs unknown -> episode stays open
            self.assertEqual(feed(log, clock, 60, vision_state="paused"), [])
            self.assertTrue(log.list_events()[0]["ongoing"])
            # camera gone for good -> closed at the last true sample
            closed = feed(log, clock, 70, vision_state="unknown")
            self.assertEqual(len(closed), 1)
            event = log.list_events()[0]
            self.assertTrue(event["interrupted"])
            self.assertEqual(event["end_at"], last_true)

    def test_restart_closes_open_episode_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, path = make_log(tmp)
            feed(log, clock, 40, no_person=True)  # opens after 5 s, rewritten every 30 s while open
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(saved["events"][0]["end_at"])
            again = EventLog(path, clock=clock)
            event = again.list_events()[0]
            self.assertFalse(event["ongoing"])
            self.assertTrue(event["interrupted"])
            self.assertGreaterEqual(event["end_at"], event["start_at"])

    def test_old_events_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, path = make_log(tmp)
            feed(log, clock, 5, cry=True)
            feed(log, clock, 5, cry=False)
            clock.now += 8 * 86400
            feed(log, clock, 5, cry=True)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["events"]), 1)


class QueryTests(unittest.TestCase):
    def test_summary_counts_window_and_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            feed(log, clock, 10, cry=True)
            feed(log, clock, 100, cry=False)
            feed(log, clock, 10, cry=True)
            feed(log, clock, 100, cry=False)
            feed(log, clock, 6, face_cover_rollover=True, vision_state="covered")
            summary = log.summary(window_s=3600)
            self.assertEqual(summary["types"]["cry"]["count"], 2)
            self.assertEqual(summary["types"]["cry"]["total_s"], 18.0)
            self.assertEqual(summary["types"]["face_cover_rollover"]["count"], 1)
            self.assertTrue(summary["types"]["face_cover_rollover"]["latest"]["ongoing"])
            self.assertEqual(summary["types"]["boundary"]["count"], 0)
            self.assertEqual(summary["current"]["vision_state"], "covered")
            self.assertEqual(summary["current"]["active"], ["face_cover_rollover"])
            # a 60 s window only sees the ongoing episode
            self.assertEqual(log.summary(window_s=60)["types"]["cry"]["count"], 0)
            # nothing observed for a while -> camera state unknown
            clock.now += 30
            self.assertEqual(log.summary()["current"]["vision_state"], "unknown")

    def test_list_filters_and_millisecond_times(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, clock, _ = make_log(tmp)
            feed(log, clock, 5, cry=True)
            feed(log, clock, 60, cry=False)
            middle = clock.now
            feed(log, clock, 5, boundary=True)
            feed(log, clock, 10, boundary=False)
            self.assertEqual([e["type"] for e in log.list_events()], ["boundary", "cry"])
            self.assertEqual([e["type"] for e in log.list_events(since=int(middle * 1000))], ["boundary"])
            self.assertEqual([e["type"] for e in log.list_events(until=middle - 1)], ["cry"])
            self.assertEqual(len(log.list_events(limit="1")), 1)

    def test_bad_parameters(self) -> None:
        log = EventLog(None, clock=Clock())
        for kwargs in ({"event_type": "sneeze"}, {"limit": "0"}, {"limit": "x"}, {"since": "yesterday"}):
            with self.assertRaises(EventLogError):
                log.list_events(**kwargs)
        for window in ("abc", 10, 30 * 86400):
            with self.assertRaises(EventLogError):
                log.summary(window_s=window)


if __name__ == "__main__":
    unittest.main()
