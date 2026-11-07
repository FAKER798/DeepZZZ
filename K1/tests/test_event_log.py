from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.event_log import EventLog, EventLogError, classify_posture

FACE = "face_cover_rollover"


def kp(points: dict[int, tuple[float, float]], conf: float = 0.9) -> list[list[float]]:
    """17 COCO keypoints; only the given indexes are visible."""
    out = [[0.0, 0.0, 0.0] for _ in range(17)]
    for index, (x, y) in points.items():
        out[index] = [x, y, conf]
    return out


# Top-down view, head at the top: shoulders 40 px apart, hips 50 px below.
BACK = kp({0: (100, 40), 1: (95, 35), 2: (105, 35), 5: (80, 60), 6: (120, 60), 11: (88, 110), 12: (112, 110)})
SIDE = kp({0: (100, 40), 1: (96, 35), 5: (99, 58), 6: (103, 64), 11: (98, 110), 12: (102, 112)})
FACE_DOWN = kp({3: (95, 40), 5: (80, 60), 6: (120, 60), 11: (88, 110), 12: (112, 110)})


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class EventLogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.json"
        self.clock = Clock()
        self.log = EventLog(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def feed(self, seconds: int, face: bool | None = False, cry: bool | None = False, **details) -> None:
        """One sample per second, like the main service."""
        for _ in range(seconds):
            self.log.observe({FACE: face, "cry": cry, "boundary": False, "no_person": False},
                             now=self.clock.now, vision_state="covered" if face else "visible", details=details)
            self.clock.now += 1

    def stored(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["events"]


class DebounceTests(EventLogCase):
    def test_single_frame_flicker_is_not_logged(self) -> None:
        self.feed(5)
        self.feed(1, face=True)
        self.feed(10)
        self.assertEqual(self.log.list_events(), [])

    def test_starts_after_2s_at_first_true_and_ends_after_5s_at_last_true(self) -> None:
        self.feed(3)
        first_true = self.clock.now
        self.feed(2, face=True)
        self.assertEqual(self.log.list_events(), [])  # 1 s of True so far, not 2
        self.feed(1, face=True)
        events = self.log.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_at"], first_true)
        self.assertTrue(events[0]["ongoing"])
        self.assertEqual(events[0]["id"], f"{FACE}-{int(first_true * 1000)}")
        last_true = self.clock.now
        self.feed(1, face=True)
        self.feed(5)  # 4 s of False after the last True: still ongoing
        self.assertTrue(self.log.list_events()[0]["ongoing"])
        self.feed(1)
        event = self.log.list_events()[0]
        self.assertFalse(event["ongoing"])
        self.assertEqual(event["end_at"], last_true)
        self.assertEqual(event["duration_s"], last_true - first_true)
        self.assertEqual(self.stored()[0]["end_at"], last_true)

    def test_cry_starts_at_once_and_keeps_peak_score(self) -> None:
        self.feed(1, cry=True, cry_score=0.5)
        self.feed(1, cry=True, cry_score=0.82)
        self.feed(1, cry=True, cry_score=0.6)
        self.feed(4)
        event = self.log.list_events(event_type="cry")[0]
        self.assertEqual(event["peak_score"], 0.82)
        self.assertFalse(event["ongoing"])

    def test_rolling_back_and_forth_within_30s_is_one_event(self) -> None:
        self.feed(3, face=True)
        self.feed(10)  # ended
        self.assertFalse(self.log.list_events()[0]["ongoing"])
        self.feed(3, face=True)  # back within 30 s of the end
        events = self.log.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["merged"], 1)
        self.assertTrue(events[0]["ongoing"])
        self.feed(40)
        self.feed(3, face=True)  # more than 30 s later: a new event
        self.assertEqual(len(self.log.list_events()), 2)


class UnknownAndRestartTests(EventLogCase):
    def test_unknown_input_keeps_event_then_interrupts_after_120s(self) -> None:
        self.feed(3, face=True)
        last_true = self.clock.now - 1
        self.feed(60, face=None)
        self.assertTrue(self.log.list_events()[0]["ongoing"])  # unknown never ends an event
        self.feed(61, face=None)
        event = self.log.list_events()[0]
        self.assertFalse(event["ongoing"])
        self.assertTrue(event["interrupted"])
        self.assertEqual(event["end_at"], last_true)

    def test_unknown_input_never_starts_an_event(self) -> None:
        self.feed(1, face=True)
        self.feed(5, face=None)
        self.feed(1, face=True)
        self.assertEqual(self.log.list_events(), [])

    def test_restart_closes_ongoing_events_at_last_seen(self) -> None:
        self.feed(5, face=True)
        last_seen = self.stored()[0]["last_seen_at"]  # flushed on start, then every 30 s
        reopened = EventLog(self.path, clock=self.clock)
        event = reopened.list_events()[0]
        self.assertFalse(event["ongoing"])
        self.assertTrue(event["interrupted"])
        self.assertEqual(event["end_at"], last_seen)

    def test_events_older_than_7_days_are_dropped(self) -> None:
        self.feed(3, face=True)
        self.feed(10)
        self.clock.now += 8 * 86400
        self.feed(1, cry=True)  # any write prunes
        self.assertEqual([e["type"] for e in self.stored()], ["cry"])


class PostureTests(EventLogCase):
    def test_classify_posture(self) -> None:
        self.assertEqual(classify_posture(BACK)["posture"], "back")
        self.assertEqual(classify_posture(SIDE)["posture"], "side")
        self.assertEqual(classify_posture(FACE_DOWN)["posture"], "face_down")
        self.assertEqual(classify_posture(SIDE, side_ratio=0)["posture"], "back")  # side detection off
        hidden_body = kp({0: (100, 40), 1: (96, 35)})
        self.assertEqual(classify_posture(hidden_body), {"posture": "back", "shoulder_ratio": None})

    def test_roll_from_back_to_side_is_one_event_with_posture(self) -> None:
        self.feed(10, posture="back")
        self.feed(4, face=True, posture="side")
        event = self.log.list_events()[0]
        self.assertEqual((event["type"], event["posture"], event["from_posture"]), (FACE, "side", "back"))
        summary = self.log.summary(600)
        self.assertEqual(summary["types"][FACE]["side_count"], 1)
        self.assertEqual(summary["current"]["posture"], "side")

    def test_face_already_hidden_at_start_has_no_from_posture(self) -> None:
        self.feed(4, face=True, posture="face_down")
        self.assertNotIn("from_posture", self.log.list_events()[0])


class QueryTests(EventLogCase):
    def test_summary_counts_and_current_state(self) -> None:
        self.feed(5, cry=True, cry_score=0.7)
        self.feed(10)
        self.feed(4, face=True)
        summary = self.log.summary(window_s=600)
        self.assertEqual(summary["types"]["cry"]["count"], 1)
        self.assertEqual(summary["types"]["cry"]["total_s"], 4.0)
        self.assertEqual(summary["types"][FACE]["latest"]["ongoing"], True)
        self.assertEqual(summary["current"]["vision_state"], "covered")
        self.assertEqual(summary["current"]["active"], [FACE])
        self.assertEqual(summary["types"]["boundary"], {"count": 0, "total_s": 0, "latest": None})
        self.clock.now += 11  # no sample for >10 s
        self.assertEqual(self.log.summary()["current"]["vision_state"], "unknown")
        with self.assertRaises(EventLogError) as caught:
            self.log.summary(window_s=5)
        self.assertEqual(str(caught.exception), "window_s must be between 60 and 604800")

    def test_list_filters_newest_first_and_accepts_ms(self) -> None:
        self.feed(5, cry=True)
        self.feed(10)
        middle = self.clock.now
        self.feed(4, face=True)
        events = self.log.list_events()
        self.assertEqual([e["type"] for e in events], [FACE, "cry"])
        self.assertEqual([e["type"] for e in self.log.list_events(since=int(middle * 1000))], [FACE])
        self.assertEqual([e["type"] for e in self.log.list_events(until=middle - 1)], ["cry"])
        self.assertEqual(len(self.log.list_events(limit="1")), 1)

    def test_bad_parameters(self) -> None:
        cases = [
            ({"event_type": "sneeze"}, "type must be one of cry, face_cover_rollover, boundary, no_person"),
            ({"limit": "x"}, "limit must be an integer"),
            ({"limit": "0"}, "limit must be at least 1"),
            ({"since": "abc"}, "since must be a number"),
            ({"until": "-5"}, "until must be a non-negative number"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(EventLogError) as caught:
                self.log.list_events(**kwargs)
            self.assertEqual(str(caught.exception), message)


if __name__ == "__main__":
    unittest.main()
