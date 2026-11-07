from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from k1edge.runtime_store import age_seconds, is_stale, read_json, write_json


class RuntimeStoreTests(unittest.TestCase):
    def test_read_json_handles_missing_and_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(read_json(path, {"ok": False}), {"ok": False})

            path.write_text("{bad", encoding="utf-8")
            self.assertEqual(read_json(path), {})

    def test_write_json_adds_schema_and_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_json(path, {"running": True})

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIs(payload["running"], True)
            self.assertIs(read_json(path)["running"], True)

    def test_age_and_stale_helpers(self) -> None:
        payload = {"updated_at": 100.0}

        self.assertEqual(age_seconds(payload, now=103.25), 3.2)
        self.assertIs(is_stale(payload, max_age_s=2, now=103.25), True)
        self.assertIs(is_stale(payload, max_age_s=5, now=103.25), False)


if __name__ == "__main__":
    unittest.main()

