from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.care_log import CareError, CareNotFound, CareStore, age_parts, wake_window_for_months


class Clock:
    def __init__(self) -> None:
        # 2026-11-05 15:00 local time
        self.now = dt.datetime(2026, 11, 5, 15, 0, 0).timestamp()

    def __call__(self) -> float:
        return self.now


def make_store(clock: Clock | None = None) -> CareStore:
    tmp = Path(tempfile.mkdtemp())
    return CareStore(tmp / "records.json", tmp / "profile.json", clock=clock or Clock())


class RecordTests(unittest.TestCase):
    def test_create_list_update_delete_roundtrip(self) -> None:
        clock = Clock()
        store = make_store(clock)
        created = store.create_record({"type": "feeding", "kind": "bottle", "amount_ml": "120", "milk": "formula", "start_at": clock.now * 1000})
        self.assertEqual(created["start_at"], clock.now)  # milliseconds accepted
        self.assertEqual(created["amount_ml"], 120)
        self.assertEqual(created["source"], "app")

        updated = store.update_record(created["id"], {**created, "amount_ml": 150, "note": "吃得很好"})
        self.assertEqual((updated["amount_ml"], updated["note"]), (150, "吃得很好"))
        self.assertEqual(updated["created_at"], created["created_at"])

        # persisted across a fresh store instance
        again = CareStore(store.records_path, store.profile_path, clock=clock)
        self.assertEqual(again.get_record(created["id"])["amount_ml"], 150)

        self.assertEqual(store.delete_record(created["id"]), {"id": created["id"], "deleted": True})
        with self.assertRaises(CareNotFound) as ctx:
            store.get_record(created["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_list_filters(self) -> None:
        clock = Clock()
        store = make_store(clock)
        store.create_record({"type": "diaper", "kind": "wet", "start_at": clock.now - 5 * 86400})
        store.create_record({"type": "diaper", "kind": "dirty", "start_at": clock.now - 3600})
        store.create_record({"type": "feeding", "start_at": clock.now - 600})
        recent = store.list_records(since=clock.now - 3 * 86400)
        self.assertEqual([r["type"] for r in recent], ["feeding", "diaper"])  # newest first
        self.assertEqual(len(store.list_records(record_type="diaper")), 2)
        self.assertEqual(len(store.list_records(limit=1)), 1)

    def test_validation_messages_match_app_expectations(self) -> None:
        clock = Clock()
        store = make_store(clock)
        cases = [
            ({"type": "bath"}, "type must be one of"),
            ({"type": "feeding", "start_at": clock.now + 3600}, "in the future"),
            ({"type": "feeding", "amount_ml": 900}, "must be between"),
            ({"type": "diaper", "kind": "blue"}, "kind must be one of"),
            ({"type": "sleep", "start_at": clock.now - 600, "end_at": clock.now - 900}, "end_at must be after"),
            ({"type": "sleep", "start_at": clock.now - 30 * 3600, "end_at": clock.now - 1}, "longer than"),
            ({"type": "growth"}, "at least one"),
            ({"type": "growth", "weight_kg": 99}, "must be between"),
        ]
        for payload, fragment in cases:
            with self.assertRaises(CareError, msg=payload) as ctx:
                store.create_record(payload)
            self.assertIn(fragment, str(ctx.exception), payload)

    def test_only_one_sleep_in_progress(self) -> None:
        clock = Clock()
        store = make_store(clock)
        sleep = store.create_record({"type": "sleep", "start_at": clock.now - 1800})
        self.assertIsNone(sleep["end_at"])
        with self.assertRaises(CareError) as ctx:
            store.create_record({"type": "sleep", "start_at": clock.now})
        self.assertIn("already in progress", str(ctx.exception))
        # back-filling a finished sleep is fine
        store.create_record({"type": "sleep", "start_at": clock.now - 6 * 3600, "end_at": clock.now - 5 * 3600})
        ended = store.end_sleep()
        self.assertEqual(ended["end_at"], clock.now)
        with self.assertRaises(CareError) as ctx:
            store.end_sleep()
        self.assertIn("no sleep in progress", str(ctx.exception))
        # updating a finished sleep without end_at keeps it finished
        kept = store.update_record(sleep["id"], {"note": "午睡"})
        self.assertEqual(kept["end_at"], clock.now)


class ProfileAndOverviewTests(unittest.TestCase):
    def test_profile_validation(self) -> None:
        store = make_store()
        self.assertEqual(store.profile(), {"name": "", "birth_date": None, "sex": None})
        saved = store.save_profile({"name": "小宝", "birth_date": "2026-07-01", "sex": "female"})
        self.assertEqual(saved, {"name": "小宝", "birth_date": "2026-07-01", "sex": "female"})
        for bad in ({"birth_date": "2030-01-01"}, {"birth_date": "07/01/2026"}):
            with self.assertRaises(CareError) as ctx:
                store.save_profile(bad)
            self.assertIn("birth_date", str(ctx.exception))

    def test_age_parts(self) -> None:
        self.assertEqual(age_parts(dt.date(2026, 7, 1), dt.date(2026, 11, 5)), {"days": 127, "months": 4, "month_days": 4})
        self.assertEqual(age_parts(dt.date(2026, 1, 31), dt.date(2026, 3, 1)), {"days": 29, "months": 1, "month_days": 1})
        self.assertEqual(wake_window_for_months(4), (90, 135))

    def test_overview_today_and_nap(self) -> None:
        clock = Clock()
        store = make_store(clock)
        overview = store.overview()
        self.assertEqual(overview["nap"]["state"], "need_profile")
        self.assertIsNone(overview["profile"])

        store.save_profile({"birth_date": "2026-07-01"})
        self.assertEqual(store.overview()["nap"]["state"], "need_sleep")

        store.create_record({"type": "feeding", "kind": "bottle", "amount_ml": 120, "start_at": clock.now - 3600})
        store.create_record({"type": "feeding", "kind": "breast", "duration_min": 15, "start_at": clock.now - 7200})
        store.create_record({"type": "feeding", "kind": "solid", "food": "米粉", "start_at": clock.now - 8000})
        store.create_record({"type": "feeding", "kind": "bottle", "amount_ml": 90, "start_at": clock.now - 86400})  # yesterday
        store.create_record({"type": "diaper", "kind": "mixed", "start_at": clock.now - 1000})
        store.create_record({"type": "diaper", "kind": "wet", "start_at": clock.now - 2000})
        store.create_record({"type": "sleep", "start_at": clock.now - 3 * 3600, "end_at": clock.now - 2 * 3600})
        overview = store.overview()
        today = overview["today"]
        self.assertEqual(today["feeding"], {"count": 3, "bottle_ml": 120, "breast_min": 15, "solid_count": 1})
        self.assertEqual(today["diaper"], {"count": 2, "wet": 2, "dirty": 1})
        self.assertEqual((today["sleep"]["count"], today["sleep"]["total_min"]), (1, 60.0))
        self.assertEqual(overview["last_diaper"]["kind"], "mixed")
        self.assertEqual(overview["age"]["months"], 4)
        nap = overview["nap"]
        self.assertEqual((nap["state"], nap["basis"], nap["samples"]), ("awake", "age", 0))
        self.assertEqual(nap["age_range_min"], [90, 135])
        self.assertEqual(nap["last_wake_at"], clock.now - 2 * 3600)
        self.assertLess(nap["earliest_at"], nap["ideal_at"])
        self.assertLess(nap["ideal_at"], nap["latest_at"])
        self.assertIn(nap["timing"], {"early", "due", "overdue"})

        store.create_record({"type": "sleep", "start_at": clock.now - 60})
        nap = store.overview()["nap"]
        self.assertEqual(nap["state"], "sleeping")
        self.assertEqual(store.overview()["today"]["sleep"]["count"], 2)

    def test_nap_uses_history(self) -> None:
        clock = Clock()
        store = make_store(clock)
        store.save_profile({"birth_date": "2026-07-01"})
        day = clock.now - 86400
        # Yesterday: naps with 100 min awake windows between them (07:00 .. 18:00 local)
        start = dt.datetime.fromtimestamp(day).replace(hour=7, minute=0, second=0).timestamp()
        for _ in range(6):
            store.create_record({"type": "sleep", "start_at": start, "end_at": start + 3600})
            start += 3600 + 100 * 60
        nap = store.overview()["nap"]
        self.assertEqual((nap["basis"], nap["samples"]), ("history", 5))
        self.assertAlmostEqual(nap["ideal_at"] - nap["last_wake_at"], 100 * 60, delta=1)


class CareRouteTests(unittest.TestCase):
    def test_routes_follow_app_contract(self) -> None:
        try:
            from k1edge.app import create_main_app
            from k1edge.config import parse_config
            from k1edge.runtime import AppRuntime
        except ModuleNotFoundError as exc:
            self.skipTest(f"main app dependencies unavailable: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("k1edge.runtime.CARE_RECORDS_FILE", Path(tmp) / "records.json"), patch(
                "k1edge.runtime.CARE_PROFILE_FILE", Path(tmp) / "profile.json"
            ):
                runtime = AppRuntime(parse_config(["--no-camera", "--disable-yamnet"]))
            client = create_main_app(runtime).test_client()

            created = client.post("/api/care/records", json={"type": "diaper", "kind": "wet"}).get_json()
            self.assertTrue(created["ok"])
            record_id = created["result"]["id"]

            listed = client.get("/api/care/records?since=1&type=diaper&limit=10").get_json()
            self.assertEqual([r["id"] for r in listed["result"]], [record_id])

            updated = client.post(f"/api/care/records/{record_id}", json={"type": "diaper", "kind": "dirty"}).get_json()
            self.assertEqual(updated["result"]["kind"], "dirty")

            bad = client.post("/api/care/records", json={"type": "feeding", "amount_ml": 9999})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("must be between", bad.get_json()["error"])

            missing = client.delete("/api/care/records/nope")
            self.assertEqual(missing.status_code, 404)
            self.assertFalse(missing.get_json()["ok"])

            profile = client.post("/api/care/profile", json={"name": "小宝", "birth_date": "2026-07-01", "sex": "male"}).get_json()
            self.assertEqual(profile["result"]["sex"], "male")
            self.assertEqual(client.get("/api/care/profile").get_json()["result"]["name"], "小宝")

            overview = client.get("/api/care/overview").get_json()["result"]
            for key in ("now", "day", "profile", "age", "today", "last_feeding", "last_diaper", "latest_growth", "nap"):
                self.assertIn(key, overview)
            self.assertEqual(overview["today"]["diaper"]["dirty"], 1)

            self.assertTrue(client.delete(f"/api/care/records/{record_id}").get_json()["result"]["deleted"])
            json.dumps(overview)  # serializable


if __name__ == "__main__":
    unittest.main()
