from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

import numpy as np

from k1edge.app import create_dataset_app, create_main_app
from k1edge.config import parse_config
from k1edge.runtime import AppRuntime


def make_runtime() -> AppRuntime:
    return AppRuntime(parse_config(["--no-camera", "--disable-yamnet", "--enable-dataset-capture"]))


class AppRouteTests(unittest.TestCase):
    def test_healthz_and_status_shapes(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        health = client.get("/api/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertIs(health.get_json()["ok"], True)
        self.assertIn("runtime", health.get_json())

        status = client.get("/api/status")
        payload = status.get_json()
        self.assertEqual(status.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertIn("camera", payload)
        self.assertIn("light", payload)
        self.assertIn("temperature_humidity", payload)

    def test_snapshot_returns_compatible_error_shape_when_empty(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        response = client.get("/api/snapshot")

        self.assertEqual(response.status_code, 404)
        self.assertIs(response.get_json()["ok"], False)

    def test_light_post_writes_dual_pwm_command(self) -> None:
        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.hardware.light.command_path = Path(tmp) / "pwm_dual_command.json"
            runtime.hardware.light.status_path = Path(tmp) / "pwm_dual_status.json"
            runtime.hardware.light.ack_timeout_s = 0.05
            client = create_main_app(runtime).test_client()

            response = client.post("/api/light", json={"duty": 42, "frequency_hz": 20000})
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertIs(payload["ok"], True)
            self.assertEqual(payload["light"]["target_duty"], 42.0)
            command = json.loads(runtime.hardware.light.command_path.read_text(encoding="utf-8"))
            self.assertEqual((command["duty"], command["enabled"], command["frequency_hz"]), (42.0, True, 20000))

            bad = client.post("/api/light", json={"duty": 10, "frequency_hz": 5})
            self.assertEqual(bad.status_code, 400)

    def test_dataset_stats_route(self) -> None:
        runtime = make_runtime()
        client = create_dataset_app(runtime).test_client()

        response = client.get("/api/dataset/stats")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertIs(payload["result"]["enabled"], True)

    def test_audio_playback_route_records_when_no_buffer(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        with tempfile.TemporaryDirectory() as tmp:
            runtime.audio.pipeline.args.audio_device = "input"
            with patch("pipelines.audio_pipeline.AUDIO_DIR", Path(tmp)):
                with patch("pipelines.audio_pipeline.record_waveform") as record_waveform:
                    with patch("pipelines.audio_pipeline.subprocess.run") as run:
                        record_waveform.return_value = (np.zeros(16000, dtype=np.float32), 16000)
                        run.return_value.returncode = 0
                        run.return_value.stdout = ""
                        run.return_value.stderr = ""

                        response = client.post(
                            "/api/audio/playback",
                            json={"seconds": 1, "volume": 1.2, "output_device": "default"},
                        )
                        thread = runtime.audio.pipeline._playback_thread
                        if thread is not None:
                            thread.join(timeout=2)
                        payload = response.get_json()
                        with wave.open(payload["result"]["file"], "rb") as handle:
                            self.assertEqual(handle.getframerate(), 48000)
                            self.assertEqual(handle.getnchannels(), 2)

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["result"]["output_device"], "plughw:CARD=UACDemoV10,DEV=0")
        self.assertEqual(payload["result"]["volume"], 1.2)
        record_waveform.assert_called_once()

    def test_audio_playback_capture_start_stop_and_play(self) -> None:
        runtime = make_runtime()
        client = create_main_app(runtime).test_client()

        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipelines.audio_pipeline.AUDIO_DIR", Path(tmp)):
                with patch("pipelines.audio_pipeline.time.time") as now:
                    with patch("pipelines.audio_pipeline.record_waveform") as record_waveform:
                        with patch("pipelines.audio_pipeline.subprocess.run") as run:
                            ticks = iter([100.0, 100.0, 102.0, 102.0, 102.0, 102.0, 102.0, 102.0])
                            now.side_effect = lambda: next(ticks, 102.0)
                            record_waveform.return_value = (np.zeros(32000, dtype=np.float32), 16000)
                            run.return_value.returncode = 0
                            run.return_value.stdout = ""
                            run.return_value.stderr = ""

                            start = client.post("/api/audio/playback/capture/start")
                            stop = client.post("/api/audio/playback/capture/stop", json={"volume": 1.0})
                            play = client.post(
                                "/api/audio/playback/play",
                                json={"output_device": "default", "volume": 1.0},
                            )
                            thread = runtime.audio.pipeline._playback_thread
                            if thread is not None:
                                thread.join(timeout=2)

        self.assertEqual(start.status_code, 200)
        self.assertEqual(stop.status_code, 200)
        self.assertEqual(play.status_code, 200)
        self.assertIs(play.get_json()["ok"], True)
        self.assertEqual(play.get_json()["result"]["output_device"], "plughw:CARD=UACDemoV10,DEV=0")
        record_waveform.assert_called_once()


    def test_assistant_routes(self) -> None:
        from tests.test_chat_assistant import FakeOllama

        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.assistant.client = FakeOllama()
            runtime.assistant.pauser.voice_pause_path = Path(tmp) / "voice_pause.json"
            client = create_main_app(runtime).test_client()

            status = client.get("/api/assistant/status").get_json()
            self.assertIs(status["ok"], True)
            self.assertIs(status["result"]["available"], True)
            self.assertIs(status["result"]["llm"]["ok"], True)
            self.assertIs(status["result"]["busy"], False)

            response = client.post("/api/assistant/chat", json={"stream": True, "messages": [{"role": "user", "content": "宝宝吐奶怎么办？"}]})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.mimetype.endswith("ndjson"))
            lines = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]
            self.assertEqual(lines[0]["type"], "start")
            self.assertEqual(lines[-1]["type"], "done")
            self.assertFalse(runtime.vision.paused)

            bad = client.post("/api/assistant/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
            self.assertEqual(bad.status_code, 400)
            self.assertIs(bad.get_json()["ok"], False)
            long = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "问" * 501}]})
            self.assertIn("longer than", long.get_json()["error"])

            plain = client.post("/api/assistant/chat", json={"stream": False, "messages": [{"role": "user", "content": "q"}]})
            self.assertEqual(plain.get_json()["result"]["reply"], "吐奶很常见，竖抱拍嗝即可。")

            runtime.assistant.settings = type(runtime.assistant.settings)(enabled=False)
            off = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "q"}]})
            self.assertEqual(off.status_code, 503)

    def test_event_inputs(self) -> None:
        from k1edge.runtime import event_inputs
        from tests.test_event_log import BACK, FACE_DOWN, SIDE

        now = 1_800_000_000.0
        yamnet = {"status": "ok", "crying": True, "cry_score": 0.7, "updated_at": now - 1}

        def vision(persons=None, age=0.5, paused=False, model_active=True):
            latest = {"updated_at": now - age, "frame_size": [320, 180], "model_active": model_active,
                      "persons": [{"keypoints": k} for k in (persons or [])]}
            return {"paused": paused, "latest": latest}

        observed, state, details = event_inputs(vision([BACK]), True, yamnet, {"zone": None}, now)
        self.assertEqual(state, "visible")
        self.assertEqual(observed, {"cry": True, "face_cover_rollover": False, "boundary": False, "no_person": False})
        self.assertEqual((details["posture"], details["cry_score"]), ("back", 0.7))

        observed, state, details = event_inputs(vision([FACE_DOWN]), True, None, {"zone": None}, now)
        self.assertEqual((state, observed["face_cover_rollover"], observed["cry"]), ("covered", True, None))
        self.assertEqual(event_inputs(vision([SIDE]), True, None, {}, now)[2]["posture"], "side")
        self.assertEqual(event_inputs(vision([SIDE]), True, None, {}, now, side_ratio=0)[1], "visible")

        observed, state, _ = event_inputs(vision([]), True, None, {}, now)
        self.assertEqual((state, observed["no_person"], observed["face_cover_rollover"]), ("no_person", True, False))

        # can't tell -> None for every vision type
        for args, expected in (
            ((vision([FACE_DOWN], age=6), True), "stale"),
            ((vision([FACE_DOWN], paused=True), True), "paused"),
            ((vision([FACE_DOWN]), False), "unknown"),
            ((vision([FACE_DOWN], model_active=False), True), "unknown"),
        ):
            observed, state, _ = event_inputs(*args, yamnet, {}, now)
            self.assertEqual(state, expected)
            self.assertEqual([observed[t] for t in ("face_cover_rollover", "boundary", "no_person")], [None, None, None])
            self.assertIs(observed["cry"], True)  # the microphone is unaffected

        stale_yamnet = {**yamnet, "updated_at": now - 30}
        self.assertIsNone(event_inputs(vision([BACK]), True, stale_yamnet, {}, now)[0]["cry"])
        self.assertIsNone(event_inputs(vision([BACK]), True, {"status": "warming"}, {}, now)[0]["cry"])

    def test_event_routes(self) -> None:
        from services.event_log import EventLog

        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            runtime.events = EventLog(Path(tmp) / "events.json")
            start = runtime.events.clock() - 20
            for second in range(5):
                runtime.events.observe({"face_cover_rollover": True}, now=start + second, vision_state="covered",
                                       details={"posture": "side"})
            client = create_main_app(runtime).test_client()

            listed = client.get(f"/api/events?type=face_cover_rollover&since={int(start * 1000)}").get_json()
            self.assertIs(listed["ok"], True)
            self.assertEqual(len(listed["result"]), 1)
            self.assertEqual(listed["result"][0]["posture"], "side")
            summary = client.get("/api/events/summary?window_s=600")
            self.assertEqual(summary.headers["Cache-Control"], "no-store, no-cache, must-revalidate")
            self.assertEqual(summary.get_json()["result"]["types"]["face_cover_rollover"]["count"], 1)

            bad = client.get("/api/events?type=sneeze")
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(bad.get_json(), {"ok": False, "error": "type must be one of cry, face_cover_rollover, boundary, no_person"})
            self.assertEqual(client.get("/api/events?limit=0").status_code, 400)
            self.assertEqual(client.get("/api/events/summary?window_s=5").get_json()["error"], "window_s must be between 60 and 604800")

            runtime.sample_events()  # no camera in tests: vision inputs are unknown, nothing breaks
            self.assertEqual(runtime.events.summary()["current"]["vision_state"], "unknown")


if __name__ == "__main__":
    unittest.main()
